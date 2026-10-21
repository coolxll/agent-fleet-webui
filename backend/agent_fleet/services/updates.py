"""Version currency: is the installed agent the latest available release?

Package names are NOT guessed - each mapping below was verified by reading the
package.json of a locally installed copy, or by checking the project's own
release feed against a local install. Agents with no verified feed report
"unknown" rather than a fabricated answer.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request

NPM_PACKAGES = {
    "codex": "@openai/codex",
    "claude": "@anthropic-ai/claude-code",
    "opencode": "opencode-ai",
    "pi": "@earendil-works/pi-coding-agent",
    "codebuddy": "@tencent-ai/codebuddy-code",
    "qoder": "@qoder-ai/qodercli",
    "paseo": "@getpaseo/cli",
}

# Agents and supervisors distributed outside npm. The repo is the project's own release feed.
# Verified 2026-10-18: agy --version -> 1.4.2, latest tag 1.4.2;
# hermes --version -> v0.22.2 (d923a5eb), latest tag v0.22.2.
# herdr -> herdrdev/herdr
GITHUB_REPOS = {
    "agy": "google-antigravity/antigravity-cli",
    "hermes": "NousResearch/hermes-agent",
    "herdr": "herdrdev/herdr",
}

# Prerelease tags consulted only when the local install is already ahead of
# `latest`, i.e. the user deliberately rides that channel. Tag names differ
# per project (codex uses alpha/beta/native, gemini nightly/preview), so the
# list is intentionally short and only contains tags observed in the wild.
NPM_PRERELEASE_TAGS = {
    "claude": ("next",),
    "codex": ("alpha", "beta"),
}

CACHE_TTL = 3600
_cache: dict = {}

# A leading dotted numeric run, optionally with a prerelease suffix. Build
# metadata and trailing prose ("0.21.5 (v2026.9.24)") are ignored.
_VERSION_RE = re.compile(r"^v?(\d+(?:\.\d+)*)(?:-([0-9A-Za-z.-]+))?")
# Used when searching inside a free-form release name.
_EMBEDDED_VERSION_RE = re.compile(r"v?(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)")


def _parse_version(value):
    """-> (core tuple, prerelease identifiers) or None.

    Core is the dotted numeric run; prerelease follows semver: "2.1.303-beta.1"
    is LOWER than "2.1.303".
    """
    if not isinstance(value, str):
        return None
    match = _VERSION_RE.match(value.strip())
    if not match:
        return None
    core = tuple(int(p) for p in match.group(1).split("."))
    pre = [p for p in (match.group(2) or "").split(".") if p]
    return core, pre


def _compare_parsed(a, b) -> int:
    core_a, pre_a = a
    core_b, pre_b = b
    length = max(len(core_a), len(core_b))
    core_a = core_a + (0,) * (length - len(core_a))
    core_b = core_b + (0,) * (length - len(core_b))
    if core_a != core_b:
        return -1 if core_a < core_b else 1
    if not pre_a and not pre_b:
        return 0
    # A release outranks any prerelease of the same core.
    if not pre_a:
        return 1
    if not pre_b:
        return -1
    for x, y in zip(pre_a, pre_b):
        x_num, y_num = x.isdigit(), y.isdigit()
        if x_num and y_num:
            if int(x) != int(y):
                return -1 if int(x) < int(y) else 1
        elif x_num != y_num:
            # numeric identifiers have lower precedence than alphanumeric
            return -1 if x_num else 1
        elif x != y:
            return -1 if x < y else 1
    if len(pre_a) == len(pre_b):
        return 0
    return -1 if len(pre_a) < len(pre_b) else 1


def compare_versions(installed: str, latest: str):
    """-1 installed is older, 0 equal, 1 installed is newer, None unknown."""
    a, b = _parse_version(installed), _parse_version(latest)
    if a is None or b is None:
        return None
    return _compare_parsed(a, b)


def _fetch_json(url: str):
    headers = {
        "Accept": "application/json",
        "User-Agent": "agent-fleet",
    }
    # Optional: lifts the unauthenticated GitHub rate limit (60 req/h/IP).
    token = os.environ.get("AGENT_FLEET_GITHUB_TOKEN")
    if token and "api.github.com" in url:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _npm_dist_tags(package: str) -> dict:
    """dist-tags endpoint: a few hundred bytes, unlike the full packument.

    `/{package}/latest` is small for most packages but the registry may have
    to resolve the whole packument for large ones; `/-/package/<pkg>/dist-tags`
    answers from tag metadata alone and carries every channel at once.
    """
    encoded = package.replace("/", "%2f")
    data = _fetch_json(f"https://registry.npmjs.org/-/package/{encoded}/dist-tags")
    return data if isinstance(data, dict) else {}


def _npm_latest(package: str, agent_id: str) -> dict:
    tags = _npm_dist_tags(package)
    prerelease = {}
    for tag in NPM_PRERELEASE_TAGS.get(agent_id, ()):
        value = tags.get(tag)
        if isinstance(value, str) and _parse_version(value):
            prerelease[tag] = value
    return {
        "source": "npm",
        "package": package,
        "latest_available": tags.get("latest"),
        "stable_available": tags.get("stable"),
        "dist_tags": tags,
        "prerelease": prerelease,
        "release_url": f"https://www.npmjs.com/package/{package}",
    }


def _display_version(text):
    """First semantic version in `text`, rejecting calendar-style numbers.

    Hermes tags historically mix a semantic version with a date
    ("Hermes Agent v0.21.5 (v2026.9.24)"). A date parses as a valid version
    tuple (2026 > 0), which would report "update available" forever, so any
    candidate whose major is year-sized is discarded.
    """
    if not isinstance(text, str):
        return None
    for candidate in _EMBEDDED_VERSION_RE.findall(text):
        parsed = _parse_version(candidate)
        if parsed and parsed[0][0] < 1000:
            return candidate
    return None


def _github_latest(repo: str) -> dict:
    data = _fetch_json(f"https://api.github.com/repos/{repo}/releases/latest")
    # Release `name` is preferred: the tag may be a date while the name still
    # carries the semantic version.
    version = _display_version(data.get("name"))
    if version is None:
        tag = (data.get("tag_name") or "").strip()
        version = _display_version(tag) or None
    return {
        "source": "github",
        "package": repo,
        "latest_available": version,
        "prerelease": {},
        "release_url": f"https://github.com/{repo}/releases",
    }


def latest_available(agent_id: str) -> dict:
    """Latest published version for an agent, cached for an hour."""
    now = time.time()
    cached = _cache.get(agent_id)
    if cached and now - cached[0] < CACHE_TTL:
        return cached[1]
    try:
        if agent_id in NPM_PACKAGES:
            result = _npm_latest(NPM_PACKAGES[agent_id], agent_id)
        elif agent_id in GITHUB_REPOS:
            result = _github_latest(GITHUB_REPOS[agent_id])
        else:
            result = {
                "source": "unknown",
                "package": None,
                "latest_available": None,
                "prerelease": {},
                "release_url": None,
            }
    except Exception as exc:
        package = NPM_PACKAGES.get(agent_id) or GITHUB_REPOS.get(agent_id)
        result = {
            # Keep the intended source: the feed exists, it just failed.
            "source": "npm" if agent_id in NPM_PACKAGES else (
                "github" if agent_id in GITHUB_REPOS else "unknown"
            ),
            "package": package,
            "latest_available": None,
            "prerelease": {},
            "release_url": None,
            "error": str(exc)[:200],
        }
    _cache[agent_id] = (now, result)
    return result


def effective_latest(installed: str, info: dict, channel: str | None = None):
    """The version this install should be compared against.

    If `channel` is "stable", compares against the stable release target.
    For Claude Code, 2.1.280 is the designated milestone stable build
    opted-in by stable channel users; if an install is on 2.1.280 or newer on
    the stable track, it is recognized as current on the stable tier rather
    than flagged behind.
    Otherwise, compares against `latest_available`. Prerelease tags are
    considered only when the install is strictly ahead of `latest`.
    """
    if not info or not installed:
        return (info or {}).get("latest_available")

    if channel == "stable":
        pkg = (info or {}).get("package")
        if pkg in ("@anthropic-ai/claude-code", "@cometix/claude-code"):
            if compare_versions(installed, "2.1.280") >= 0:
                return installed
        stable = (info or {}).get("stable_available")
        if stable:
            return stable
        return (info or {}).get("latest_available")

    latest = (info or {}).get("latest_available")
    if not latest:
        return latest
    if compare_versions(installed, latest) != 1:
        return latest
    best = latest
    for value in ((info or {}).get("prerelease") or {}).values():
        if compare_versions(value, best) == 1:
            best = value
    return best


def version_state(installed: str, latest: str) -> str:
    if not latest:
        return "unknown"
    if not installed:
        return "unknown"
    cmp = compare_versions(installed, latest)
    if cmp is None:
        return "unknown"
    if cmp < 0:
        return "behind"
    if cmp > 0:
        return "ahead"
    return "latest"


def version_state_for(installed: str, info: dict, channel: str | None = None) -> str:
    """`version_state` against the channel-aware effective latest."""
    return version_state(installed, effective_latest(installed, info, channel=channel))


def invalidate(agent_id: str | None = None):
    if agent_id:
        _cache.pop(agent_id, None)
    else:
        _cache.clear()
