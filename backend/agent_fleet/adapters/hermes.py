"""Hermes Agent adapter.

~/.hermes/config.yaml is the user config. ~/.hermes/.env holds live
credentials and is deliberately never requested.
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
    apply_scope,
    extract_version,
    guess_install_method,
)
from agent_fleet.adapters.sanitize import is_secret_key
from agent_fleet.remotes.probe import BinProbe

CONFIG_PATH = "~/.hermes/config.yaml"
SETTING_KEYS = (
    "max_live_sessions",
    "file_read_max_chars",
    "timezone",
    "mcp_discovery_timeout",
    "context",
    "memory",
    "display",
    "terminal",
)


class HermesAdapter(AgentAdapter):
    agent_id = "hermes"
    display_name = "Hermes Agent"
    supports_config = True

    PRECEDENCE_CHAIN = [
        ("system", "/etc/hermes/config.yaml"),
        ("user", CONFIG_PATH),
    ]
    system_paths = ["/etc/hermes/config.yaml"]

    def probe_spec(self) -> ProbeSpec:
        return ProbeSpec(
            bins=[
                BinProbe(
                    key="hermes:hermes",
                    binary="hermes",
                    version_args=["--version"],
                    candidates=["~/.hermes/bin/hermes", "~/.local/bin/hermes"],
                )
            ],
            files=[CONFIG_PATH],
        )

    def detect(self, facts) -> Detection:
        key = "hermes:hermes"
        path = facts.bin_path(key)
        rc, out = facts.version(key)
        raw = out.strip()
        if not path:
            if key not in facts.probed:
                return Detection(status="unknown", error="no probe data for hermes")
            return Detection(status="not_installed")
        return Detection(
            status="installed",
            detected=True,
            version=extract_version(raw) if rc == 0 else None,
            version_raw=raw or None,
            exec_path=path,
            install_method=guess_install_method(path),
            config_dir="~/.hermes",
            error=None if rc == 0 else f"version command exited {rc}: {raw[:200]}",
        )

    def discover_config(self, facts) -> list:
        return [
            _read(facts, "/etc/hermes/config.yaml", "yaml", scope="system", kind="policy"),
            _read(facts, CONFIG_PATH, "yaml", scope="user", kind="config"),
        ]

    def parse_config(self, facts, sources) -> ParsedConfig:
        cfg = ParsedConfig(resolution="declared")
        cfg.warnings.append("~/.hermes/.env is never read (contains credentials)")
        src = next((s for s in sources if s.path == CONFIG_PATH), None)
        if not src or not src.usable:
            if src:
                cfg.warnings.append(f"{CONFIG_PATH}: {src.status}")
            cfg.parse_status = "empty" if (src and src.status == "missing") else "error"
            apply_scope(cfg, self, sources)
            cfg.sanitized = self._view(cfg)
            return cfg
        try:
            data = _load(src)
        except Exception as exc:
            cfg.parse_status = "error"
            cfg.warnings.append(f"YAML parse error: {type(exc).__name__}: {exc}"[:300])
            apply_scope(cfg, self, sources)
            cfg.sanitized = self._view(cfg)
            return cfg
        if not isinstance(data, dict):
            cfg.parse_status = "error"
            cfg.warnings.append(f"{CONFIG_PATH}: root is not a mapping")
            cfg.sanitized = self._view(cfg)
            return cfg

        model_block = data.get("model")
        if isinstance(model_block, dict):
            cfg.model = model_block.get("default")
            cfg.model_provider = model_block.get("provider")
            if model_block.get("key_env"):
                cfg.settings["key_env"] = model_block["key_env"]
            if model_block.get("aliases"):
                cfg.settings["model_aliases"] = sorted(model_block["aliases"].keys())[:40]

        providers = data.get("providers")
        if isinstance(providers, dict):
            names = []
            for name, entry in list(providers.items())[:60]:
                names.append(name)
                if name == cfg.model_provider and isinstance(entry, dict):
                    if entry.get("base_url"):
                        cfg.base_url, cfg.base_url_masked = _mask_endpoint(entry["base_url"])
                    if entry.get("key_env"):
                        cfg.settings["key_env"] = entry["key_env"]
            cfg.settings["providers"] = names
            if any(isinstance(e, dict) and e.get("api_key") for e in providers.values()):
                cfg.warnings.append("provider entries contain inline api_key fields; values not read")

        mcp = data.get("mcp_servers")
        if isinstance(mcp, dict):
            for name, entry in list(mcp.items())[:60]:
                cfg.mcp_servers.append(
                    _mcp_common(entry, name) if isinstance(entry, dict) else {"name": name}
                )
        elif isinstance(mcp, list):
            for entry in mcp[:60]:
                if isinstance(entry, dict):
                    cfg.mcp_servers.append(_mcp_common(entry, entry.get("name", "?")))

        for k in SETTING_KEYS:
            if k in data and isinstance(data[k], (str, int, float, bool)):
                cfg.settings[k] = data[k]
        toolsets = data.get("toolsets")
        if isinstance(toolsets, list):
            cfg.settings["toolsets"] = [str(t)[:40] for t in toolsets[:40]]
        if isinstance(data.get("skills"), dict):
            cfg.settings["skills_configured"] = True

        known = set(SETTING_KEYS) | {
            "model",
            "providers",
            "mcp_servers",
            "skills",
            "toolsets",
            "fallback_providers",
        }
        cfg.unsupported_fields = sorted(set(str(k) for k in data.keys() if k not in known))[:60]

        cfg.parse_status = "parsed" if (cfg.model or cfg.mcp_servers) else "partial"
        apply_scope(cfg, self, sources)
        cfg.sanitized = self._view(cfg)
        return cfg

    def _view(self, cfg):
        return {
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
