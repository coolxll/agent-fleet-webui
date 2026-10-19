"""Version currency: is the installed agent the latest available release?

Package names are NOT guessed - each mapping below was verified by reading the
package.json of a locally installed copy. Agents that are not distributed
through npm (agy is a Go binary, hermes uses its own installer) report
"unknown" rather than a fabricated answer.
"""

from __future__ import annotations

import json
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


def latest_available(agent_id: str) -> dict:
    """Latest published version for an agent, cached for an hour."""
    now = time.time()
    cached = _cache.get(agent_id)
    if cached and now - cached[0] < CACHE_TTL:
        return cached[1]
    package = NPM_PACKAGES.get(agent_id)
    if not package:
        result = {"source": "unknown", "package": None, "latest_available": None}
        _cache[agent_id] = (now, result)
        return result
    try:
        req = urllib.request.Request(
            f"https://registry.npmjs.org/{package}/latest",
            headers={"Accept": "application/json", "User-Agent": "agent-fleet"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        result = {
            "source": "npm",
            "package": package,
            "latest_available": data.get("version"),
        }
    except Exception as exc:
        result = {
            "source": "unknown",
            "package": package,
            "latest_available": None,
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
