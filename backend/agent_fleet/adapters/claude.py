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

MODEL_ENV_KEYS = (
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL",
)
SETTING_KEYS = ("effortLevel", "model", "permissions", "enableAllProjectMcpServers", "theme", "language")


class ClaudeAdapter(AgentAdapter):
    agent_id = "claude"
    display_name = "Claude Code"
    supports_config = True

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
        return [
            _read(facts, SETTINGS_PATH, "json", scope="user", kind="config"),
            _read(facts, GLOBAL_PATH, "json", scope="user", kind="state"),
            _read(facts, LOCAL_SETTINGS_PATH, "json", scope="user", kind="config"),
        ]

    def parse_config(self, facts, sources) -> ParsedConfig:
        cfg = ParsedConfig(resolution="declared")
        by_path = {s.path: s for s in sources}
        settings_src = by_path.get(SETTINGS_PATH)
        global_src = by_path.get(GLOBAL_PATH)

        env = {}
        if settings_src and settings_src.usable:
            try:
                data = _load(settings_src)
                e = data.get("env") if isinstance(data, dict) else None
                if isinstance(e, dict):
                    env = e
                if isinstance(data, dict):
                    for k in SETTING_KEYS:
                        if k in data and k != "model":
                            cfg.settings[k] = data[k] if not is_secret_key(k) else None
                    dropped = [k for k in data.keys() if k not in SETTING_KEYS and k != "env"]
                    cfg.unsupported_fields = sorted(set(str(x) for x in dropped))[:60]
                    hooks = data.get("hooks")
                    if isinstance(hooks, dict) and hooks:
                        cfg.settings["hooks"] = sorted(hooks.keys())[:20]
            except Exception as exc:
                cfg.warnings.append(f"{SETTINGS_PATH}: {type(exc).__name__}: {exc}"[:300])
                cfg.parse_status = "partial"
        elif settings_src:
            cfg.warnings.append(f"{SETTINGS_PATH}: {settings_src.status}")
            if settings_src.status == "missing":
                cfg.parse_status = "empty"

        for key in MODEL_ENV_KEYS:
            if env.get(key):
                cfg.model = str(env[key])
                break
        if env.get("ANTHROPIC_BASE_URL"):
            cfg.base_url, cfg.base_url_masked = _mask_endpoint(env["ANTHROPIC_BASE_URL"])
            cfg.model_provider = "custom"
        else:
            cfg.model_provider = "anthropic"
        if env:
            cfg.sanitized["_env_keys"] = sorted(scrub_env(env).keys())
            cfg.settings["env_keys"] = sorted(scrub_env(env).keys())[:40]

        if global_src and global_src.usable:
            try:
                g = _load(global_src)
                servers = g.get("mcpServers") if isinstance(g, dict) else None
                if isinstance(servers, dict):
                    for name, entry in list(servers.items())[:60]:
                        cfg.mcp_servers.append(
                            _mcp_common(entry, name) if isinstance(entry, dict) else {"name": name}
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
                        "per-project MCP servers are not merged into the user-scope list"
                    )
            except Exception as exc:
                cfg.warnings.append(f"{GLOBAL_PATH}: {type(exc).__name__}: {exc}"[:300])
        elif global_src:
            cfg.warnings.append(f"{GLOBAL_PATH}: {global_src.status}")

        cfg.skills = _skills_from_dir(facts, "~/.claude/skills")
        plugins = facts.ls.get("~/.claude/plugins", [])
        if plugins:
            cfg.settings["plugins"] = [
                n for n, kind in plugins if kind == "d" and not n.startswith(".") 
            ][:40]

        if cfg.parse_status == "empty" and (cfg.mcp_servers or cfg.model):
            cfg.parse_status = "partial"
        elif cfg.model or cfg.mcp_servers or cfg.skills:
            cfg.parse_status = "parsed"
        cfg.sanitized.update(
            {
                "agent": self.agent_id,
                "scope": "user",
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
