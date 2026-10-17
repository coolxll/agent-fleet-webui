"""Codex adapter.

Config lives in ~/.codex/config.toml (TOML). Real installs keep secrets there
(experimental_bearer_token, MCP env blocks), so every emitted field is
whitelisted and unknown keys are reported by name only.
"""

from __future__ import annotations

from agent_fleet.adapters.base import (
    AgentAdapter,
    Detection,
    ParsedConfig,
    ProbeSpec,
    SourceInfo,
    _load,
    _mask_endpoint,
    _mcp_common,
    _read,
    _skills_from_dir,
    apply_scope,
    extract_version,
    guess_install_method,
)
from agent_fleet.adapters.sanitize import is_secret_key
from agent_fleet.remotes.probe import BinProbe

CONFIG_PATH = "~/.codex/config.toml"
SETTING_KEYS = (
    "model",
    "model_provider",
    "model_reasoning_effort",
    "model_reasoning_summary",
    "approval_policy",
    "sandbox_mode",
    "sandbox_workspace_write",
    "wire_api",
    "service_tier",
    "personality",
    "tool_output_token_limit",
)


class CodexAdapter(AgentAdapter):
    agent_id = "codex"
    display_name = "Codex CLI"
    supports_config = True

    PRECEDENCE_CHAIN = [
        ("system", "/etc/codex/config.toml"),
        ("user", CONFIG_PATH),
    ]
    system_paths = ["/etc/codex/config.toml"]
    # Codex keeps project-level state inside the user config's [projects] table.
    project_files: list = []

    def probe_spec(self) -> ProbeSpec:
        return ProbeSpec(
            bins=[BinProbe(key="codex:codex", binary="codex", version_args=["--version"])],
            files=[CONFIG_PATH, "~/.codex/AGENTS.md"],
            skill_dirs=["~/.codex/skills"],
        )

    def detect(self, facts) -> Detection:
        key = "codex:codex"
        path = facts.bin_path(key)
        rc, out = facts.version(key)
        raw = out.strip()
        if not path:
            if key not in facts.probed:
                return Detection(status="unknown", error="no probe data for codex")
            return Detection(status="not_installed")
        version = extract_version(raw) if rc == 0 else None
        cfg = _read(facts, CONFIG_PATH, "toml")
        return Detection(
            status="installed",
            detected=True,
            version=version,
            version_raw=raw or None,
            exec_path=path,
            install_method=guess_install_method(path),
            config_dir="~/.codex",
            error=None if rc == 0 else f"version command exited {rc}: {raw[:200]}",
        )

    def discover_config(self, facts) -> list:
        return [
            _read(facts, "/etc/codex/config.toml", "toml", scope="system", kind="policy"),
            _read(facts, CONFIG_PATH, "toml", scope="user", kind="config"),
            _read(facts, "~/.codex/AGENTS.md", "text", scope="user", kind="instructions"),
        ]

    def project_roots(self, facts) -> list:
        """Codex stores project trust in the user config, not on disk."""
        src = _read(facts, CONFIG_PATH, "toml")
        if not src.usable:
            return []
        try:
            data = _load(src)
        except Exception:
            return []
        projects = data.get("projects") if isinstance(data, dict) else None
        if not isinstance(projects, dict):
            return []
        return [str(p) for p in projects.keys() if isinstance(p, str)][:5]

    def parse_config(self, facts, sources) -> ParsedConfig:
        primary = None
        for s in sources:
            if s.path == CONFIG_PATH:
                primary = s
                break
        cfg = ParsedConfig(resolution="declared")
        if primary is None or not primary.usable:
            cfg.parse_status = "empty" if (primary and primary.status == "missing") else "error"
            cfg.warnings.append(
                f"codex config not readable ({primary.status if primary else 'absent'})"
            )
            return apply_scope(cfg, self, sources)

        try:
            data = _load(primary)
        except Exception as exc:
            cfg.parse_status = "error"
            cfg.warnings.append(f"TOML parse error: {type(exc).__name__}: {exc}"[:300])
            return apply_scope(cfg, self, sources)
        if not isinstance(data, dict):
            cfg.parse_status = "error"
            cfg.warnings.append("TOML root is not a table")
            return apply_scope(cfg, self, sources)

        settings, dropped = {}, []
        for k, v in data.items():
            if k in SETTING_KEYS:
                settings[k] = v
            elif k in ("mcp_servers", "skills", "model_providers", "projects"):
                continue
            else:
                dropped.append(k)
        cfg.unsupported_fields = sorted(set(dropped))[:60]
        cfg.settings = {
            k: v for k, v in settings.items() if isinstance(v, (str, int, float, bool)) and not is_secret_key(k)
        }
        cfg.model = settings.get("model")
        cfg.model_provider = settings.get("model_provider")

        # Providers table: only the active one contributes a network endpoint.
        providers = data.get("model_providers") or {}
        if isinstance(providers, dict) and providers:
            active = None
            if cfg.model_provider and cfg.model_provider in providers:
                active = providers[cfg.model_provider]
            elif len(providers) == 1:
                active = list(providers.values())[0]
            if isinstance(active, dict) and active.get("base_url"):
                cfg.base_url, cfg.base_url_masked = _mask_endpoint(active.get("base_url"))

        servers = data.get("mcp_servers") or {}
        if isinstance(servers, dict):
            for name, entry in list(servers.items())[:60]:
                if isinstance(entry, dict):
                    cfg.mcp_servers.append(_mcp_common(entry, name))
                else:
                    cfg.mcp_servers.append({"name": name, "transport": "unknown"})

        skills_cfg = (data.get("skills") or {}).get("config") or []
        if isinstance(skills_cfg, list):
            for s in skills_cfg:
                if isinstance(s, dict) and s.get("path"):
                    p = str(s["path"])
                    parts = [x for x in p.split("/") if x]
                    name = parts[-1] if parts else p
                    if name.lower() == "skill.md" and len(parts) >= 2:
                        name = parts[-2]
                    cfg.skills.append(
                        {
                            "name": name,
                            "path": p,
                            "enabled": bool(s.get("enabled", True)),
                        }
                    )
        for s in _skills_from_dir(facts, "~/.codex/skills"):
            if not any(x["name"] == s["name"] for x in cfg.skills):
                cfg.skills.append(s)

        # project trust entries are names only (paths of directories)
        projects = data.get("projects") or {}
        if isinstance(projects, dict) and projects:
            cfg.settings["projects"] = sorted(projects.keys())[:40]
            cfg.settings["trusted_projects"] = sum(
                1 for v in projects.values()
                if isinstance(v, dict) and v.get("trust_level") == "trusted"
            )

        if "experimental_bearer_token" in data:
            cfg.warnings.append("config contains a bearer token field; value not read")
        cfg.parse_status = "parsed" if cfg.settings or cfg.mcp_servers or cfg.skills else "partial"
        apply_scope(cfg, self, sources)
        cfg.sanitized = {
            "agent": self.agent_id,
            "scope": "user",
            "resolution": cfg.resolution,
            "scopes": cfg.settings.get("scopes"),
            "config_file": CONFIG_PATH,
            "model": cfg.model,
            "provider": cfg.model_provider,
            "network": {"base_url": cfg.base_url, "masked": cfg.base_url_masked},
            "settings": cfg.settings,
            "mcp_servers": cfg.mcp_servers,
            "skills": cfg.skills,
            "unsupported_fields": cfg.unsupported_fields,
            "warnings": cfg.warnings,
        }
        return cfg
