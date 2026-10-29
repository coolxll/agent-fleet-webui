"""Application version and build metadata discovery service.

Provides unified version, commit SHA, build timestamp, and release status
following strict discovery precedence and security constraints:
1. version.json (in package directory or project root)
2. Environment variables: AGENT_FLEET_VERSION, AGENT_FLEET_COMMIT_SHA, AGENT_FLEET_BUILD_TIME
3. Local git repository (HEAD commit SHA if .git is accessible)
4. Safe fallback: 0.2.0-dev / dev

Never leaks filesystem paths, usernames, or sensitive configuration.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TypedDict

BASE_VERSION = "0.2.0"

_COMMIT_HEX_PATTERN = re.compile(r"^[0-9a-fA-F]{7,40}$")


class VersionInfo(TypedDict):
    version: str
    commit: str
    commit_short: str
    build_time: str | None
    is_release: bool


def _clean_str(val: Any) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def _clean_commit(commit: str | None) -> tuple[str, str]:
    """Return (commit, commit_short) ensuring clean representation."""
    if not commit or commit.lower() == "dev":
        return "dev", "dev"
    c = commit.strip()
    if _COMMIT_HEX_PATTERN.match(c):
        c_clean = c.lower()
        return c_clean, c_clean[:7]
    # If not a standard hex sha (e.g. custom label or tag), truncate cleanly without path separators
    safe_c = re.sub(r"[^\w.-]", "", c)
    return safe_c or "dev", (safe_c[:7] if safe_c else "dev")


def _get_git_commit(repo_dir: Path | None = None) -> str | None:
    """Retrieve full git commit SHA if repository is available."""
    if not shutil.which("git"):
        return None

    if repo_dir is None:
        current = Path(__file__).resolve().parent
        for candidate in [current, *current.parents]:
            if (candidate / ".git").exists():
                repo_dir = candidate
                break
        if repo_dir is None:
            return None
    else:
        if not (repo_dir / ".git").exists():
            return None

    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
        sha = res.stdout.strip()
        if _COMMIT_HEX_PATTERN.match(sha):
            return sha.lower()
    except Exception:
        return None
    return None


def _find_version_file(specified_path: Path | None = None) -> Path | None:
    if specified_path is not None:
        return specified_path if specified_path.is_file() else None

    # Search candidate locations
    here = Path(__file__).resolve().parent
    candidates = [
        here / "version.json",
        here.parent / "version.json",
        here.parents[1] / "version.json",
    ]
    for p in candidates:
        try:
            if p.is_file():
                return p
        except OSError:
            continue
    return None


def get_version_info(
    version_file: Path | None = None,
    env: Mapping[str, str] | None = None,
    repo_dir: Path | None = None,
) -> VersionInfo:
    """Discover version and build metadata with strict source precedence.

    Precedence:
    a. version.json (file)
    b. Environment variables (AGENT_FLEET_VERSION, etc.)
    c. Local git repository (git rev-parse HEAD)
    d. Fallback: 0.2.0-dev / dev
    """
    # Source a: version.json
    v_file = _find_version_file(version_file)
    if v_file:
        try:
            with open(v_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                ver = _clean_str(data.get("version")) or BASE_VERSION
                raw_commit = _clean_str(data.get("commit"))
                commit, commit_short = _clean_commit(raw_commit)
                if "commit_short" in data and data["commit_short"]:
                    commit_short = str(data["commit_short"]).strip()[:7]
                build_time = _clean_str(data.get("build_time"))
                is_release_val = data.get("is_release")
                if is_release_val is not None:
                    is_release = bool(is_release_val)
                else:
                    is_release = not ver.endswith("-dev")
                return {
                    "version": ver,
                    "commit": commit,
                    "commit_short": commit_short,
                    "build_time": build_time,
                    "is_release": is_release,
                }
        except Exception:
            pass

    # Source b: Environment variables
    env_map = os.environ if env is None else env
    env_ver = _clean_str(env_map.get("AGENT_FLEET_VERSION"))
    env_commit = _clean_str(env_map.get("AGENT_FLEET_COMMIT_SHA"))
    env_build_time = _clean_str(env_map.get("AGENT_FLEET_BUILD_TIME"))
    env_is_release_raw = _clean_str(env_map.get("AGENT_FLEET_IS_RELEASE"))

    if env_ver or env_commit or env_build_time or env_is_release_raw:
        if env_is_release_raw is not None:
            is_release = env_is_release_raw.lower() in ("true", "1", "yes", "on", "release")
        elif env_ver:
            is_release = not env_ver.endswith("-dev")
        else:
            is_release = False

        ver = env_ver or (BASE_VERSION if is_release else f"{BASE_VERSION}-dev")
        commit, commit_short = _clean_commit(env_commit)
        if commit == "dev":
            # If commit was not in env, check git
            git_sha = _get_git_commit(repo_dir)
            if git_sha:
                commit, commit_short = _clean_commit(git_sha)

        return {
            "version": ver,
            "commit": commit,
            "commit_short": commit_short,
            "build_time": env_build_time,
            "is_release": is_release,
        }

    # Source c: Local git repository
    git_sha = _get_git_commit(repo_dir)
    if git_sha:
        commit, commit_short = _clean_commit(git_sha)
        return {
            "version": f"{BASE_VERSION}-dev",
            "commit": commit,
            "commit_short": commit_short,
            "build_time": None,
            "is_release": False,
        }

    # Source d: Fallback
    return {
        "version": f"{BASE_VERSION}-dev",
        "commit": "dev",
        "commit_short": "dev",
        "build_time": None,
        "is_release": False,
    }
