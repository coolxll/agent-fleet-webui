"""PowerShell probe for Windows runtimes.

Emits the same AF-framed record stream as the POSIX probe so adapters work
unchanged. Two Windows constraints shape the generator:
  - The remote command line is silently truncated around 8k characters, so
    the script is compact (data-driven loops) and split into safe chunks.
  - File content travels as base64, which is byte-exact and immune to CRLF.
"""

from __future__ import annotations

NL = chr(10)
BS = chr(92)
SLASH = chr(47)
SQ = chr(39)
CR = "[char]13"
LF = "[char]10"

# Keep each encoded command comfortably below the ~8k remote limit.
MAX_CHUNK_CHARS = 2600


def _ps_q(s: str) -> str:
    """Single-quote a literal for PowerShell."""
    return SQ + s.replace(SQ, SQ + SQ) + SQ


def _ps_list(items) -> str:
    return "@(" + ",".join(_ps_q(i) for i in items) + ")"


HEADER = [
    "# agent-fleet read-only probe (Windows)",
    "$ErrorActionPreference = 'SilentlyContinue'",
    "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
    "$T = [char]9",
    "$B = " + _ps_q(BS),
    "$H = $HOME",
    'function r2($a,$b) { "AF" + $T + $a + $T + $b }',
    'function r3($a,$b,$c) { "AF" + $T + $a + $T + $b + $T + $c }',
    'function r4($a,$b,$c,$d) { "AF" + $T + $a + $T + $b + $T + $c + $T + $d }',
]


def build_windows_probe_script(spec) -> list:
    """Return a list of independently runnable PowerShell scripts."""
    from agent_fleet.remotes.probe import MAX_DIR_ENTRIES, MAX_FILE_BYTES, _safe_path

    blocks: list = []

    blocks.append(
        [
            'r2 os "Windows"',
            "r2 arch $env:PROCESSOR_ARCHITECTURE",
            "r2 home $H",
            "r2 user $env:USERNAME",
            "r2 osrelease ((Get-CimInstance Win32_OperatingSystem).Caption)",
            'r2 haswsl $(if (Get-Command wsl.exe) { "yes" } else { "no" })',
        ]
    )

    entries = []
    for b in spec.bins:
        cands = ";".join(c for c in b.candidates if c.startswith("~"))
        entries.append(b.key + "|" + b.binary + ("|" + cands if cands else ""))
    version_args = " ".join(b.version_args)
    blocks.append(
        [
            "foreach ($e in " + _ps_list(entries) + ") {",
            "  $f = $e.Split('|')",
            "  r2 probed $f[0] 1",
            "  $_p = (Get-Command $f[1]).Source",
            "  if (-not $_p -and $f.Count -gt 2) {",
            "    foreach ($c in $f[2].Split(';')) {",
            "      $cp = ($H + $c.Substring(1).Replace('/', $B))",
            "      if (Test-Path -LiteralPath $cp) { $_p = $cp; break }",
            "    }",
            "  }",
            "  if ($_p) { r3 which $f[0] $_p }",
            "  $_o = ''; $_r = 127",
            "  if ($_p) { $_o = (& $_p " + version_args + " 2>&1 | Out-String); $_r = $LASTEXITCODE }",
            "  $_o = (($_o -split " + LF + ") -join ' ')",
            "  $_o = (($_o -split " + CR + ") -join ' ')",
            "  $_o = $_o.Trim()",
            "  if ($_o.Length -gt 300) { $_o = $_o.Substring(0,300) }",
            "  r4 version $f[0] $_r $_o",
            "}",
        ]
    )

    files = [p for p in spec.files if _safe_path(p)]
    if files:
        blocks.append(
            [
                "foreach ($k in " + _ps_list(files) + ") {",
                "  $p = ($H + $k.Substring(1).Replace('/', $B))",
                "  if (Test-Path -LiteralPath $p -PathType Leaf) {",
                "    $l = (Get-Item -LiteralPath $p).Length",
                "    if ($l -gt " + str(MAX_FILE_BYTES) + ") { r4 file $k $l toolarge }",
                "    else {",
                "      r4 file $k $l b64",
                "      $z = [Convert]::ToBase64String([IO.File]::ReadAllBytes($p))",
                "      if ($z.Length -gt 0) { $z }",
                "      r2 endfile $k",
                "    }",
                "  } elseif (Test-Path -LiteralPath $p -PathType Container) { r4 file $k 0 isdir }",
                "  else { r4 file $k 0 missing }",
                "}",
            ]
        )

    dirs = [p for p in spec.dirs if _safe_path(p)]
    if dirs:
        blocks.append(
            [
                "foreach ($k in " + _ps_list(dirs) + ") {",
                "  $p = ($H + $k.Substring(1).Replace('/', $B))",
                "  if (Test-Path -LiteralPath $p -PathType Container) {",
                "    r3 dir $k dir",
                "    $n = 0",
                "    foreach ($e in (Get-ChildItem -LiteralPath $p)) {",
                "      $n = $n + 1",
                "      if ($n -le " + str(MAX_DIR_ENTRIES) + ") {",
                "        if ($e.PSIsContainer) { r4 ls $k $e.Name d }",
                "        else { r4 ls $k $e.Name f }",
                "      }",
                "    }",
                "  } else { r3 dir $k missing }",
                "}",
            ]
        )

    skills = [p for p in spec.skill_dirs if _safe_path(p)]
    if skills:
        blocks.append(
            [
                "foreach ($k in " + _ps_list(skills) + ") {",
                "  $p = ($H + $k.Substring(1).Replace('/', $B))",
                "  if (Test-Path -LiteralPath $p -PathType Container) {",
                "    r3 skilldir $k dir",
                "    foreach ($s in (Get-ChildItem -LiteralPath $p -Directory)) {",
                "      $sf = Join-Path $s.FullName 'SKILL.md'",
                "      if (Test-Path -LiteralPath $sf) { r4 skill $k $s.Name $sf }",
                "      else { r4 skill $k $s.Name '' }",
                "    }",
                "    foreach ($s in (Get-ChildItem -LiteralPath $p -Filter *.md -File)) {",
                "      r4 skill $k $s.Name $s.FullName",
                "    }",
                "  } else { r3 skilldir $k missing }",
                "}",
            ]
        )

    blocks.append(["r2 done 1"])
    return _chunks(blocks)


def _chunks(blocks) -> list:
    """Group whole blocks into chunks that each stay under the size limit."""
    head = NL.join(HEADER)
    parts, current, size = [], [], 0
    for block in blocks:
        text = NL.join(block)
        if size + len(text) > MAX_CHUNK_CHARS and current:
            parts.append(head + NL + NL.join(current) + NL)
            current, size = [], 0
        current.append(text)
        size += len(text)
    parts.append(head + NL + NL.join(current) + NL)
    return parts
