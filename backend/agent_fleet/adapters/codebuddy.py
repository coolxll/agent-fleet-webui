"""CodeBuddy adapter.

Verified on a real machine (codebuddy 2.168.2). CodeBuddy is a
Claude-Code-compatible CLI, so the layout is familiar:

  binary      ~/.nvm/versions/node/<v>/bin/codebuddy
  version     codebuddy --version  ->  "2.168.2"
  settings    ~/.codebuddy/settings.json
              top-level keys seen: model, hooks (7 event types),
              trustedDirectories
  plugins     ~/.codebuddy/plugins/known_marketplaces.json and
              ~/.codebuddy/plugins/marketplaces/<name>/plugins/<plugin>/.mcp.json
  state       ~/.codebuddy/user-state.json (counters only, nothing sensitive)

There is no user-scope MCP file on this machine: MCP servers live in
project .mcp.json files, which the project-scope pass covers. Nothing is
invented when they are absent.
"""

from __future__ import annotations

from agent_fleet.adapters.base import (
    AgentAdapter,
    Detection,
    ParsedConfig,
    ProbeSpec,
    _load,
    _mcp_common,
    _read,
    _skills_from_dir,
    apply_scope,
    extract_version,
    guess_install_method,
)
from agent_fleet.adapters.sanitize import is_secret_key, scrub_env
from agent_fleet.remotes.probe import BinProbe

SETTINGS_PATH = "~/.codebuddy/settings.json"
MARKETPLACES_PATH = "~/.codebuddy/plugins/known_marketplaces.json"

SETTING_KEYS = ("model", "trustedDirectories", "autoUpdates", "theme", "effortLevel")


class CodeBuddyAdapter(AgentAdapter):
    agent_id = "codebuddy"
    display_name = "CodeBuddy"
    supports_config = True

    PRECEDENCE_CHAIN = [
        ("user", SETTINGS_PATH),
        ("project", "PROJECT/.codebuddy/settings.json"),
        ("project", "PROJECT/.mcp.json"),
    ]
    system_paths: list = []
    project_files = [".codebuddy/settings.json", ".mcp.json"]

    def probe_spec(self) -> ProbeSpec:
        return ProbeSpec(
            bins=[
                BinProbe(
                    key="codebuddy:codebuddy",
                    binary="codebuddy",
                    version_args=["--version"],
                    candidates=[
                        "~/.local/bin/codebuddy",
                        "/usr/local/bin/codebuddy",
                        "/usr/bin/codebuddy",
                    ],
                )
            ],
            files=[SETTINGS_PATH, MARKETPLACES_PATH],
            skill_dirs=["~/.codebuddy/skills"],
        )

    def detect(self, facts) -> Detection:
        key = "codebuddy:codebuddy"
        path = facts.bin_path(key)
        rc, out = facts.version(key)
        raw = out.strip()
        if not path:
            if key not in facts.probed:
                return Detection(status="unknown", error="no probe data for codebuddy")
            return Detection(status="not_installed")
        return Detection(
            status="installed",
            detected=True,
            version=extract_version(raw) if rc == 0 else None,
            version_raw=raw or None,
            exec_path=path,
            install_method=guess_install_method(path),
            config_dir="~/.codebuddy",
            error=None if rc == 0 else f"version command exited {rc}: {raw[:200]}",
        )

    def project_roots(self, facts) -> list:
        """Project roots come from trustedDirectories in settings.json.

        That is the list of directories the user has actually trusted, which is
        exactly where project-scope files (.mcp.json, .codebuddy/settings.json)
        can live.
        """
        src = _read(facts, SETTINGS_PATH, "json")
        if not src.usable:
            return []
        try:
            data = _load(src)
        except Exception:
            return []
        if not isinstance(data, dict):
            return []
        roots = data.get("trustedDirectories") or data.get("projects")
        if isinstance(roots, dict):
            return [str(p) for p in roots.keys()][:5]
        if isinstance(roots, list):
            return [str(p) for p in roots if isinstance(p, str)][:5]
        return []

    def discover_config(self, facts) -> list:
        sources = [
            _read(facts, SETTINGS_PATH, "json", scope="user", kind="config"),
            _read(facts, MARKETPLACES_PATH, "json", scope="user", kind="catalog"),
        ]
        for root in self.project_roots(facts):
            for rel in self.project_files:
                kind = "mcp" if rel.endswith(".mcp.json") else "config"
                sources.append(
                    _read(facts, root.rstrip("/") + "/" + rel, "json", scope="project", kind=kind)
                )
        return sources

    def parse_config(self, facts, sources) -> ParsedConfig:
        cfg = ParsedConfig(resolution="declared")
        by_path = {s.path: s for s in sources}
        settings_src = by_path.get(SETTINGS_PATH)

        env_keys: list = []
        settings_broken = False
        if settings_src and settings_src.usable:
            try:
                data = _load(settings_src)
            except Exception as exc:
                cfg.warnings.append(f"{SETTINGS_PATH}: {type(exc).__name__}: {exc}"[:300])
                data = None
                settings_broken = True
            if isinstance(data, dict):
                cfg.model = data.get("model") if isinstance(data.get("model"), str) else None
                dropped = [k for k in data.keys() if k not in SETTING_KEYS]
                cfg.unsupported_fields = sorted(set(str(x) for x in dropped))[:60]
                for key in SETTING_KEYS:
                    if key == "model" or key not in data:
                        continue
                    value = data[key]
                    if is_secret_key(key):
                        continue
                    if isinstance(value, (str, int, float, bool)):
                        cfg.settings[key] = value
                    elif isinstance(value, list):
                        cfg.settings[key] = len(value)
                hooks = data.get("hooks")
                if isinstance(hooks, dict) and hooks:
                    cfg.settings["hooks"] = sorted(hooks.keys())[:20]
                env = data.get("env")
                if isinstance(env, dict) and env:
                    env_keys = sorted(scrub_env(env).keys())[:40]
                    cfg.settings["env_keys"] = env_keys
        elif settings_src:
            cfg.warnings.append(f"{SETTINGS_PATH}: {settings_src.status}")

        market_src = by_path.get(MARKETPLACES_PATH)
        if market_src and market_src.usable:
            try:
                data = _load(market_src)
                if isinstance(data, dict):
                    cfg.settings["marketplaces"] = sorted(data.keys())[:20]
            except Exception as exc:
                cfg.warnings.append(f"{MARKETPLACES_PATH}: {type(exc).__name__}: {exc}"[:300])
        elif market_src:
            cfg.warnings.append(f"{MARKETPLACES_PATH}: {market_src.status}")

        # project-scope MCP servers
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

        cfg.skills = _skills_from_dir(facts, "~/.codebuddy/skills")
        if settings_broken:
            # the primary settings file failed to parse; whatever else we got
            # is only part of the picture
            cfg.parse_status = "partial"
        elif settings_src is None or not settings_src.usable:
            cfg.parse_status = "parsed" if (cfg.mcp_servers or cfg.settings) else "empty"
        else:
            cfg.parse_status = (
                "parsed"
                if (cfg.model or cfg.mcp_servers or cfg.skills or cfg.settings)
                else "empty"
            )
        apply_scope(cfg, self, sources, self.project_roots(facts))
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
