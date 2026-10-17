"""Pi Coding Agent adapter.

Pi keeps user state under ~/.pi/agent:
  settings.json : defaultProvider / defaultModel
  models.json   : provider catalog with baseUrl entries

~/.pi/agent/auth.json holds credentials and is deliberately never requested.
"""

from __future__ import annotations

from agent_fleet.adapters.base import (
    AgentAdapter,
    Detection,
    ParsedConfig,
    ProbeSpec,
    _load,
    _mask_endpoint,
    _read,
    apply_scope,
    extract_version,
    guess_install_method,
)
from agent_fleet.adapters.sanitize import is_secret_key
from agent_fleet.remotes.probe import BinProbe

SETTINGS_PATH = "~/.pi/agent/settings.json"
MODELS_PATH = "~/.pi/agent/models.json"


class PiAdapter(AgentAdapter):
    agent_id = "pi"
    display_name = "Pi Coding Agent"
    supports_config = True

    PRECEDENCE_CHAIN = [
        ("system", "/etc/pi/agent/settings.json"),
        ("user", SETTINGS_PATH),
    ]
    system_paths = ["/etc/pi/agent/settings.json"]

    def probe_spec(self) -> ProbeSpec:
        return ProbeSpec(
            bins=[BinProbe(key="pi:pi", binary="pi", version_args=["--version"])],
            files=[SETTINGS_PATH, MODELS_PATH],
        )

    def detect(self, facts) -> Detection:
        key = "pi:pi"
        path = facts.bin_path(key)
        rc, out = facts.version(key)
        raw = out.strip()
        if not path:
            if key not in facts.probed:
                return Detection(status="unknown", error="no probe data for pi")
            return Detection(status="not_installed")
        return Detection(
            status="installed",
            detected=True,
            version=extract_version(raw) if rc == 0 else None,
            version_raw=raw or None,
            exec_path=path,
            install_method=guess_install_method(path),
            config_dir="~/.pi/agent",
            error=None if rc == 0 else f"version command exited {rc}: {raw[:200]}",
        )

    def discover_config(self, facts) -> list:
        return [
            _read(facts, "/etc/pi/agent/settings.json", "json", scope="system", kind="policy"),
            _read(facts, SETTINGS_PATH, "json", scope="user", kind="config"),
            _read(facts, MODELS_PATH, "json", scope="user", kind="catalog"),
        ]

    def parse_config(self, facts, sources) -> ParsedConfig:
        cfg = ParsedConfig(resolution="declared")
        cfg.warnings.append("~/.pi/agent/auth.json is never read (contains credentials)")
        by_path = {s.path: s for s in sources}
        s_src, m_src = by_path.get(SETTINGS_PATH), by_path.get(MODELS_PATH)

        if s_src and s_src.usable:
            try:
                data = _load(s_src)
                if isinstance(data, dict):
                    cfg.model = data.get("defaultModel")
                    cfg.model_provider = data.get("defaultProvider")
                    cfg.settings = {
                        k: v
                        for k, v in data.items()
                        if not is_secret_key(k) and isinstance(v, (str, int, float, bool))
                    }
            except Exception as exc:
                cfg.warnings.append(f"{SETTINGS_PATH}: {type(exc).__name__}: {exc}"[:300])
        elif s_src:
            cfg.warnings.append(f"{SETTINGS_PATH}: {s_src.status}")

        if m_src and m_src.usable:
            try:
                data = _load(m_src)
                providers = data.get("providers") if isinstance(data, dict) else None
                if isinstance(providers, dict):
                    names = []
                    for name, entry in list(providers.items())[:60]:
                        names.append(name)
                        if name == cfg.model_provider and isinstance(entry, dict):
                            if entry.get("baseUrl"):
                                cfg.base_url, cfg.base_url_masked = _mask_endpoint(entry["baseUrl"])
                            cfg.settings["api_type"] = entry.get("api")
                            models = entry.get("models")
                            if isinstance(models, list):
                                cfg.settings["declared_models"] = len(models)
                    cfg.settings["providers"] = names
            except Exception as exc:
                cfg.warnings.append(f"{MODELS_PATH}: {type(exc).__name__}: {exc}"[:300])
        elif m_src:
            cfg.warnings.append(f"{MODELS_PATH}: {m_src.status}")

        cfg.unsupported_fields = ["mcp_servers", "skills"] if not cfg.mcp_servers else []
        apply_scope(cfg, self, sources)
        cfg.parse_status = "parsed" if (cfg.model or cfg.model_provider) else (
            "empty" if any(s.status == "missing" for s in sources) else "partial"
        )
        cfg.sanitized = {
            "agent": self.agent_id,
            "scope": "user",
            "resolution": cfg.resolution,
            "scopes": cfg.settings.get("scopes"),
            "config_file": SETTINGS_PATH,
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
