"""Remote fact collection.

A single read-only script runs per runtime and streams back a framed record
stream. The parsed result (RemoteFacts) is what adapters consume, which keeps
adapters pure and trivially testable from fixtures.
"""

from __future__ import annotations

from dataclasses import dataclass, field

NL = chr(10)
BS = chr(92)
ESC_N = BS + "n"
ESC_T = BS + "t"
SQ = chr(39)
FLATTEN = "_o=$(echo $_o | tr " + SQ + ESC_N + SQ + " " + SQ + " " + SQ + " | head -c 300); "

MAX_FILE_BYTES = 512_000
MAX_DIR_ENTRIES = 400

# Never touch these, even if an adapter were to ask for them.
FORBIDDEN_PATH_MARKERS = (
    ".ssh/",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    "id_dsa",
    ".gnupg/",
    "keychain",
    "/etc/shadow",
    ".npmrc",
    ".netrc",
    ".aws/credentials",
    ".docker/config.json",
    ".kube/config",
)


def _safe_path(path: str) -> bool:
    low = path.lower()
    return not any(m in low for m in FORBIDDEN_PATH_MARKERS)


@dataclass
class BinProbe:
    key: str  # "<agent_id>:<binary>"
    binary: str
    version_args: list = field(default_factory=lambda: ["--version"])
    candidates: list = field(default_factory=list)


@dataclass
class ProbeSpec:
    bins: list = field(default_factory=list)
    files: list = field(default_factory=list)
    dirs: list = field(default_factory=list)
    skill_dirs: list = field(default_factory=list)

    def merge(self, other: "ProbeSpec") -> "ProbeSpec":
        return ProbeSpec(
            bins=self.bins + other.bins,
            files=_uniq(self.files + other.files),
            dirs=_uniq(self.dirs + other.dirs),
            skill_dirs=_uniq(self.skill_dirs + other.skill_dirs),
        )


def _uniq(items: list) -> list:
    seen, out = set(), []
    for i in items:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def _q(s: str) -> str:
    """Shell-quote by wrapping in single quotes."""
    return "'" + s.replace("'", "'" + BS + "''") + "'"


def _pathq(path: str) -> str:
    """Shell-quote a path while keeping $HOME expandable."""
    if path.startswith("~/"):
        return '"$HOME"/' + _q(path[2:])
    return _q(path)


PATH_BOOTSTRAP = (
    NL
    + 'for _c in "$HOME/.local/bin" "$HOME/.bun/bin" "$HOME/.cargo/bin" "$HOME/.deno/bin" '
    + BS + NL
    + '          "$HOME/.volta/bin" "$HOME/.npm-global/bin" "$HOME/.yarn/bin" '
    + BS + NL
    + '          "/usr/local/bin" "/usr/local/sbin" "/opt/homebrew/bin" "/snap/bin"; do'
    + NL
    + '  case ":$PATH:" in'
    + NL
    + '    *":$_c:"*) ;;'
    + NL
    + '    *) [ -d "$_c" ] && PATH="$_c:$PATH" ;;'
    + NL
    + "  esac"
    + NL
    + "done"
    + NL
    + 'if [ -d "$HOME/.nvm/versions/node" ]; then'
    + NL
    + '  for _v in $(ls -1 "$HOME/.nvm/versions/node" 2>/dev/null | sort -r); do'
    + NL
    + '    if [ -d "$HOME/.nvm/versions/node/$_v/bin" ]; then'
    + NL
    + '      PATH="$HOME/.nvm/versions/node/$_v/bin:$PATH"'
    + NL
    + "      break"
    + NL
    + "    fi"
    + NL
    + "  done"
    + NL
    + "fi"
    + NL
    + 'if [ -d "$HOME/.local/share/fnm" ]; then'
    + NL
    + '  for _v in $(ls -1 "$HOME/.local/share/fnm" 2>/dev/null | sort -r); do'
    + NL
    + '    if [ -d "$HOME/.local/share/fnm/$_v/bin" ]; then'
    + NL
    + '      PATH="$HOME/.local/share/fnm/$_v/bin:$PATH"'
    + NL
    + "      break"
    + NL
    + "    fi"
    + NL
    + "  done"
    + NL
    + "fi"
    + NL
    + "export PATH"
    + NL
)


def build_probe_script(spec: ProbeSpec) -> str:
    """Render the POSIX probe script. Only whitelisted paths are touched."""
    files = [p for p in spec.files if _safe_path(p)]
    dirs = [p for p in spec.dirs if _safe_path(p)]
    skill_dirs = [p for p in spec.skill_dirs if _safe_path(p)]

    lines = [
        "# agent-fleet read-only probe",
        'TAB=$(printf "' + ESC_T + '")',
        'rec2() { printf "%s' + ESC_N + '" "AF$TAB$1$TAB$2"; }',
        'rec3() { printf "%s' + ESC_N + '" "AF$TAB$1$TAB$2$TAB$3"; }',
        'rec4() { printf "%s' + ESC_N + '" "AF$TAB$1$TAB$2$TAB$3$TAB$4"; }',
        PATH_BOOTSTRAP,
        'rec2 os "$(uname -s 2>/dev/null)"',
        'rec2 arch "$(uname -m 2>/dev/null)"',
        'rec2 kernel "$(uname -r 2>/dev/null)"',
        'rec2 home "$HOME"',
        'rec2 user "$(id -un 2>/dev/null)"',
        'rec2 loginuser "$USER"',
        '_r=$(sed -n "s/^PRETTY_NAME=//p" /etc/os-release 2>/dev/null | head -1 | tr -d '
        + chr(39) + chr(34) + chr(39) + ')',
        'rec2 osrelease "$_r"',
        'rec2 macosver "$(sw_vers -productVersion 2>/dev/null)"',
        'rec2 haswsl "$(command -v wsl.exe >/dev/null 2>&1 && echo yes || echo no)"',
    ]

    for b in spec.bins:
        # The probed marker distinguishes "looked and absent" from "never looked".
        lines.append("rec2 probed " + _q(b.key) + " 1")
        # A binary found on PATH wins; otherwise fall back to known locations.
        lines.append(
            "if _p=$(command -v -- " + _q(b.binary) + ' 2>/dev/null) && [ -n "$_p" ]; then '
            + "rec3 which " + _q(b.key) + ' "$_p"; '
            + "else _p=; fi"
        )
        for cand in b.candidates:
            lines.append(
                'if [ -z "$_p" ] && [ -x ' + _pathq(cand) + " ]; then "
                + "rec3 which " + _q(b.key) + " " + _pathq(cand) + "; "
                + "_p=" + _pathq(cand) + "; fi"
            )
        args = " ".join(_q(a) for a in b.version_args)
        lines.append(
            "_o=$(" + _q(b.binary) + " " + args + " 2>&1); _r2=$?; " + FLATTEN
            + "rec4 version " + _q(b.key) + ' "$_r2" "$_o"'
        )
        for cand in b.candidates:
            lines.append(
                'if [ "$_r2" != "0" ] && [ -x ' + _pathq(cand) + ' ]; then '
                + "_o=$(" + _pathq(cand) + " " + args + " 2>&1); _r3=$?; " + FLATTEN
                + "rec4 version " + _q(b.key) + ' "$_r3" "$_o"; fi'
            )

    for p in files:
        hp = _pathq(p)
        lines += [
            "if [ -f " + hp + " ]; then",
            "  _sz=$(wc -c < " + hp + ' 2>/dev/null | tr -d " ")',
            '  [ -n "$_sz" ] || _sz=0',
            '  if [ "$_sz" -gt ' + str(MAX_FILE_BYTES) + " ]; then",
            '    rec4 file ' + _q(p) + ' "$_sz" toolarge',
            "  else",
            '    rec4 file ' + _q(p) + ' "$_sz" present',
            "    cat " + hp + " 2>/dev/null",
            '    printf "' + ESC_N + '"',
            "    rec2 endfile " + _q(p),
            "  fi",
            "elif [ -d " + hp + " ]; then rec4 file " + _q(p) + " 0 isdir",
            "else rec4 file " + _q(p) + " 0 missing; fi",
        ]

    for p in dirs:
        hp = _pathq(p)
        lines += [
            "if [ -d " + hp + " ]; then",
            "  rec3 dir " + _q(p) + " dir",
            "  _n=0",
            "  for _e in " + hp + "/*; do",
            '    [ -e "$_e" ] || continue',
            '    _n=$((_n+1)); [ "$_n" -gt ' + str(MAX_DIR_ENTRIES) + " ] && break",
            '    _b=$(basename "$_e")',
            '    if [ -d "$_e" ]; then rec4 ls '
            + _q(p)
            + ' "$_b" d; else rec4 ls '
            + _q(p)
            + ' "$_b" f; fi',
            "  done",
            "else rec3 dir " + _q(p) + " missing; fi",
        ]

    for p in skill_dirs:
        hp = _pathq(p)
        lines += [
            "if [ -d " + hp + " ]; then",
            "  rec3 skilldir " + _q(p) + " dir",
            "  for _s in " + hp + "/*/; do",
            '    [ -d "$_s" ] || continue',
            '    _sn=$(basename "$_s")',
            '    if [ -f "$_s/SKILL.md" ]; then rec4 skill '
            + _q(p)
            + ' "$_sn" "$_s/SKILL.md"',
            "    else rec4 skill " + _q(p) + ' "$_sn" ""; fi',
            "  done",
            "  for _s in " + hp + "/*.md; do",
            '    [ -f "$_s" ] || continue',
            '    _sn=$(basename "$_s")',
            "    rec4 skill " + _q(p) + ' "$_sn" "$_s"',
            "  done",
            "else rec3 skilldir " + _q(p) + " missing; fi",
        ]

    lines.append("rec2 done 1")
    return NL.join(lines) + NL


# ---------------------------------------------------------------------------


@dataclass
class FileFact:
    path: str
    status: str = "unknown"
    size: int = 0
    content: str = None
    b64: bool = False
    error: str = None

    @property
    def ok(self) -> bool:
        return self.status == "ok" and self.content is not None


@dataclass
class RemoteFacts:
    """Parsed view of a probe run."""

    os: str = ""
    arch: str = ""
    kernel: str = ""
    home: str = ""
    user: str = ""
    osrelease: str = ""
    macosver: str = ""
    has_wsl: bool = False
    probed: set = field(default_factory=set)
    which: dict = field(default_factory=dict)
    version_rc: dict = field(default_factory=dict)
    version_out: dict = field(default_factory=dict)
    files: dict = field(default_factory=dict)
    dirs: dict = field(default_factory=dict)
    ls: dict = field(default_factory=dict)
    skills: dict = field(default_factory=dict)
    truncated: bool = False

    def bin_path(self, key: str):
        return self.which.get(key) or None

    def version(self, key: str):
        return self.version_rc.get(key, -1), self.version_out.get(key, "")

    def file(self, path: str) -> FileFact:
        return self.files.get(path, FileFact(path=path, status="unknown"))


def _f(parts, i):
    return parts[i] if len(parts) > i else ""


def parse_probe_output(stdout: str) -> RemoteFacts:
    facts = RemoteFacts()
    current = None
    buf: list = []
    for line in stdout.split(NL):
        # Windows emitters add CR; a lone CR is never meaningful data here.
        if line.endswith(chr(13)):
            line = line[:-1]
        if current is not None:
            if line.startswith("AF" + chr(9) + "endfile" + chr(9)):
                if current.b64:
                    import base64

                    try:
                        raw = base64.b64decode("".join(buf), validate=False).decode(
                            "utf-8", "replace"
                        )
                    except Exception:
                        raw = ""
                        current.error = "base64 decode failed"
                else:
                    raw = NL.join(buf)
                    want = current.size
                    got = len(raw.encode("utf-8", "replace"))
                    if got > want:
                        raw = raw[: max(0, len(raw) - (got - want))]
                if current.size == 0 and raw == "":
                    current.status = "empty"
                else:
                    current.status = "ok"
                if current.b64 and current.error:
                    current.status = "unreadable"
                current.content = raw
                facts.files[current.path] = current
                current, buf = None, []
                continue
            buf.append(line)
            continue
        if not line.startswith("AF" + chr(9)):
            continue
        parts = line.split(chr(9))
        tag = _f(parts, 1)
        if tag == "os":
            facts.os = _f(parts, 2)
        elif tag == "arch":
            facts.arch = _f(parts, 2)
        elif tag == "kernel":
            facts.kernel = _f(parts, 2)
        elif tag == "home":
            facts.home = _f(parts, 2)
        elif tag == "user":
            facts.user = _f(parts, 2)
        elif tag == "osrelease":
            facts.osrelease = _f(parts, 2)
        elif tag == "macosver":
            facts.macosver = _f(parts, 2)
        elif tag == "haswsl":
            facts.has_wsl = _f(parts, 2) == "yes"
        elif tag == "probed":
            facts.probed.add(_f(parts, 2))
        elif tag == "which":
            key, value = _f(parts, 2), _f(parts, 3)
            if key and value and key not in facts.which:
                facts.which[key] = value
        elif tag == "version":
            key = _f(parts, 2)
            try:
                rc = int(_f(parts, 3))
            except ValueError:
                rc = -1
            facts.version_rc[key] = rc
            facts.version_out[key] = _f(parts, 4)
        elif tag == "file":
            path = _f(parts, 2)
            try:
                size = int(_f(parts, 3))
            except ValueError:
                size = 0
            status = _f(parts, 4)
            if status == "b64":
                fact = FileFact(path=path, status="present", size=size, b64=True)
                current, buf = fact, []
                continue
            fact = FileFact(path=path, status=status, size=size)
            if status == "present":
                current, buf = fact, []
            else:
                facts.files[path] = fact
        elif tag == "dir":
            facts.dirs[_f(parts, 2)] = _f(parts, 3)
        elif tag == "ls":
            facts.ls.setdefault(_f(parts, 2), []).append((_f(parts, 3), _f(parts, 4)))
        elif tag == "skilldir":
            facts.skills.setdefault(_f(parts, 2), [])
        elif tag == "skill":
            facts.skills.setdefault(_f(parts, 2), []).append(
                {"name": _f(parts, 3), "path": _f(parts, 3), "skill_file": _f(parts, 4)}
            )
        elif tag == "done":
            facts.truncated = False
    if current is not None:
        facts.truncated = True
        facts.files[current.path] = FileFact(
            path=current.path, status="truncated", size=current.size
        )
    return facts
