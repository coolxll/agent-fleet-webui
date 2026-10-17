"""Probe script generation and record parsing (no remote involved)."""

from __future__ import annotations

from agent_fleet.remotes.probe import (
    BinProbe,
    ProbeSpec,
    build_probe_script,
    parse_probe_output,
)

TAB = chr(9)
NL = chr(10)


FILE_CONTENT = 'model = "x"' + NL + 'approval_policy = "never"'


def _sample_stream() -> str:
    return NL.join(
        [
            "AF" + TAB + "os" + TAB + "Linux",
            "AF" + TAB + "arch" + TAB + "x86_64",
            "AF" + TAB + "home" + TAB + "/home/fixture",
            "AF" + TAB + "user" + TAB + "fixture",
            "AF" + TAB + "probed" + TAB + "codex:codex",
            "AF" + TAB + "which" + TAB + "codex:codex" + TAB + "/usr/bin/codex",
            "AF" + TAB + "version" + TAB + "codex:codex" + TAB + "0" + TAB + "codex-cli 1.2.3",
            "AF" + TAB + "file" + TAB + "~/.codex/config.toml" + TAB + str(len(FILE_CONTENT)) + TAB + "present",
            'model = "x"',
            "approval_policy = " + chr(34) + "never" + chr(34),
            "AF" + TAB + "endfile" + TAB + "~/.codex/config.toml",
            "AF" + TAB + "file" + TAB + "~/.missing.toml" + TAB + "0" + TAB + "missing",
            "AF" + TAB + "dir" + TAB + "~/.claude/skills" + TAB + "dir",
            "AF" + TAB + "ls" + TAB + "~/.claude/skills" + TAB + "one" + TAB + "d",
            "AF" + TAB + "skill" + TAB + "~/.claude/skills" + TAB + "one" + TAB + "x/SKILL.md",
            "AF" + TAB + "done" + TAB + "1",
        ]
    )


def test_parse_roundtrip():
    facts = parse_probe_output(_sample_stream())
    assert facts.os == "Linux"
    assert facts.arch == "x86_64"
    assert facts.home == "/home/fixture"
    assert facts.user == "fixture"
    assert facts.bin_path("codex:codex") == "/usr/bin/codex"
    assert facts.version("codex:codex") == (0, "codex-cli 1.2.3")
    assert facts.file("~/.codex/config.toml").status == "ok"
    assert facts.file("~/.codex/config.toml").content == FILE_CONTENT
    assert facts.file("~/.missing.toml").status == "missing"
    assert facts.file("~/.nope").status == "unknown"
    assert facts.dirs["~/.claude/skills"] == "dir"
    assert facts.ls["~/.claude/skills"] == [("one", "d")]
    assert facts.skills["~/.claude/skills"][0]["name"] == "one"
    assert facts.probed == {"codex:codex"}
    assert facts.truncated is False


def test_truncated_stream_marks_file_partial():
    stream = NL.join(
        [
            "AF" + TAB + "file" + TAB + "~/.codex/config.toml" + TAB + "10" + TAB + "present",
            "model = partial",
        ]
    )
    facts = parse_probe_output(stream)
    assert facts.truncated is True
    assert facts.file("~/.codex/config.toml").status == "truncated"


def test_empty_file_is_empty_not_unreadable():
    stream = NL.join(
        [
            "AF" + TAB + "file" + TAB + "~/.x" + TAB + "0" + TAB + "present",
            "AF" + TAB + "endfile" + TAB + "~/.x",
        ]
    )
    facts = parse_probe_output(stream)
    assert facts.file("~/.x").status == "empty"


def test_generated_script_is_read_only_and_quoted():
    spec = ProbeSpec(
        bins=[BinProbe(key="codex:codex", binary="codex", candidates=["~/.codex/bin/codex"])],
        files=["~/.codex/config.toml"],
        skill_dirs=["~/.codex/skills"],
    )
    script = build_probe_script(spec)
    assert "command -v -- 'codex'" in script
    assert '"$HOME"/' in script
    assert "rm " not in script
    assert "tee " not in script
    assert "curl " not in script
    # no redirection to files: only stderr->stdout and stderr to /dev/null
    stripped = script.replace("2>&1", "").replace(">/dev/null", "")
    assert ">" not in stripped

    # secret files can never be requested
    unsafe = ProbeSpec(files=["~/.ssh/id_rsa", "~/.aws/credentials"])
    unsafe_script = build_probe_script(unsafe)
    assert "id_rsa" not in unsafe_script
    assert "aws/credentials" not in unsafe_script


def test_generated_script_captures_real_exit_code():
    spec = ProbeSpec(bins=[BinProbe(key="x:x", binary="nosuchbinary-fixture")])
    script = build_probe_script(spec)
    # the exit code must be captured before the output is piped anywhere
    assert "2>&1); _r2=$?;" in script
