"""Adapter registry.

Adding an agent means writing an AgentAdapter and listing it here.
"""

from __future__ import annotations

import importlib
import logging
from typing import Dict, List

from agent_fleet.adapters.base import AgentAdapter

logger = logging.getLogger("agent_fleet.adapters")

ADAPTER_MODULES = (
    "agent_fleet.adapters.codex:CodexAdapter",
    "agent_fleet.adapters.claude:ClaudeAdapter",
    "agent_fleet.adapters.opencode:OpenCodeAdapter",
    "agent_fleet.adapters.pi:PiAdapter",
    "agent_fleet.adapters.hermes:HermesAdapter",
    "agent_fleet.adapters.agy:AntigravityAdapter",
    "agent_fleet.adapters.codebuddy:CodeBuddyAdapter",
)

AGENT_ORDER = ("codex", "claude", "opencode", "pi", "hermes", "agy", "codebuddy")

_cache: Dict[str, AgentAdapter] = {}
_order: List[str] = []


def _load_all():
    if _cache:
        return
    for dotted in ADAPTER_MODULES:
        mod_name, cls_name = dotted.split(":")
        try:
            cls = getattr(importlib.import_module(mod_name), cls_name)
        except Exception as exc:  # a broken adapter must not kill the fleet
            logger.warning("adapter %s failed to import: %s", dotted, exc)
            continue
        inst = cls()
        _cache[inst.agent_id] = inst
        _order.append(inst.agent_id)
    for extra in AGENT_ORDER:
        if extra not in _order:
            _order.append(extra)


def all_adapters() -> List[AgentAdapter]:
    _load_all()
    return [_cache[k] for k in AGENT_ORDER if k in _cache]


def get_adapter(agent_id: str) -> AgentAdapter:
    _load_all()
    return _cache[agent_id]


def agent_ids() -> List[str]:
    _load_all()
    return [k for k in AGENT_ORDER if k in _cache]


def agent_meta() -> List[dict]:
    return [
        {"id": a.agent_id, "name": a.display_name, "config_supported": a.supports_config}
        for a in all_adapters()
    ]


def merged_probe_spec():
    """One script per runtime: union of everything every adapter needs."""
    from agent_fleet.remotes.probe import ProbeSpec

    spec = ProbeSpec()
    for a in all_adapters():
        spec = spec.merge(a.probe_spec())
        # system-scope policy files are static, so they ride along for free
        for path in getattr(a, "system_paths", []) or []:
            if path not in spec.files:
                spec.files.append(path)
    return spec
