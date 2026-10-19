"""Qoder (QoderCN) adapter.

Qoder is an agentic AI coding assistant and CLI (qoder / qodercli / qoderclicn).
Config hierarchy:
  User:
    ~/.qoder/settings.json
    ~/.qoder/settings.local.json
    ~/.qoder/mcp.json
    ~/.qoder/AGENTS.md
  Project:
    PROJECT/.qoder/settings.json
    PROJECT/.qoder/settings.local.json
    PROJECT/.qoder/mcp.json
    PROJECT/.mcp.json
    PROJECT/AGENTS.md
  Skills:
    ~/.qoder/skills
  Plugins:
    ~/.qoder/plugins
  Rules:
    ~/.qoder/rules
"""

from __future__ import annotations

from agent_fleet.adapters.base import (
    AgentAdapter,
    Detection,
    ParsedConfig,
    ProbeSpec,
    _load,
    _mask_endpoint,
    _mcp_common,
    _read,
    _skills_from_dir,
    apply_scope,
    extract_version,
    guess_install_method,
)
from agent_fleet.adapters.sanitize import is_secret_key, scrub_env
from agent_fleet.remotes.probe import BinProbe

SETTINGS_PATH = "~/.qoder/settings.json"
LOCAL_SETTINGS_PATH = "~/.qoder/settings.local.json"
USER_MCP_PATH = "~/.qoder/mcp.json"
USER_AGENTS_PATH = "~/.qoder/AGENTS.md"

CN_SETTINGS_PATH = "~/.qoder-cn/settings.json"
CN_LOCAL_SETTINGS_PATH = "~/.qoder-cn/settings.local.json"
CN_USER_MCP_PATH = "~/.qoder-cn/mcp.json"
CN_USER_AGENTS_PATH = "~/.qoder-cn/AGENTS.md"

SETTING_KEYS = (
    "model",
    "provider",
    "language",
    "theme",
    "autoMemoryEnabled",
    "trustedDirectories",
    "projects",
    "workspaces",
    "permissions",
)

BIN_KEYS = ("qoder:qodercli", "qoder:qoderclicn")


class QoderAdapter(AgentAdapter):
    agent_id = "qoder"
    display_name = "Qoder"
    supports_config = True

    PRECEDENCE_CHAIN = [
        ("user", SETTINGS_PATH),
        ("user", CN_SETTINGS_PATH),
        ("user", LOCAL_SETTINGS_PATH),
        ("user", CN_LOCAL_SETTINGS_PATH),
        ("user", USER_MCP_PATH),
        ("user", CN_USER_MCP_PATH),
        ("project", "PROJECT/.qoder/settings.json"),
        ("project", "PROJECT/.qoder-cn/settings.json"),
        ("project", "PROJECT/.qoder/settings.local.json"),
        ("project", "PROJECT/.qoder-cn/settings.local.json"),
        ("project", "PROJECT/.qoder/mcp.json"),
        ("project", "PROJECT/.qoder-cn/mcp.json"),
        ("project", "PROJECT/.mcp.json"),
        ("project", "PROJECT/AGENTS.md"),
    ]
    system_paths: list = []
    project_files = [
        ".qoder/settings.json",
        ".qoder/settings.local.json",
        ".qoder/mcp.json",
        ".qoder-cn/settings.json",
        ".qoder-cn/settings.local.json",
        ".qoder-cn/mcp.json",
        ".mcp.json",
        "AGENTS.md",
    ]

    def probe_spec(self) -> ProbeSpec:
        return ProbeSpec(
            bins=[
                BinProbe(
                    key="qoder:qodercli",
                    binary="qodercli",
                    version_args=["--version"],
                    candidates=[
                        "~/.local/bin/qodercli",
                        "~/.qoder/bin/qodercli",
                        "/usr/local/bin/qodercli",
                        "/usr/bin/qodercli",
                    ],
                ),
                BinProbe(
                    key="qoder:qoderclicn",
                    binary="qoderclicn",
                    version_args=["--version"],
                    candidates=[
                        "~/.local/bin/qoderclicn",
                        "~/.qodersec/bin/qoderclicn",
                        "~/.qoder-cn/entry/qoder-cn",
                        "~/.qoder-cn/entry/qodercn",
                        "~/.qoder-cn/bin/qoderclicn/qoderclicn",
                        "~/.qoder-cn/bin/qoderclicn",
                        "~/.qoder/bin/qodercli",
                        "/usr/local/bin/qoderclicn",
                        "/usr/bin/qoderclicn",
                    ],
                ),
            ],
            files=[
                SETTINGS_PATH,
                LOCAL_SETTINGS_PATH,
                USER_MCP_PATH,
                USER_AGENTS_PATH,
                CN_SETTINGS_PATH,
                CN_LOCAL_SETTINGS_PATH,
                CN_USER_MCP_PATH,
                CN_USER_AGENTS_PATH,
            ],
            dirs=[
                "~/.qoder/plugins",
                "~/.qoder/rules",
                "~/.qoder-cn/plugins",
                "~/.qoder-cn/rules",
            ],
            skill_dirs=["~/.qoder/skills", "~/.qoder-cn/skills"],
        )

    def detect(self, facts) -> Detection:
        found_key = None
        for key in BIN_KEYS:
            if facts.bin_path(key):
                found_key = key
                break
        if not found_key:
            if not any(k in facts.probed for k in BIN_KEYS):
                return Detection(status="unknown", error="no probe data for qoder")
            return Detection(status="not_installed")

        path = facts.bin_path(found_key)
        rc, out = facts.version(found_key)
        raw = out.strip()
        config_dir = "~/.qoder"
        if found_key == "qoder:qoderclicn":
            cn_file = facts.file(CN_SETTINGS_PATH)
            if cn_file.status in ("ok", "exists"):
                config_dir = "~/.qoder-cn"

        return Detection(
            status="installed",
            detected=True,
            version=extract_version(raw) if rc == 0 else None,
            version_raw=raw or None,
            exec_path=path,
            install_method=guess_install_method(path),
            config_dir=config_dir,
            error=None if rc == 0 else f"version command exited {rc}: {raw[:200]}",
        )

    def project_roots(self, facts) -> list:
        for p in (SETTINGS_PATH, LOCAL_SETTINGS_PATH, CN_SETTINGS_PATH, CN_LOCAL_SETTINGS_PATH):
            src = _read(facts, p, "json")
            if not src.usable:
                continue
            try:
                data = _load(src)
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            roots = (
                data.get("trustedDirectories")
                or data.get("projects")
                or data.get("workspaces")
            )
            if isinstance(roots, dict):
                return [str(k) for k in roots.keys()][:5]
            if isinstance(roots, list):
                return [str(k) for k in roots if isinstance(k, str)][:5]
        return []

    def discover_config(self, facts) -> list:
        sources = [
            _read(facts, SETTINGS_PATH, "json", scope="user", kind="config"),
            _read(facts, LOCAL_SETTINGS_PATH, "json", scope="user", kind="config"),
            _read(facts, USER_MCP_PATH, "json", scope="user", kind="mcp"),
            _read(facts, USER_AGENTS_PATH, "text", scope="user", kind="instructions"),
            _read(facts, CN_SETTINGS_PATH, "json", scope="user", kind="config"),
            _read(facts, CN_LOCAL_SETTINGS_PATH, "json", scope="user", kind="config"),
            _read(facts, CN_USER_MCP_PATH, "json", scope="user", kind="mcp"),
            _read(facts, CN_USER_AGENTS_PATH, "text", scope="user", kind="instructions"),
        ]
        for root in self.project_roots(facts):
            for rel in self.project_files:
                kind = (
                    "mcp"
                    if rel.endswith("mcp.json")
                    else ("instructions" if rel.endswith(".md") else "config")
                )
                fmt = "text" if rel.endswith(".md") else "json"
                path = root.rstrip("/") + "/" + rel
                sources.append(_read(facts, path, fmt, scope="project", kind=kind))
        return sources

    def parse_config(self, facts, sources) -> ParsedConfig:
        cfg = ParsedConfig(resolution="declared")
        by_path = {s.path: s for s in sources}
        settings_src = by_path.get(SETTINGS_PATH)
        local_src = by_path.get(LOCAL_SETTINGS_PATH)
        cn_settings_src = by_path.get(CN_SETTINGS_PATH)
        cn_local_src = by_path.get(CN_LOCAL_SETTINGS_PATH)

        # Settings files in precedence order (user settings < local user settings < project settings)
        ordered = [src for src in sources if src.scope == "project" and src.kind == "config"]
        ordered.sort(key=lambda s: s.path)
        if cn_local_src and cn_local_src.usable:
            ordered.insert(0, cn_local_src)
        if local_src and local_src.usable:
            ordered.insert(0, local_src)
        if cn_settings_src and cn_settings_src.usable:
            ordered.insert(0, cn_settings_src)
        if settings_src and settings_src.usable:
            ordered.insert(0, settings_src)

        settings_broken = False
        dropped_keys = set()
        merged_mcp: dict[str, dict] = {}

        for src in ordered:
            if not src.usable:
                continue
            try:
                data = _load(src)
            except Exception as exc:
                cfg.warnings.append(f"{src.path}: {type(exc).__name__}: {exc}"[:300])
                if src.path == SETTINGS_PATH:
                    settings_broken = True
                continue
            if not isinstance(data, dict):
                continue

            if isinstance(data.get("model"), str):
                cfg.model = data["model"]
            if isinstance(data.get("provider"), str):
                cfg.model_provider = data["provider"]

            raw_url = data.get("baseURL") or data.get("base_url") or data.get("endpoint")
            if isinstance(raw_url, str):
                cfg.base_url, cfg.base_url_masked = _mask_endpoint(raw_url)
                if not cfg.model_provider:
                    cfg.model_provider = "custom"

            for key in SETTING_KEYS:
                if key in ("model", "provider") or key not in data:
                    continue
                val = data[key]
                if is_secret_key(key):
                    continue
                if isinstance(val, (str, int, float, bool)):
                    cfg.settings[key] = val
                elif isinstance(val, list):
                    cfg.settings[key] = len(val)

            hooks = data.get("hooks")
            if isinstance(hooks, dict) and hooks:
                cfg.settings["hooks"] = sorted(hooks.keys())[:20]

            env = data.get("env")
            if isinstance(env, dict) and env:
                cfg.settings["env_keys"] = sorted(scrub_env(env).keys())[:40]

            mcp_entries = data.get("mcpServers")
            if isinstance(mcp_entries, dict):
                for name, entry in list(mcp_entries.items())[:60]:
                    item = _mcp_common(entry, name) if isinstance(entry, dict) else {"name": name}
                    item["scope"] = src.scope
                    item["source"] = src.path
                    merged_mcp[name] = item

            for k in data.keys():
                if k not in SETTING_KEYS and k not in (
                    "mcpServers",
                    "hooks",
                    "env",
                    "baseURL",
                    "base_url",
                    "endpoint",
                ):
                    dropped_keys.add(str(k))

        if not cfg.model_provider:
            cfg.model_provider = "qoder" if cfg.model else None

        cfg.unsupported_fields = sorted(dropped_keys)[:60]

        project_mcp_files = 0
        for src in sources:
            if src.kind != "mcp" or not src.usable:
                continue
            try:
                data = _load(src)
            except Exception as exc:
                cfg.warnings.append(f"{src.path}: {type(exc).__name__}: {exc}"[:300])
                continue
            if not isinstance(data, dict):
                continue
            entries = data.get("mcpServers")
            if not isinstance(entries, dict):
                continue
            if src.scope == "project":
                project_mcp_files += 1
            for name, entry in list(entries.items())[:60]:
                item = _mcp_common(entry, name) if isinstance(entry, dict) else {"name": name}
                item["scope"] = src.scope
                item["source"] = src.path
                merged_mcp[name] = item

        if project_mcp_files:
            cfg.settings["project_mcp_files"] = project_mcp_files

        cfg.mcp_servers = list(merged_mcp.values())

        skills = _skills_from_dir(facts, "~/.qoder/skills") + _skills_from_dir(
            facts, "~/.qoder-cn/skills"
        )
        cfg.skills = skills

        plugins = facts.ls.get("~/.qoder/plugins", []) + facts.ls.get("~/.qoder-cn/plugins", [])
        if plugins:
            cfg.settings["plugins"] = sorted(
                list(
                    {
                        n
                        for n, kind in plugins
                        if kind == "d" and not n.startswith(".")
                    }
                )
            )[:30]

        rules = facts.ls.get("~/.qoder/rules", []) + facts.ls.get("~/.qoder-cn/rules", [])
        if rules:
            cfg.settings["rules"] = sorted(
                list({n for n, kind in rules if not n.startswith(".")})
            )[:30]

        _, unresolved = self.resolution_for(sources)
        any_usable = any(s.usable for s in sources)
        if not any_usable:
            cfg.parse_status = "error" if unresolved else "empty"
        elif settings_broken or any(
            src is not None and src.status not in ("ok", "missing", "empty", "not_probed")
            for src in (settings_src, local_src)
        ):
            cfg.parse_status = "partial"
        elif cfg.model or cfg.mcp_servers or cfg.skills or cfg.settings:
            cfg.parse_status = "parsed"
        else:
            cfg.parse_status = "empty"

        apply_scope(cfg, self, sources, self.project_roots(facts))
        primary_cfg_path = (
            CN_SETTINGS_PATH
            if (cn_settings_src and cn_settings_src.usable)
            and not (settings_src and settings_src.usable)
            else SETTINGS_PATH
        )
        cfg.sanitized = {
            "agent": self.agent_id,
            "scope": "user",
            "resolution": cfg.resolution,
            "scopes": cfg.settings.get("scopes"),
            "config_file": primary_cfg_path,
            "model": cfg.model,
            "provider": cfg.model_provider,
            "network": {"base_url": cfg.base_url, "masked": cfg.base_url_masked},
            "settings": {
                k: v for k, v in cfg.settings.items() if k not in ("_env_keys", "env_keys")
            },
            "env_keys": cfg.settings.get("env_keys"),
            "mcp_servers": cfg.mcp_servers,
            "skills": cfg.skills,
            "unsupported_fields": cfg.unsupported_fields,
            "warnings": cfg.warnings,
        }
        return cfg
