"""Claude Code adapter.

Two files matter at user scope:
  ~/.claude/settings.json : env (base URL, model aliases), hooks, permissions
  ~/.claude.json         : mcpServers (user scope) + per-project state

Both routinely contain tokens (ANTHROPIC_AUTH_TOKEN and friends), so this
adapter only reads the keys it needs and reports env blocks by name.
"""

from __future__ import annotations

from agent_fleet.adapters.base import (
    AgentAdapter,
    apply_scope,
    Detection,
    ParsedConfig,
    ProbeSpec,
    _load,
    _mask_endpoint,
    _mcp_common,
    _read,
    _skills_from_dir,
    extract_version,
    guess_install_method,
)
from agent_fleet.adapters.sanitize import is_secret_key, scrub_env
from agent_fleet.remotes.probe import BinProbe

SETTINGS_PATH = "~/.claude/settings.json"
GLOBAL_PATH = "~/.claude.json"
LOCAL_SETTINGS_PATH = "~/.claude/settings.local.json"

SYSTEM_SETTINGS_PATH = "/etc/claude-code/managed-settings.json"

MODEL_ENV_KEYS = (
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL",
)
SETTING_KEYS = ("effortLevel", "model", "permissions", "enableAllProjectMcpServers", "theme", "language")


MAX_PROJECT_ROOTS = 5


class ClaudeAdapter(AgentAdapter):
    agent_id = "claude"
    display_name = "Claude Code"
    supports_config = True

    # Managed policy < user settings < project settings < project-local
    PRECEDENCE_CHAIN = [
        ("system", "/etc/claude-code/managed-settings.json"),
        ("user", SETTINGS_PATH),
        ("project", "PROJECT/.claude/settings.json"),
        ("project", "PROJECT/.claude/settings.local.json"),
    ]
    system_paths = ["/etc/claude-code/managed-settings.json"]
    project_files = [
        ".claude/settings.json",
        ".claude/settings.local.json",
        ".mcp.json",
    ]

    def project_roots(self, facts) -> list:
        """Claude records every project it has seen in ~/.claude.json."""
        src = _read(facts, GLOBAL_PATH, "json")
        if not src.usable:
            return []
        try:
            data = _load(src)
        except Exception:
            return []
        projects = data.get("projects") if isinstance(data, dict) else None
        if not isinstance(projects, dict):
            return []
        roots = [str(p) for p in projects.keys() if isinstance(p, str)]
        return roots[:MAX_PROJECT_ROOTS]

    def probe_spec(self) -> ProbeSpec:
        return ProbeSpec(
            bins=[
                BinProbe(
                    key="claude:claude",
                    binary="claude",
                    version_args=["--version"],
                    candidates=["~/.claude/local/claude", "~/.local/bin/claude"],
                )
            ],
            files=[SETTINGS_PATH, GLOBAL_PATH, LOCAL_SETTINGS_PATH],
            dirs=["~/.claude/plugins"],
            skill_dirs=["~/.claude/skills"],
        )

    def detect(self, facts) -> Detection:
        key = "claude:claude"
        path = facts.bin_path(key)
        rc, out = facts.version(key)
        raw = out.strip()
        if not path:
            if key not in facts.probed:
                return Detection(status="unknown", error="no probe data for claude")
            return Detection(status="not_installed")
        method = guess_install_method(path)
        if not method:
            method = self._method_from_state(facts)
        return Detection(
            status="installed",
            detected=True,
            version=extract_version(raw) if rc == 0 else None,
            version_raw=raw or None,
            exec_path=path,
            install_method=method,
            config_dir="~/.claude",
            error=None if rc == 0 else f"version command exited {rc}: {raw[:200]}",
        )

    def _method_from_state(self, facts):
        src = _read(facts, GLOBAL_PATH, "json")
        if not src.usable:
            return None
        try:
            data = _load(src)
        except Exception:
            return None
        v = data.get("installMethod") if isinstance(data, dict) else None
        if isinstance(v, str) and v:
            return f"claude ({v})" if v not in ("unknown", "global") else None
        return None

    def discover_config(self, facts) -> list:
        sources = [
            _read(facts, SYSTEM_SETTINGS_PATH, "json", scope="system", kind="policy"),
            _read(facts, SETTINGS_PATH, "json", scope="user", kind="config"),
            _read(facts, GLOBAL_PATH, "json", scope="user", kind="state"),
            _read(facts, LOCAL_SETTINGS_PATH, "json", scope="user", kind="config"),
        ]
        for root in self.project_roots(facts):
            for rel in self.project_files:
                path = root.rstrip("/") + "/" + rel
                kind = "mcp" if rel.endswith(".mcp.json") else "config"
                sources.append(
                    _read(facts, path, "json", scope="project", kind=kind)
                )
        return sources

    def parse_config(self, facts, sources) -> ParsedConfig:
        cfg = ParsedConfig(resolution="declared")
        by_path = {s.path: s for s in sources}
        settings_src = by_path.get(SETTINGS_PATH)
        global_src = by_path.get(GLOBAL_PATH)

        # ---- ordered scopes: system policy < user < project -------------
        # Local settings sit between the user file and project files. Project
        # files are not really ordered against each other (they belong to
        # different repositories), so they merge in a stable path order.
        ordered = [src for src in sources if src.scope == "project" and src.kind == "config"]
        ordered.sort(key=lambda s: s.path)
        local_src = by_path.get(LOCAL_SETTINGS_PATH)
        if local_src and local_src.usable:
            ordered.insert(0, local_src)
        if settings_src and settings_src.usable:
            ordered.insert(0, settings_src)
        system_src = by_path.get(SYSTEM_SETTINGS_PATH)
        if system_src and system_src.usable:
            ordered.insert(0, system_src)

        merged_env: dict = {}
        merged_settings: dict = {}
        for src in ordered:
            if not src.usable:
                continue
            try:
                data = _load(src)
            except Exception as exc:
                cfg.warnings.append(f"{src.path}: {type(exc).__name__}: {exc}"[:300])
                continue
            if not isinstance(data, dict):
                continue
            env = data.get("env")
            if isinstance(env, dict):
                merged_env.update(env)
            for key in SETTING_KEYS:
                if key in data and key != "model":
                    merged_settings[key] = data[key]
            hooks = data.get("hooks")
            if isinstance(hooks, dict) and hooks:
                merged_settings["hooks"] = sorted(hooks.keys())[:20]

        if merged_env:
            for key in MODEL_ENV_KEYS:
                if merged_env.get(key):
                    cfg.model = str(merged_env[key])
                    break
            if merged_env.get("ANTHROPIC_BASE_URL"):
                cfg.base_url, cfg.base_url_masked = _mask_endpoint(
                    merged_env["ANTHROPIC_BASE_URL"]
                )
                cfg.model_provider = "custom"
            else:
                cfg.model_provider = "anthropic"
            cfg.settings["env_keys"] = sorted(scrub_env(merged_env).keys())[:40]
            cfg.sanitized["env_keys"] = cfg.settings["env_keys"]
        else:
            cfg.model_provider = "anthropic"

        cfg.settings.update(merged_settings)
        if settings_src and settings_src.usable:
            try:
                data = _load(settings_src)
                if isinstance(data, dict):
                    dropped = [
                        k for k in data.keys() if k not in SETTING_KEYS and k != "env"
                    ]
                    cfg.unsupported_fields = sorted(set(str(x) for x in dropped))[:60]
            except Exception:
                pass
        elif settings_src:
            cfg.warnings.append(f"{SETTINGS_PATH}: {settings_src.status}")

        # ---- MCP servers: user scope first, project .mcp.json overrides ---
        if global_src and global_src.usable:
            try:
                g = _load(global_src)
                servers = g.get("mcpServers") if isinstance(g, dict) else None
                if isinstance(servers, dict):
                    for name, entry in list(servers.items())[:60]:
                        cfg.mcp_servers.append(
                            _mcp_common(entry, name)
                            if isinstance(entry, dict)
                            else {"name": name}
                        )
                projects = g.get("projects") if isinstance(g, dict) else None
                if isinstance(projects, dict) and projects:
                    project_servers = set()
                    for p in projects.values():
                        if isinstance(p, dict) and isinstance(p.get("mcpServers"), dict):
                            project_servers.update(p["mcpServers"].keys())
                    cfg.settings["project_scoped_mcp_servers"] = sorted(project_servers)[:60]
                    cfg.settings["project_count"] = len(projects)
                    cfg.warnings.append(
                        "per-project MCP servers recorded in ~/.claude.json are listed "
                        "but not merged into the user-scope list"
                    )
            except Exception as exc:
                cfg.warnings.append(f"{GLOBAL_PATH}: {type(exc).__name__}: {exc}"[:300])
        elif global_src:
            cfg.warnings.append(f"{GLOBAL_PATH}: {global_src.status}")

        project_mcp = 0
        for src in sources:
            if src.scope != "project" or src.kind != "mcp" or not src.usable:
                continue
            try:
                data = _load(src)
            except Exception as exc:
                cfg.warnings.append(f"{src.path}: {type(exc).__name__}: {exc}"[:300])
                continue
            entries = data.get("mcpServers") if isinstance(data, dict) else None
            if not isinstance(entries, dict):
                continue
            project_mcp += 1
            for name, entry in list(entries.items())[:60]:
                item = _mcp_common(entry, name) if isinstance(entry, dict) else {"name": name}
                item["scope"] = "project"
                item["source"] = src.path
                cfg.mcp_servers.append(item)
        if project_mcp:
            cfg.settings["project_mcp_files"] = project_mcp

        cfg.skills = _skills_from_dir(facts, "~/.claude/skills")
        plugins = facts.ls.get("~/.claude/plugins", [])
        if plugins:
            cfg.settings["plugins"] = [
                n for n, kind in plugins if kind == "d" and not n.startswith(".") 
            ][:40]

        # ---- status and resolution -------------------------------------
        # Any readable file counts, including ~/.claude.json (MCP servers),
        # project settings and the managed policy, all merged above. Unreadable
        # scopes must surface as errors, not "empty", or a broken scan looks
        # like an absent config and the persist layer drops the last good one.
        # resolution_for owns the "which statuses mean unreadable" rule.
        _, unresolved = self.resolution_for(sources)
        any_usable = any(src.usable for src in sources)
        if not any_usable:
            cfg.parse_status = "error" if unresolved else "empty"
        elif any(
            src is not None and src.status not in ("ok", "missing", "empty", "not_probed")
            for src in (settings_src, by_path.get(LOCAL_SETTINGS_PATH))
        ):
            # The user settings file holds model/base_url and is unreadable,
            # while something else (~/.claude.json) is still readable. The MCP
            # list alone must not make this look like a clean parse: persist
            # would replace the last good config with the near-empty merge.
            cfg.parse_status = "partial"
        elif cfg.model or cfg.mcp_servers or cfg.settings:
            cfg.parse_status = "parsed"
        else:
            cfg.parse_status = "partial"

        apply_scope(cfg, self, sources, self.project_roots(facts))
        cfg.sanitized.update(
            {
                "agent": self.agent_id,
                "resolution": cfg.resolution,
                "scopes": cfg.settings.get("scopes"),
                "config_file": SETTINGS_PATH,
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
        )
        return cfg
