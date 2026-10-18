"""PowerShell probe generation.

No PowerShell here: the tests pin the properties of the generated script that
broke on real Windows hosts or would push a chunk past the remote limit.
"""

from __future__ import annotations

import base64

from agent_fleet.adapters.registry import merged_probe_spec
from agent_fleet.remotes.probe import BinProbe, ProbeSpec
from agent_fleet.remotes.probe_win import (
    MAX_CHUNK_CHARS,
    build_windows_probe_script,
)


def _encoded_len(script: str) -> int:
    return len(base64.b64encode(script.encode("utf-16-le")).decode("ascii"))


def test_version_args_are_not_quoted_inside_the_entry():
    """`& $exe $f[3]` passes the field literally, so quotes would become part
    of the argument and every version command would fail."""
    spec = ProbeSpec(
        bins=[BinProbe(key="codex:codex", binary="codex", version_args=["--version"])]
    )
    script = "\n".join(build_windows_probe_script(spec))
    assert "|--version'" in script or "|--version\"" in script
    assert "''--version''" not in script
    assert "& $_p $_a" in script


def test_multi_arg_version_commands_are_split_at_run_time():
    spec = ProbeSpec(
        bins=[BinProbe(key="a:a", binary="a", version_args=["--version", "--short"])]
    )
    script = "\n".join(build_windows_probe_script(spec))
    assert "|--version;--short'" in script
    assert "$f[3].Split(';')" in script


def test_drive_absolute_project_paths_are_resolved_not_prefixed_with_home():
    spec = ProbeSpec(files=["C:/Users/me/repo/.claude/settings.json"])
    script = "\n".join(build_windows_probe_script(spec, header=False))
    assert "function rp($k)" in script
    assert "$p = (rp $k)" in script
    # Home-relative paths are the only ones resolved against $HOME.
    assert "if ($k.StartsWith('~/'))" in script
    assert "if ($k -match '^[A-Za-z]:')" in script


def test_every_chunk_stays_under_the_limit_including_the_header():
    for spec in (merged_probe_spec(), ProbeSpec(files=["~/a.json"] * 30)):
        scripts = build_windows_probe_script(spec)
        assert len(scripts) >= 1
        for script in scripts:
            assert len(script) <= MAX_CHUNK_CHARS
            assert _encoded_len(script) < 8000


def test_long_project_file_lists_are_split_into_several_chunks():
    paths = []
    for r in range(5):
        root = "C:/Users/somebody/workspace/a-rather-long-project-name/repo-" + str(r)
        for rel in (
            ".claude/settings.json",
            ".claude/settings.local.json",
            ".mcp.json",
            "opencode.json",
            ".opencode/opencode.json",
            "AGENTS.md",
        ):
            paths.append(root + "/" + rel)
    scripts = build_windows_probe_script(ProbeSpec(files=paths[:30]))
    assert len(scripts) > 1
    for script in scripts:
        assert len(script) <= MAX_CHUNK_CHARS
    # every requested path still appears exactly once across the chunks
    for path in paths[:30]:
        assert sum(script.count(path) for script in scripts) == 1


def test_generated_probe_is_read_only():
    script = "\n".join(build_windows_probe_script(merged_probe_spec()))
    for forbidden in ("Remove-Item", "Set-Content", "Out-File", "New-Item", "Invoke-WebRequest"):
        assert forbidden not in script
