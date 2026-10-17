"""Adapter contract and shared parsing helpers.

Every adapter turns RemoteFacts into:
  - Detection:  is the agent there, which binary, which version, how installed
  - sources:    which files we looked at and whether they were readable
  - ParsedConfig: normalized + sanitized fields

Adapters never receive a live connection; they only see RemoteFacts, so they
can be unit tested from recorded fixtures.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - py<3.11
    tomllib = None

import yaml

from agent_fleet.adapters.sanitize import mask_url, sanitize_object
from agent_fleet.remotes.probe import ProbeSpec


# --------------------------------------------------------------------------
# formats
# --------------------------------------------------------------------------


class ParseFailure(Exception):
    pass


def parse_toml(text: str):
    if tomllib is None:
        raise ParseFailure("tomllib unavailable")
    return tomllib.loads(text)


def _strip_jsonc(text: str) -> str:
    """Remove // and /* */ comments plus trailing commas, string-aware."""
    out = []
    i, n = 0, len(text)
    in_str = False
    escaped = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if escaped:
                escaped = False
            elif c == chr(92):
                escaped = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != chr(10):
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    cleaned = "".join(out)
    # trailing commas: ", }" or ", ]"
    buf, res = [], []
    for ch in cleaned:
        if ch in "}]":
            while buf and buf[-1] in (" " + chr(9) + chr(10) + chr(13) + ","):
                buf.pop()
            res.append("".join(buf))
            buf = []
            res.append(ch)
            continue
        buf.append(ch)
    return "".join(res) + "".join(buf)


def parse_json(text: str):
    return json.loads(text)


def parse_jsonc(text: str):
    cleaned = _strip_jsonc(text)
    if not cleaned.strip():
        raise ParseFailure("empty after stripping comments")
    return json.loads(cleaned)


def load_structured(text: str, fmt: str):
    if fmt == "toml":
        return parse_toml(text)
    if fmt == "json":
        return parse_json(text)
    if fmt == "jsonc":
        return parse_jsonc(text)
    if fmt in ("yaml", "yml"):
        return yaml.safe_load(text)
    raise ParseFailure(f"unsupported format: {fmt}")


# --------------------------------------------------------------------------
# value helpers
# --------------------------------------------------------------------------


def extract_version(raw: str):
    if not raw:
        return None
    for tok in raw.replace(",", " ").split():
        t = tok.strip("()[]{};,")
        if t[:1] == "v" and len(t) > 1:
            t = t[1:]
        if not t or not t[0].isdigit():
            continue
        core = ""
        for ch in t:
            if ch.isdigit() or ch == ".":
                core += ch
            else:
                break
        core = core.strip(".")
        if core and any(ch.isdigit() for ch in core):
            return core
    return None


INSTALL_RULES = (
    (".nvm/versions/node", "npm (nvm)"),
    ("/.bun/bin", "bun"),
    ("/.local/share/pnpm", "pnpm"),
    ("/.volta/", "volta"),
    ("/.cargo/bin", "cargo"),
    ("/.deno/bin", "deno"),
    ("/.yarn/bin", "yarn"),
    ("/.claude/local", "claude native installer"),
    ("/.local/bin", "user-local install"),
    ("/opt/homebrew/bin", "homebrew"),
    ("/home/linuxbrew", "homebrew"),
    ("/usr/local/bin", "system path install"),
    ("/snap/bin", "snap"),
    ("/.pi/agent", "pi local"),
    ("/.hermes/", "hermes local"),
    ("appdata/roaming/npm", "npm (windows)"),
    ("/usr/bin", "distro package"),
    ("/usr/sbin", "distro package"),
)


def guess_install_method(path: str):
    if not path:
        return None
    low = path.lower().replace(chr(92), "/")
    for marker, label in INSTALL_RULES:
        if marker in low:
            return label
    return None


# --------------------------------------------------------------------------
# results
# --------------------------------------------------------------------------


@dataclass
class SourceInfo:
    scope: str = "user"
    path: str = ""
    format: str = "json"
    kind: str = "config"
    status: str = "unknown"  # ok|missing|isdir|unreadable|toolarge|truncated|parse_error|unsupported
    size: int = 0
    content: str = None
    error: str = None

    @property
    def usable(self) -> bool:
        return self.status == "ok" and self.content is not None


@dataclass
class Detection:
    status: str = "not_scanned"  # installed|not_installed|config_only|unknown|error
    detected: bool = False
    version: str = None
    version_raw: str = None
    exec_path: str = None
    install_method: str = None
    config_dir: str = None
    error: str = None


@dataclass
class ParsedConfig:
    parse_status: str = "empty"  # parsed|partial|empty|unsupported|error
    resolution: str = "declared"  # declared|effective|unknown
    model: str = None
    model_provider: str = None
    base_url: str = None
    base_url_masked: bool = False
    mcp_servers: list = field(default_factory=list)
    skills: list = field(default_factory=list)
    settings: dict = field(default_factory=dict)
    unsupported_fields: list = field(default_factory=list)
    sanitized: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)


@dataclass
class AgentResult:
    agent_id: str
    detection: Detection
    sources: list
    config: ParsedConfig


class AgentAdapter(ABC):
    agent_id: str = "unknown"
    display_name: str = "Unknown"
    # adapters that only do discovery set this to False
    supports_config: bool = True

    @abstractmethod
    def probe_spec(self) -> ProbeSpec:
        """What to look for on the remote side."""

    @abstractmethod
    def detect(self, facts) -> Detection:
        """Executable + version + install method."""

    @abstractmethod
    def discover_config(self, facts) -> list:
        """Config files and where they live."""

    @abstractmethod
    def parse_config(self, facts, sources) -> ParsedConfig:
        """Normalize + sanitize; never raise."""

    def sanitize(self, data: dict) -> dict:
        return sanitize_object(data)

    def run(self, facts) -> AgentResult:
        detection = self.detect(facts)
        sources = self.discover_config(facts)
        config = ParsedConfig()
        if self.supports_config:
            try:
                config = self.parse_config(facts, sources)
            except Exception as exc:  # adapters must never break a scan
                config = ParsedConfig(
                    parse_status="error",
                    warnings=[f"parse error: {type(exc).__name__}: {exc}"[:300]],
                )
        else:
            config = ParsedConfig(
                parse_status="unsupported",
                unsupported_fields=["<full config parsing not implemented>"],
            )
        return AgentResult(agent_id=self.agent_id, detection=detection, sources=sources, config=config)


def _read(facts, path: str, fmt: str, scope: str = "user", kind: str = "config") -> SourceInfo:
    """Materialize one fact into a SourceInfo."""
    f = facts.file(path)
    if f.status == "missing":
        return SourceInfo(scope=scope, path=path, format=fmt, kind=kind, status="missing")
    if f.status == "isdir":
        return SourceInfo(scope=scope, path=path, format=fmt, kind=kind, status="isdir")
    if f.status == "toolarge":
        return SourceInfo(
            scope=scope,
            path=path,
            format=fmt,
            kind=kind,
            status="toolarge",
            size=f.size,
            error=f"file too large ({f.size} bytes > cap)",
        )
    if f.status == "truncated":
        return SourceInfo(
            scope=scope,
            path=path,
            format=fmt,
            kind=kind,
            status="truncated",
            size=f.size,
            error="probe output truncated",
        )
    if f.status == "unreadable" or f.content is None:
        return SourceInfo(
            scope=scope,
            path=path,
            format=fmt,
            kind=kind,
            status="unreadable",
            error="permission denied or read failed",
        )
    return SourceInfo(
        scope=scope, path=path, format=fmt, kind=kind, status="ok", size=f.size, content=f.content
    )


def _load(source: SourceInfo):
    """Load a usable source; raises ParseFailure."""
    if not source.usable:
        raise ParseFailure(source.status)
    return load_structured(source.content, source.format)


def _mask_endpoint(url):
    masked, changed = mask_url(url)
    return masked, changed


def _skills_from_dir(facts, path: str) -> list:
    entries = facts.skills.get(path, [])
    out = []
    for e in entries:
        name = e.get("name") or ""
        if not name:
            continue
        out.append({"name": name, "path": e.get("path") or "", "has_skill_md": bool(e.get("skill_file"))})
    return out


def _mcp_common(entry: dict, name: str) -> dict:
    """Normalize one MCP server entry across agents, dropping secrets."""
    transport = "stdio"
    if isinstance(entry, dict):
        if entry.get("url") or entry.get("type") in ("http", "sse", "streamable-http", "url"):
            transport = entry.get("type") or "http"
    item = {
        "name": name,
        "transport": transport,
        "command": entry.get("command") if isinstance(entry.get("command"), str) else None,
        "args": entry.get("args")[:10] if isinstance(entry.get("args"), list) else [],
        "enabled": entry.get("enabled", None),
    }
    if isinstance(entry.get("url"), str):
        masked, changed = mask_url(entry["url"])
        item["url"] = masked
        item["url_masked"] = changed
    else:
        item["url"] = None
    env = entry.get("env") or entry.get("environment") or entry.get("headers") or entry.get("http_headers")
    if isinstance(env, dict) and env:
        item["env_keys"] = sorted([str(k)[:60] for k in env.keys()])[:20]
        item["secret_keys_redacted"] = True
    return item
