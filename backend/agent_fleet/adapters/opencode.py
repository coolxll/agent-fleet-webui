"""OpenCode adapter.

Config is XDG based: ~/.config/opencode/opencode.json (or .jsonc).
Skills live in ~/.config/opencode/skills.
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
from agent_fleet.adapters.sanitize import is_secret_key, strip_unknown
from agent_fleet.remotes.probe import BinProbe

JSON_PATH = "~/.config/opencode/opencode.json"
JSONC_PATH = "~/.config/opencode/opencode.jsonc"
SETTING_KEYS = ("model", "provider", "theme", "autoupdate", "default_agent", "agent")


class OpenCodeAdapter(AgentAdapter):
    agent_id = "opencode"
    display_name = "OpenCode"
    supports_config = True

    def probe_spec(self) -> ProbeSpec:
        return ProbeSpec(
            bins=[
                BinProbe(
                    key="opencode:opencode",
                    binary="opencode",
                    version_args=["--version"],
                    candidates=["~/.opencode/bin/opencode", "~/.local/bin/opencode"],
                )
            ],
            files=[JSON_PATH, JSONC_PATH],
            dirs=["~/.config/opencode/plugins"],
            skill_dirs=["~/.config/opencode/skills"],
        )

    def detect(self, facts) -> Detection:
        key = "opencode:opencode"
        path = facts.bin_path(key)
        rc, out = facts.version(key)
        raw = out.strip()
        if not path:
            if key not in facts.probed:
                return Detection(status="unknown", error="no probe data for opencode")
            return Detection(status="not_installed")
        return Detection(
            status="installed",
            detected=True,
            version=extract_version(raw) if rc == 0 else None,
            version_raw=raw or None,
            exec_path=path,
            install_method=guess_install_method(path),
            config_dir="~/.config/opencode",
            error=None if rc == 0 else f"version command exited {rc}: {raw[:200]}",
        )

    def discover_config(self, facts) -> list:
        return [
            _read(facts, JSON_PATH, "json", scope="user", kind="config"),
            _read(facts, JSONC_PATH, "jsonc", scope="user", kind="config"),
        ]

    def parse_config(self, facts, sources) -> ParsedConfig:
        cfg = ParsedConfig(resolution="declared")
        cfg.skills = _skills_from_dir(facts, "~/.config/opencode/skills")
        plugins = facts.ls.get("~/.config/opencode/plugins", [])
        if plugins:
            cfg.settings["plugins"] = [n for n, kind in plugins if kind == "d"][:40]

        usable = [s for s in sources if s.usable]
        if not usable:
            states = {s.path: s.status for s in sources}
            if all(v == "missing" for v in states.values()):
                cfg.parse_status = "empty"
            else:
                cfg.parse_status = "error"
            for p, st in states.items():
                if st != "missing":
                    cfg.warnings.append(f"{p}: {st}")
            cfg.sanitized = self._view(cfg)
            return cfg

        data = None
        used = None
        for s in usable:
            try:
                data = _load(s)
                used = s
                break
            except Exception as exc:
                cfg.warnings.append(f"{s.path}: {type(exc).__name__}: {exc}"[:300])
        if data is None:
            cfg.parse_status = "error"
            cfg.sanitized = self._view(cfg)
            return cfg

        if not isinstance(data, dict):
            cfg.parse_status = "error"
            cfg.warnings.append(f"{used.path}: root is not an object")
            cfg.sanitized = self._view(cfg)
            return cfg

        settings, dropped = strip_unknown(
            data, [k for k in SETTING_KEYS] + ["mcp", "skills", "$schema", "instructions"]
        )
        cfg.unsupported_fields = [d for d in dropped if d not in ("$schema",)][:60]
        cfg.settings = {k: v for k, v in settings.items() if not is_secret_key(k)}

        model = data.get("model")
        if isinstance(model, str) and model:
            cfg.model = model
            if "/" in model:
                cfg.model_provider = model.split("/", 1)[0]
            elif isinstance(data.get("provider"), str):
                cfg.model_provider = data["provider"]
        if isinstance(data.get("provider"), str) and data["provider"]:
            cfg.model_provider = data["provider"]

        mcp = data.get("mcp")
        if isinstance(mcp, dict):
            for name, entry in list(mcp.items())[:60]:
                cfg.mcp_servers.append(
                    _mcp_common(entry, name) if isinstance(entry, dict) else {"name": name}
                )
        elif isinstance(mcp, list):
            for entry in mcp[:60]:
                if isinstance(entry, dict):
                    cfg.mcp_servers.append(_mcp_common(entry, entry.get("name", "?")))

        for s in _skills_from_dir(facts, "~/.config/opencode/skills"):
            if not any(x["name"] == s["name"] for x in cfg.skills):
                cfg.skills.append(s)

        agents = data.get("agent")
        if isinstance(agents, dict) and agents:
            cfg.settings["agents"] = sorted(agents.keys())[:30]

        cfg.parse_status = "parsed" if (cfg.model or cfg.mcp_servers or cfg.skills or cfg.settings) else "empty"
        cfg.sanitized = self._view(cfg)
        return cfg

    def _view(self, cfg):
        return {
            "agent": self.agent_id,
            "scope": "user",
            "config_file": JSON_PATH,
            "model": cfg.model,
            "provider": cfg.model_provider,
            "network": {"base_url": cfg.base_url, "masked": cfg.base_url_masked},
            "settings": cfg.settings,
            "mcp_servers": cfg.mcp_servers,
            "skills": cfg.skills,
            "unsupported_fields": cfg.unsupported_fields,
            "warnings": cfg.warnings,
        }
