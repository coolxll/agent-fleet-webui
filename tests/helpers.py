"""Build RemoteFacts straight from fixture files; no machine required."""

from __future__ import annotations

from pathlib import Path

from agent_fleet.remotes.probe import FileFact, RemoteFacts

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def fixture_text(*parts: str) -> str:
    return (FIXTURES.joinpath(*parts)).read_text(encoding="utf-8")


def make_facts(
    files: dict | None = None,
    which: dict | None = None,
    versions: dict | None = None,
    os_name: str = "Linux",
    home: str = "/home/fixture",
    user: str = "fixture",
    probed: set | None = None,
    skills: dict | None = None,
    dirs: dict | None = None,
) -> RemoteFacts:
    """Compose a RemoteFacts object the same way a probe run would."""
    facts = RemoteFacts(os=os_name, home=home, user=user)
    facts.probed = set(probed or [])
    facts.which = dict(which or {})
    for key, (rc, out) in (versions or {}).items():
        facts.version_rc[key] = rc
        facts.version_out[key] = out
    for path, spec in (files or {}).items():
        if spec is None:
            facts.files[path] = FileFact(path=path, status="missing", size=0)
            continue
        if isinstance(spec, str):
            status, content = "ok", spec
        else:
            status, content = spec
        size = len(content.encode("utf-8")) if content is not None else 0
        facts.files[path] = FileFact(
            path=path,
            status=status,
            size=size if status == "ok" else 0,
            content=content if status == "ok" else None,
        )
    facts.skills = dict(skills or {})
    facts.dirs = dict(dirs or {})
    for path, entries in (skills or {}).items():
        facts.skills[path] = entries
    return facts
