"""Antigravity (AGY) adapter.

Verified on a real machine (agy 1.4.1):

  binary      ~/.local/bin/agy   (also ~/.antigravity/antigravity/bin/agy)
  version     agy --version  ->  "1.4.1"   (fast; the models subcommand does a
                                  network call and hangs, so it is never probed)
  state       ~/.gemini/antigravity/antigravity_state.pbtxt
              holds last_selected_agent_model
  mcp         ~/.gemini/antigravity/mcp_config.json   (user scope)
              ~/.gemini/config/mcp_config.json        (shared with Gemini CLI)
              both are JSONC-tolerant and use the same mcpServers shape as
              Claude Code: stdio entries carry command/args/env, http entries
              carry serverUrl/headers, and either may set disabled.
  skills      ~/.gemini/antigravity/skills and ~/.agents/skills

The shared Gemini-level file regularly contains Authorization headers, so only
server names are ever reported.
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
from agent_fleet.remotes.probe import BinProbe

STATE_PATH = "~/.gemini/antigravity/antigravity_state.pbtxt"
MCP_USER_PATH = "~/.gemini/antigravity/mcp_config.json"
MCP_SHARED_PATH = "~/.gemini/config/mcp_config.json"


def _state_model(text: str):
    """Read last_selected_agent_model out of the protobuf-text state file."""
    for line in text.split(chr(10)):
        line = line.strip()
        if line.startswith("last_selected_agent_model:"):
            value = line.split(":", 1)[1].strip().strip('"')
            return value or None
    return None


class AntigravityAdapter(AgentAdapter):
    agent_id = "agy"
    display_name = "Antigravity (AGY)"
    supports_config = True

    PRECEDENCE_CHAIN = [
        ("user", MCP_USER_PATH),
        ("shared", MCP_SHARED_PATH),
    ]
    system_paths: list = []
    project_files: list = []

    def probe_spec(self) -> ProbeSpec:
        return ProbeSpec(
            bins=[
                BinProbe(
                    key="agy:agy",
                    binary="agy",
                    version_args=["--version"],
                    candidates=[
                        "~/.antigravity/antigravity/bin/agy",
                        "~/.local/bin/agy",
                    ],
                )
            ],
            files=[STATE_PATH, MCP_USER_PATH, MCP_SHARED_PATH],
            skill_dirs=["~/.gemini/antigravity/skills", "~/.agents/skills"],
        )

    def detect(self, facts) -> Detection:
        key = "agy:agy"
        path = facts.bin_path(key)
        rc, out = facts.version(key)
        raw = out.strip()
        if not path:
            if key not in facts.probed:
                return Detection(status="unknown", error="no probe data for agy")
            return Detection(status="not_installed")
        return Detection(
            status="installed",
            detected=True,
            version=extract_version(raw) if rc == 0 else None,
            version_raw=raw or None,
            exec_path=path,
            install_method=guess_install_method(path),
            config_dir="~/.gemini/antigravity",
            error=None if rc == 0 else f"version command exited {rc}: {raw[:200]}",
        )

    def discover_config(self, facts) -> list:
        return [
            _read(facts, MCP_USER_PATH, "jsonc", scope="user", kind="mcp"),
            _read(facts, MCP_SHARED_PATH, "jsonc", scope="shared", kind="mcp"),
            _read(facts, STATE_PATH, "text", scope="user", kind="state"),
        ]

    def parse_config(self, facts, sources) -> ParsedConfig:
        cfg = ParsedConfig(resolution="declared")

        state_src = next((s for s in sources if s.path == STATE_PATH), None)
        if state_src and state_src.usable and state_src.content:
            model = _state_model(state_src.content)
            if model:
                cfg.model = model
                cfg.settings["model_source"] = "antigravity_state.pbtxt"
                cfg.warnings.append(
                    "model is the internal agent-model id recorded by Antigravity, "
                    "not a vendor model name"
                )

        # MCP servers: user scope first, then the shared Gemini-level file.
        for src in sources:
            if src.kind != "mcp" or not src.usable:
                continue
            try:
                data = _load(src)
            except Exception as exc:
                cfg.warnings.append(f"{src.path}: {type(exc).__name__}: {exc}"[:300])
                continue
            servers = data.get("mcpServers") if isinstance(data, dict) else None
            if not isinstance(servers, dict):
                continue
            for name, entry in list(servers.items())[:60]:
                item = _mcp_common(entry, name) if isinstance(entry, dict) else {"name": name}
                item["scope"] = src.scope
                item["source"] = src.path
                if isinstance(entry, dict) and entry.get("disabled"):
                    item["enabled"] = False
                cfg.mcp_servers.append(item)

        cfg.skills = _skills_from_dir(facts, "~/.gemini/antigravity/skills")
        for s in _skills_from_dir(facts, "~/.agents/skills"):
            if not any(x["name"] == s["name"] for x in cfg.skills):
                cfg.skills.append(s)

        cfg.settings["skills_dirs"] = ["~/.gemini/antigravity/skills", "~/.agents/skills"]
        cfg.unsupported_fields = [
            "conversations",
            "brain",
            "annotations",
            "plugin_data",
        ]
        # status reflects config files, not the skills directories
        cfg.parse_status = "parsed" if (cfg.model or cfg.mcp_servers) else "empty"
        apply_scope(cfg, self, sources)
        cfg.sanitized = {
            "agent": self.agent_id,
            "scope": "user",
            "resolution": cfg.resolution,
            "scopes": cfg.settings.get("scopes"),
            "config_file": MCP_USER_PATH,
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
