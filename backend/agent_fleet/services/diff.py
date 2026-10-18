"""Comparison engine.

Two entry points, both producing the same change records:
  - diff of two installations (same agent on two machines, or two agents)
  - diff of two snapshots (history of one runtime, or two runtimes)

Change record: {"path", "kind", "a", "b"}
  kind: added | removed | changed
"""

from __future__ import annotations

import json
from itertools import zip_longest

# Fill value for zip_longest: a list can legitimately contain None, so a
# None sentinel would make "removed null entry" indistinguishable from "no entry".
_MISSING = object()


def _scalar(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except Exception:
        return str(value)


def _key(item, index):
    if isinstance(item, dict):
        for k in ("name", "path", "id"):
            if item.get(k):
                return str(item[k])
    return "__" + str(index)


def diff_dicts(a: dict, b: dict, path: str = "") -> list:
    out = []
    for key in sorted(set(a) | set(b)):
        child = f"{path}.{key}" if path else key
        if key not in a:
            out.append({"path": child, "kind": "added", "a": None, "b": _scalar(b[key])})
            continue
        if key not in b:
            out.append({"path": child, "kind": "removed", "a": _scalar(a[key]), "b": None})
            continue
        va, vb = a[key], b[key]
        if isinstance(va, dict) and isinstance(vb, dict):
            out.extend(diff_dicts(va, vb, child))
        elif isinstance(va, list) and isinstance(vb, list):
            out.extend(diff_lists(va, vb, child))
        elif va != vb:
            out.append({"path": child, "kind": "changed", "a": _scalar(va), "b": _scalar(vb)})
    return out


def _group(items: list) -> dict:
    """Bucket list entries by key, keeping duplicates instead of collapsing them.

    MCP servers can appear twice with the same name (user scope plus a project
    override); a plain dict would drop one of them and mask real changes.
    """
    groups: dict = {}
    for i, item in enumerate(items or []):
        groups.setdefault(_key(item, i), []).append(item)
    return groups


def diff_lists(a: list, b: list, path: str) -> list:
    out = []
    ga = _group(a)
    gb = _group(b)
    for key in sorted(set(ga) | set(gb)):
        items_a = ga.get(key, [])
        items_b = gb.get(key, [])
        base = f"{path}[{key}]"
        # Duplicates get an index suffix so the two sides stay distinguishable.
        dup = len(items_a) > 1 or len(items_b) > 1
        for i, (va, vb) in enumerate(zip_longest(items_a, items_b, fillvalue=_MISSING)):
            child = base + (f"#{i}" if dup else "")
            if va is _MISSING:
                out.append({"path": child, "kind": "added", "a": None, "b": _scalar(vb)})
            elif vb is _MISSING:
                out.append({"path": child, "kind": "removed", "a": _scalar(va), "b": None})
            elif isinstance(va, dict) and isinstance(vb, dict):
                out.extend(diff_dicts(va, vb, child))
            elif isinstance(va, list) and isinstance(vb, list):
                out.extend(diff_lists(va, vb, child))
            elif va != vb:
                out.append({"path": child, "kind": "changed", "a": _scalar(va), "b": _scalar(vb)})
    return out


# ---------------------------------------------------------------------------
# views
# ---------------------------------------------------------------------------


def installation_view(inst) -> dict:
    """Flatten an AgentInstallation row into a comparable dict."""
    cfg = getattr(inst, "config", None)
    return {
        "status": inst.status,
        "version": inst.version,
        "exec_path": inst.exec_path,
        "install_method": inst.install_method,
        "model": cfg.model if cfg else None,
        "provider": cfg.model_provider if cfg else None,
        "base_url": cfg.base_url if cfg else None,
        "parse_status": cfg.parse_status if cfg else None,
        "mcp_servers": (cfg.mcp_servers if cfg else None) or [],
        "skills": [s.get("name") for s in (cfg.skills if cfg else None) or []],
        "settings": (cfg.settings if cfg else None) or {},
        "sources": {s.path: s.status for s in getattr(inst, "sources", []) or []},
    }


def snapshot_view(snapshot) -> dict:
    """Flatten a snapshot payload into {agent: comparable dict}."""
    out = {}
    payload = snapshot.payload or {}
    for entry in payload.get("agents") or []:
        cfg = entry.get("config") or {}
        network = cfg.get("network") or {}
        out[entry.get("agent")] = {
            "status": entry.get("status"),
            "version": entry.get("version"),
            "exec_path": entry.get("exec_path"),
            "install_method": entry.get("install_method"),
            "model": cfg.get("model"),
            "provider": cfg.get("provider"),
            "base_url": network.get("base_url"),
            "parse_status": entry.get("config_status"),
            "mcp_servers": cfg.get("mcp_servers") or [],
            "skills": [s.get("name") for s in (cfg.get("skills") or [])],
            "settings": cfg.get("settings") or {},
            "sources": None,
        }
    return out


def compare_installations(inst_a, inst_b) -> dict:
    a = installation_view(inst_a)
    b = installation_view(inst_b)
    changes = diff_dicts(a, b)
    return {
        "kind": "installations",
        "same_agent": inst_a.agent_id == inst_b.agent_id,
        "a": {
            "id": inst_a.id,
            "agent": inst_a.agent_id,
            "runtime_id": inst_a.runtime_id,
            "config_id": getattr(inst_a, "config_id", None),
        },
        "b": {
            "id": inst_b.id,
            "agent": inst_b.agent_id,
            "runtime_id": inst_b.runtime_id,
            "config_id": getattr(inst_b, "config_id", None),
        },
        "changes": changes,
        "identical": not changes,
    }


def compare_snapshots(snap_a, snap_b) -> dict:
    va = snapshot_view(snap_a)
    vb = snapshot_view(snap_b)
    per_agent = {}
    for agent in sorted(set(va) | set(vb)):
        changes = diff_dicts(va.get(agent, {}), vb.get(agent, {}))
        if changes:
            per_agent[agent] = changes
    return {
        "kind": "snapshots",
        "a": {"id": snap_a.id, "runtime_id": snap_a.runtime_id, "created_at": snap_a.created_at},
        "b": {"id": snap_b.id, "runtime_id": snap_b.runtime_id, "created_at": snap_b.created_at},
        "per_agent": per_agent,
        "changes": [c for changes in per_agent.values() for c in changes],
        "identical": not per_agent,
    }
