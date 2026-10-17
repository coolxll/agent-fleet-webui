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
    apply_scope,
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

    PRECEDENCE_CHAIN = [
        ("system", "/etc/opencode/opencode.json"),
        ("user", JSON_PATH),
        ("project", "PROJECT/opencode.json"),
        ("project", "PROJECT/.opencode/opencode.json"),
    ]
    system_paths = ["/etc/opencode/opencode.json"]
    project_files = ["opencode.json", ".opencode/opencode.json"]

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
        sources = [
            _read(facts, "/etc/opencode/opencode.json", "json", scope="system", kind="policy"),
            _read(facts, JSON_PATH, "json", scope="user", kind="config"),
            _read(facts, JSONC_PATH, "jsonc", scope="user", kind="config"),
        ]
        for root in self.project_roots(facts):
            for rel in self.project_files:
                sources.append(
                    _read(
                        facts, root.rstrip("/") + "/" + rel, "json", scope="project", kind="config"
                    )
                )
        return sources

    def project_roots(self, facts) -> list:
        """OpenCode keeps no project registry; the workspace roots it has been
        run in are the best available signal."""
        src = _read(facts, JSON_PATH, "json")
        if not src.usable:
            return []
        try:
            data = _load(src)
        except Exception:
            return []
        if not isinstance(data, dict):
            return []
        roots = data.get("workspaceRoots") or data.get("projects")
        if isinstance(roots, dict):
            return [str(p) for p in roots.keys()][:5]
        if isinstance(roots, list):
            return [str(p) for p in roots][:5]
        return []

    def parse_config(self, facts, sources) -> ParsedConfig:
        cfg = ParsedConfig(resolution="declared")
        cfg.skills = _skills_from_dir(facts, "~/.config/opencode/skills")
        plugins = facts.ls.get("~/.config/opencode/plugins", [])
        if plugins:
            cfg.settings["plugins"] = [n for n, kind in plugins if kind == "d"][:40]

        usable = [s for s in sources if s.usable]
        if not usable:
            states = {s.path: s.status for s in sources}
            probed = [st for st in states.values() if st not in ("missing", "not_probed")]
            if not probed:
                cfg.parse_status = "empty"
            else:
                cfg.parse_status = "error"
                for p, st in states.items():
                    if st not in ("missing", "not_probed"):
                        cfg.warnings.append(f"{p}: {st}")
            apply_scope(cfg, self, sources, self.project_roots(facts))
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
            apply_scope(cfg, self, sources, self.project_roots(facts))
            cfg.sanitized = self._view(cfg)
            return cfg

        if not isinstance(data, dict):
            cfg.parse_status = "error"
            cfg.warnings.append(f"{used.path}: root is not an object")
            apply_scope(cfg, self, sources, self.project_roots(facts))
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
        # providers.<name>.options.baseURL is the network endpoint in use
        providers = data.get("provider")
        if isinstance(providers, dict):
            active = None
            if isinstance(cfg.model_provider, str) and cfg.model_provider in providers:
                active = providers[cfg.model_provider]
            elif len(providers) == 1:
                active = list(providers.values())[0]
            opts = active.get("options") if isinstance(active, dict) else None
            if isinstance(opts, dict) and isinstance(opts.get("baseURL"), str):
                cfg.base_url, cfg.base_url_masked = _mask_endpoint(opts["baseURL"])
            cfg.settings["providers"] = sorted(providers.keys())[:40]

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
        apply_scope(cfg, self, sources, self.project_roots(facts))
        cfg.sanitized = self._view(cfg)
        return cfg

    def _view(self, cfg):
        return {
            "agent": self.agent_id,
            "scope": "user",
            "resolution": cfg.resolution,
            "scopes": cfg.settings.get("scopes"),
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
