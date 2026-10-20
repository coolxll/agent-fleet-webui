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

from agent_fleet.adapters.sanitize import (
    mask_url,
    redact_value,
    sanitize_object,
)
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
    low = raw.lower()
    for noise in ("command not found", "no such file", "not recognized", "permission denied"):
        if noise in low:
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
    ("/.qoder/", "qoder local"),
    ("/.qoder-cn/", "qoder local"),
    ("/.qodercn/", "qoder local"),
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
    # Optional, provider-specific account quota snapshot (for example the
    # agy-swap accounts file). Only sanitized values ever reach this field.
    quota: dict = None
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

    # ---- scope model ---------------------------------------------------
    # system  : machine-wide policy, read-only for us
    # user    : the account running the collection
    # project : per-repository files, discovered from the user config
    # Precedence runs low -> high in PRECEDENCE_CHAIN.
    PRECEDENCE_CHAIN: list = []  # [(scope, path), ...] low priority first
    system_paths: list = []
    project_files: list = []

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

    def project_roots(self, facts) -> list:
        """Project directories whose scope matters for this agent.

        Derived from the user-scope config (Claude's projects table, Codex's
        [projects] table, ...). The scanner runs a second pass over these.
        """
        return []

    def scope_report(self, sources, project_roots=None) -> list:
        """Per-scope provenance for the UI.

        Project entries carry a "PROJECT/" prefix in the chain; they are
        expanded to every discovered root so the report shows real paths.
        """
        by_path = {s.path: s for s in sources or []}
        out = []
        for scope, path in self.PRECEDENCE_CHAIN:
            if path.startswith("PROJECT/"):
                rel = path[len("PROJECT/") :]
                roots = list(project_roots or [])
                if not roots:
                    out.append({"scope": scope, "path": rel, "status": "no_projects"})
                    continue
                for root in roots:
                    full = root.rstrip("/") + "/" + rel
                    src = by_path.get(full)
                    out.append(
                        {
                            "scope": scope,
                            "path": full,
                            "status": src.status if src else "not_probed",
                        }
                    )
                continue
            src = by_path.get(path)
            out.append(
                {
                    "scope": scope,
                    "path": path,
                    "status": src.status if src else "not_probed",
                }
            )
        return out

    def resolution_for(self, sources) -> tuple:
        """(resolution, unresolved_paths).

        "effective" is only claimed when every scope in the chain was actually
        readable (or definitively absent). A file that exists but could not be
        read or parsed makes the answer "declared" instead.
        """
        # Any scope file that exists but could not be read stops the chain.
        unresolved = []
        for src in sources or []:
            # Quota files are auxiliary; they never define the effective
            # configuration, so an unreadable one must not lower the verdict.
            if src.kind == "quota":
                continue
            if src.status in ("ok", "missing", "empty", "not_probed"):
                continue
            unresolved.append(src.path)
        return ("effective" if not unresolved else "declared", unresolved)

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
        config = scrub_config(config)
        return AgentResult(
            agent_id=self.agent_id, detection=detection, sources=sources, config=config
        )


def apply_scope(cfg: "ParsedConfig", adapter, sources, project_roots=None) -> "ParsedConfig":
    """Attach scope provenance and the resolution verdict to a config."""
    resolution, unresolved = adapter.resolution_for(sources)
    cfg.resolution = resolution
    if unresolved:
        cfg.warnings.append(
            "effective values could not be resolved, unreadable: "
            + ", ".join(unresolved[:5])
        )
    cfg.settings["scope_resolution"] = resolution
    cfg.settings["scopes"] = adapter.scope_report(sources, project_roots)
    cfg.sanitized = dict(cfg.sanitized or {})
    cfg.sanitized["resolution"] = resolution
    cfg.sanitized["scopes"] = cfg.settings["scopes"]
    return cfg


def scrub_config(cfg: "ParsedConfig") -> "ParsedConfig":
    """Last line of defence: no adapter output reaches storage unscrubbed.

    Walks every value recursively, dropping secret-named keys (apiKey and
    friends can be nested inside an otherwise innocent "provider" block) and
    redacting values that look like credentials.
    """
    cfg.settings = sanitize_object(cfg.settings) if cfg.settings else {}
    cfg.sanitized = sanitize_object(cfg.sanitized) if cfg.sanitized else {}
    cfg.mcp_servers = sanitize_object(cfg.mcp_servers) if cfg.mcp_servers else []
    cfg.skills = sanitize_object(cfg.skills) if cfg.skills else []
    # Quota snapshots are already an explicit whitelist assembled by the
    # adapter (texts truncated, fractions clamped), so they are not passed
    # through the depth-limited generic sanitizer.
    if isinstance(cfg.model, str):
        cfg.model = redact_value(cfg.model)
    if isinstance(cfg.model_provider, str):
        cfg.model_provider = redact_value(cfg.model_provider)
    if isinstance(cfg.base_url, str):
        cfg.base_url = redact_value(cfg.base_url)
    return cfg


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
    if f.status == "empty":
        return SourceInfo(
            scope=scope, path=path, format=fmt, kind=kind, status="empty", size=0, content=""
        )
    if f.status == "unreadable" or (f.content is None and f.size > 0):
        return SourceInfo(
            scope=scope,
            path=path,
            format=fmt,
            kind=kind,
            status="unreadable",
            error="permission denied or read failed",
        )
    if f.status == "unknown":
        # never probed (for example a scope added after the probe ran)
        return SourceInfo(
            scope=scope, path=path, format=fmt, kind=kind, status="not_probed", error="not probed"
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
    """Normalize one MCP server entry across agents, dropping secrets.

    Handles both the Claude-style shape (url / command / args / env) and the
    Gemini/Antigravity shape (serverUrl / headers / disabled).
    """
    transport = "stdio"
    if isinstance(entry, dict):
        if entry.get("url") or entry.get("serverUrl") or entry.get("type") in (
            "http",
            "sse",
            "streamable-http",
            "url",
        ):
            transport = entry.get("type") or "http"
    item = {
        "name": name,
        "transport": transport,
        "command": entry.get("command") if isinstance(entry.get("command"), str) else None,
        "args": entry.get("args")[:10] if isinstance(entry.get("args"), list) else [],
        "enabled": None if entry.get("enabled") is None else bool(entry.get("enabled")),
    }
    raw_url = None
    if isinstance(entry, dict):
        raw_url = entry.get("url") if isinstance(entry.get("url"), str) else None
        if raw_url is None and isinstance(entry.get("serverUrl"), str):
            raw_url = entry["serverUrl"]
    if raw_url:
        masked, changed = mask_url(raw_url)
        item["url"] = masked
        item["url_masked"] = changed
    else:
        item["url"] = None
    env = entry.get("env") or entry.get("environment") or entry.get("headers") or entry.get("http_headers")
    if isinstance(env, dict) and env:
        item["env_keys"] = sorted([str(k)[:60] for k in env.keys()])[:20]
        item["secret_keys_redacted"] = True
    if isinstance(entry, dict) and entry.get("disabled") is not None:
        item["enabled"] = not bool(entry["disabled"])
    return item
