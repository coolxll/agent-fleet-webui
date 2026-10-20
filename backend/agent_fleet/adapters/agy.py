"""Antigravity (AGY) adapter.

The CLI moved its own state directory between 1.4.1 and 1.4.2, and this machine
shows both trees side by side:

  new (1.4.2+)  ~/.gemini/antigravity-cli/settings.json   live "model"
                ~/.gemini/antigravity-cli/mcp_config.json
                ~/.gemini/antigravity-cli/skills
  old (<=1.4.1) ~/.gemini/antigravity/antigravity_state.pbtxt
                holds last_selected_agent_model, an internal id such as
                MODEL_PLACEHOLDER_M319
                ~/.gemini/antigravity/mcp_config.json
                ~/.gemini/antigravity/skills

The two trees must not be merged: on a machine upgraded to 1.4.2 the old
directory still exists (left over from the IDE and never rewritten since May),
so unioning them reports MCP servers and skills the CLI does not read. Both
trees are probed; the presence of the CLI directory selects exactly one.

Always read, in either layout:

  ~/.gemini/config/mcp_config.json   shared with Gemini CLI
  ~/.gemini/config/skills            shared with Gemini CLI (new layout only)
  ~/.agents/skills

All MCP files are JSONC-tolerant and use the same mcpServers shape as Claude
Code: stdio entries carry command/args/env, http entries carry
serverUrl/headers, and either may set disabled. The shared Gemini-level file
regularly contains Authorization headers, so only server names are reported.
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
from agent_fleet.adapters.sanitize import redact_value
from agent_fleet.remotes.probe import BinProbe

# 1.4.2+ CLI locations.
CLI_DIR = "~/.gemini/antigravity-cli"
CLI_SETTINGS_PATH = "~/.gemini/antigravity-cli/settings.json"
CLI_MCP_PATH = "~/.gemini/antigravity-cli/mcp_config.json"
CLI_SKILLS = "~/.gemini/antigravity-cli/skills"

# Pre-1.4.2 / IDE locations.
OLD_DIR = "~/.gemini/antigravity"
STATE_PATH = "~/.gemini/antigravity/antigravity_state.pbtxt"
OLD_MCP_PATH = "~/.gemini/antigravity/mcp_config.json"
OLD_SKILLS = "~/.gemini/antigravity/skills"

# Shared with Gemini CLI; never moved, always read.
MCP_SHARED_PATH = "~/.gemini/config/mcp_config.json"
SHARED_SKILLS = "~/.gemini/config/skills"
AGENTS_SKILLS = "~/.agents/skills"

# agy-swap (multi-account quota manager for the Antigravity CLI) stores its
# account registry next to the CLI tree. The file also contains credential
# references, so only the quota snapshot and display fields are ever read.
AGY_SWAP_DIR = "~/.gemini/agy-swap"
AGY_SWAP_ACCOUNTS_PATH = AGY_SWAP_DIR + "/accounts.json"
AGY_SWAP_CONFIG_PATH = AGY_SWAP_DIR + "/config.json"


def _state_model(text: str):
    """Read last_selected_agent_model out of the protobuf-text state file."""
    for line in text.split(chr(10)):
        line = line.strip()
        if line.startswith("last_selected_agent_model:"):
            value = line.split(":", 1)[1].strip().strip('"')
            return value or None
    return None


def _exists(facts, path: str, kind: str = "file") -> bool:
    """True when the probe saw the path at all (even unreadable/empty)."""
    if kind == "dir":
        return path in facts.skills
    return facts.file(path).status not in ("missing", "unknown")


def cli_layout(facts) -> bool:
    """True when the 1.4.2+ CLI directory is present on this machine."""
    return (
        _exists(facts, CLI_SETTINGS_PATH)
        or _exists(facts, CLI_MCP_PATH)
        or _exists(facts, CLI_SKILLS, kind="dir")
    )


def _clamp_fraction(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return max(0.0, min(1.0, number))


def _text(value, limit=160):
    if isinstance(value, str):
        # Quota fields are an explicit whitelist, but still pass through the
        # value-level redactor in case a remote file smuggles a credential in.
        return redact_value(value[:limit])
    return None


def _agy_swap_aliases(config_data) -> dict:
    """Map email -> [alias, ...] from agy-swap's config.json."""
    aliases = {}
    raw = config_data.get("aliases") if isinstance(config_data, dict) else None
    if not isinstance(raw, dict):
        return aliases
    for alias, email in list(raw.items())[:200]:
        if isinstance(alias, str) and isinstance(email, str):
            aliases.setdefault(email.lower(), []).append(alias[:64])
    return aliases


def _agy_swap_accounts(data):
    """Yield (email_hint, account_dict) for both known registry shapes."""
    if isinstance(data, dict) and isinstance(data.get("accounts"), (dict, list)):
        data = data["accounts"]
    if isinstance(data, dict):
        for email, entry in list(data.items())[:200]:
            if isinstance(entry, dict):
                yield _text(email, 200), entry
    elif isinstance(data, list):
        for entry in data[:200]:
            if isinstance(entry, dict):
                yield _text(entry.get("email"), 200), entry


def _agy_swap_groups(snapshot):
    """Whitelist and validate quota groups from one agy-swap account."""
    groups = []
    raw_groups = snapshot.get("groups") if isinstance(snapshot, dict) else None
    if not isinstance(raw_groups, list):
        return groups
    for raw_group in raw_groups[:20]:
        if not isinstance(raw_group, dict):
            continue
        group_id = _text(raw_group.get("id"), 60)
        if not group_id:
            continue
        buckets = []
        raw_buckets = raw_group.get("buckets")
        if isinstance(raw_buckets, list):
            for raw_bucket in raw_buckets[:10]:
                if not isinstance(raw_bucket, dict):
                    continue
                window = raw_bucket.get("window")
                if window not in ("weekly", "5h"):
                    continue
                fraction = _clamp_fraction(raw_bucket.get("remaining_fraction"))
                reset_at = _text(raw_bucket.get("reset_at"), 80)
                if fraction is None or not reset_at:
                    continue
                buckets.append(
                    {
                        "id": _text(raw_bucket.get("id"), 80),
                        "name": _text(raw_bucket.get("name"), 80)
                        or ("Weekly" if window == "weekly" else "5-Hour"),
                        "window": window,
                        "remaining_fraction": fraction,
                        "reset_at": reset_at,
                    }
                )
        if buckets:
            groups.append(
                {
                    "id": group_id,
                    "name": _text(raw_group.get("name"), 80) or group_id,
                    "buckets": buckets,
                }
            )
    return groups


def _agy_swap_quota(sources, cfg) -> dict:
    """Read agy-swap's account registry into a sanitized quota snapshot.

    The registry is the same document the `agy-swap limits` command renders.
    It also carries credential references, so this function copies an explicit
    whitelist of display and quota fields and nothing else.
    """
    accounts_src = next((s for s in sources if s.path == AGY_SWAP_ACCOUNTS_PATH), None)
    if not accounts_src or not accounts_src.usable:
        if accounts_src and accounts_src.status not in ("missing", "not_probed"):
            cfg.warnings.append(
                f"{accounts_src.path}: {accounts_src.status}; agy-swap quotas unavailable"
            )
        return None
    try:
        data = _load(accounts_src)
    except Exception as exc:
        cfg.warnings.append(f"{accounts_src.path}: {type(exc).__name__}: {exc}"[:300])
        return None

    config_src = next((s for s in sources if s.path == AGY_SWAP_CONFIG_PATH), None)
    aliases = {}
    if config_src and config_src.usable:
        try:
            aliases = _agy_swap_aliases(_load(config_src))
        except Exception:
            # Aliases are cosmetic; a broken config.json must not hide quotas.
            aliases = {}

    accounts = []
    for email_hint, entry in _agy_swap_accounts(data):
        email = _text(entry.get("email"), 200) or email_hint
        if not email:
            continue
        snapshot = entry.get("quota_snapshot")
        groups = _agy_swap_groups(snapshot)
        account = {
            "email": email,
            "name": _text(entry.get("name"), 120) or email.split("@", 1)[0],
            "aliases": aliases.get(email.lower(), []),
            "groups": groups,
        }
        tier = snapshot.get("tier") if isinstance(snapshot, dict) else None
        if isinstance(tier, dict):
            account["tier"] = {
                "id": _text(tier.get("id"), 80),
                "name": _text(tier.get("name"), 80),
            }
        observed_at = _text(snapshot.get("observed_at"), 80) if isinstance(snapshot, dict) else None
        if observed_at:
            account["observed_at"] = observed_at
        # The tightest bucket across every pool: what the account can do now.
        fractions = [
            bucket["remaining_fraction"]
            for group in groups
            for bucket in group["buckets"]
        ]
        if fractions:
            account["min_remaining_fraction"] = min(fractions)
        accounts.append(account)

    if not accounts:
        return None
    observed = sorted(
        {a["observed_at"] for a in accounts if a.get("observed_at")}, reverse=True
    )
    return {
        "source": "agy-swap",
        "path": AGY_SWAP_ACCOUNTS_PATH,
        "accounts": accounts,
        "observed_at": observed[0] if observed else None,
    }


class AntigravityAdapter(AgentAdapter):
    agent_id = "agy"
    display_name = "Antigravity (AGY)"
    supports_config = True

    PRECEDENCE_CHAIN = [
        ("user", CLI_MCP_PATH),
        ("user", OLD_MCP_PATH),
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
            files=[
                CLI_SETTINGS_PATH,
                CLI_MCP_PATH,
                OLD_MCP_PATH,
                MCP_SHARED_PATH,
                STATE_PATH,
                AGY_SWAP_ACCOUNTS_PATH,
                AGY_SWAP_CONFIG_PATH,
            ],
            skill_dirs=[CLI_SKILLS, SHARED_SKILLS, OLD_SKILLS, AGENTS_SKILLS],
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
            config_dir=CLI_DIR if cli_layout(facts) else OLD_DIR,
            error=None if rc == 0 else f"version command exited {rc}: {raw[:200]}",
        )

    def discover_config(self, facts) -> list:
        if cli_layout(facts):
            sources = [
                _read(facts, CLI_SETTINGS_PATH, "jsonc", scope="user", kind="settings"),
                _read(facts, CLI_MCP_PATH, "jsonc", scope="user", kind="mcp"),
            ]
        else:
            sources = [
                _read(facts, OLD_MCP_PATH, "jsonc", scope="user", kind="mcp"),
            ]
        sources.append(_read(facts, MCP_SHARED_PATH, "jsonc", scope="shared", kind="mcp"))
        # The state file is only a model fallback, so it is read in both
        # layouts: a CLI install whose settings.json was not readable still
        # gets whatever model the state file remembers.
        sources.append(_read(facts, STATE_PATH, "text", scope="user", kind="state"))
        # agy-swap quota is an auxiliary source: it never participates in the
        # agy config precedence chain, but it rides along in the same probe.
        sources.append(
            _read(facts, AGY_SWAP_ACCOUNTS_PATH, "json", scope="user", kind="quota")
        )
        sources.append(
            _read(facts, AGY_SWAP_CONFIG_PATH, "json", scope="user", kind="quota")
        )
        return sources

    def _skill_dirs(self, facts) -> list:
        if cli_layout(facts):
            return [CLI_SKILLS, SHARED_SKILLS, AGENTS_SKILLS]
        return [OLD_SKILLS, AGENTS_SKILLS]

    def scope_report(self, sources, project_roots=None) -> list:
        """Only the active layout's user file, plus the always-read shared one.

        The class-level chain lists both user generations for documentation;
        reporting the inactive one would show a stale file as part of the
        effective configuration.
        """
        user_path = (
            CLI_MCP_PATH
            if any(s.path == CLI_MCP_PATH for s in sources or [])
            else OLD_MCP_PATH
        )
        by_path = {s.path: s for s in sources or []}
        out = []
        for scope, path in (("user", user_path), ("shared", MCP_SHARED_PATH)):
            src = by_path.get(path)
            out.append(
                {
                    "scope": scope,
                    "path": path,
                    "status": src.status if src else "not_probed",
                }
            )
        return out

    def parse_config(self, facts, sources) -> ParsedConfig:
        cfg = ParsedConfig(resolution="declared")
        new_layout = cli_layout(facts)

        # Model: the CLI's own settings are authoritative; the protobuf-text
        # state file is the pre-1.4.2 fallback and only carries an internal id.
        settings_src = next((s for s in sources if s.path == CLI_SETTINGS_PATH), None)
        if settings_src and settings_src.usable and settings_src.content:
            try:
                data = _load(settings_src)
            except Exception as exc:
                cfg.warnings.append(f"{settings_src.path}: {type(exc).__name__}: {exc}"[:300])
            else:
                if isinstance(data, dict) and isinstance(data.get("model"), str):
                    cfg.model = data["model"] or None
                    cfg.settings["model_source"] = "antigravity-cli/settings.json"

        if not cfg.model:
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

        # MCP servers: the layout's user file, then the shared Gemini-level file.
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

        skill_dirs = self._skill_dirs(facts)
        for d in skill_dirs:
            for s in _skills_from_dir(facts, d):
                if not any(x["name"] == s["name"] for x in cfg.skills):
                    cfg.skills.append(s)

        cfg.quota = _agy_swap_quota(sources, cfg)

        cfg.settings["layout"] = "cli" if new_layout else "legacy"
        cfg.settings["skills_dirs"] = skill_dirs
        cfg.unsupported_fields = [
            "conversations",
            "brain",
            "annotations",
            "plugin_data",
        ]
        # status reflects config files, not the skills directories
        cfg.parse_status = (
            "parsed" if (cfg.model or cfg.mcp_servers or cfg.quota) else "empty"
        )
        apply_scope(cfg, self, sources)
        cfg.sanitized = {
            "agent": self.agent_id,
            "scope": "user",
            "resolution": cfg.resolution,
            "scopes": cfg.settings.get("scopes"),
            "config_file": CLI_SETTINGS_PATH if new_layout else OLD_MCP_PATH,
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
