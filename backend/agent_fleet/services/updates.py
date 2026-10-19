"""Version currency: is the installed agent the latest available release?

Package names are NOT guessed - each mapping below was verified by reading the
package.json of a locally installed copy, or by checking the project's own
release feed against a local install. Agents with no verified feed report
"unknown" rather than a fabricated answer.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request

NPM_PACKAGES = {
    "codex": "@openai/codex",
    "claude": "@anthropic-ai/claude-code",
    "opencode": "opencode-ai",
    "pi": "@earendil-works/pi-coding-agent",
    "codebuddy": "@tencent-ai/codebuddy-code",
    "qoder": "@qoder-ai/qodercli",
}

# Agents distributed outside npm. The repo is the project's own release feed.
# Verified 2026-10-18: agy --version -> 1.4.2, latest tag 1.4.2;
# hermes --version -> v0.22.2 (d923a5eb), latest tag v0.22.2.
GITHUB_REPOS = {
    "agy": "google-antigravity/antigravity-cli",
    "hermes": "NousResearch/hermes-agent",
}

CACHE_TTL = 3600
_cache: dict = {}


def _semver(value):
    """Leading numeric version tuple, e.g. "0.163.0-beta" -> (0, 163, 0)."""
    core = ""
    for ch in (value or "").strip():
        if ch.isdigit() or ch == ".":
            core += ch
        else:
            break
    parts = [p for p in core.split(".") if p != ""]
    try:
        return tuple(int(p) for p in parts) or None
    except ValueError:
        return None


def compare_versions(installed: str, latest: str):
    """-1 installed is older, 0 equal, 1 installed is newer, None unknown."""
    a, b = _semver(installed), _semver(latest)
    if a is None or b is None:
        return None
    length = max(len(a), len(b))
    a = a + (0,) * (length - len(a))
    b = b + (0,) * (length - len(b))
    return (a > b) - (a < b)


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


def _npm_latest(package: str) -> dict:
    data = _fetch_json(f"https://registry.npmjs.org/{package}/latest")
    return {
        "source": "npm",
        "package": package,
        "latest_available": data.get("version"),
        "release_url": f"https://www.npmjs.com/package/{package}",
    }


def _github_latest(repo: str) -> dict:
    data = _fetch_json(f"https://api.github.com/repos/{repo}/releases/latest")
    tag = (data.get("tag_name") or "").strip()
    return {
        "source": "github",
        "package": repo,
        "latest_available": tag[1:] if tag[:1] in ("v", "V") else tag or None,
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
            result = _npm_latest(NPM_PACKAGES[agent_id])
        elif agent_id in GITHUB_REPOS:
            result = _github_latest(GITHUB_REPOS[agent_id])
        else:
            result = {
                "source": "unknown",
                "package": None,
                "latest_available": None,
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
            "release_url": None,
            "error": str(exc)[:200],
        }
    _cache[agent_id] = (now, result)
    return result


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


def invalidate(agent_id: str | None = None):
    if agent_id:
        _cache.pop(agent_id, None)
    else:
        _cache.clear()
