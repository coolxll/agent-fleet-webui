"""Inventory loading.

Three supported sources, merged by host name:
  1. Ansible inventory (YAML or INI)
  2. OpenSSH client config
  3. Project YAML (our own format, also declares WSL runtimes)

Nothing here mutates the source files.
"""

from __future__ import annotations

import configparser
import shlex
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class WslTarget:
    distro: str
    user: str | None = None


@dataclass
class DiscoveredHost:
    name: str
    hostname: str | None = None
    user: str | None = None
    port: int | None = None
    group: str | None = None
    tags: dict = field(default_factory=dict)
    source: str = "project-yaml"
    # kind hint: auto | macos | linux | windows ; refined at probe time
    kind_hint: str = "auto"
    wsl: list[WslTarget] = field(default_factory=list)
    enabled: bool = True


INVENTORY_FILES = ("hosts.yaml", "hosts.yml", "hosts", "inventory.yaml", "inventory.yml", "inventory")


def _clean(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip().strip('"').strip("'")
    return s or None


def load_ansible_inventory(path: Path) -> list[DiscoveredHost]:
    text = path.read_text(encoding="utf-8", errors="replace")
    if re.search(r"^\s*\[[^\]]+\]\s*$", text, re.M) and not text.lstrip().startswith("{"):
        # Heuristic: INI only when it really looks like INI (no top-level yaml keys)
        try:
            return _load_ansible_ini(text, path)
        except Exception:
            pass
    try:
        return _load_ansible_yaml(text)
    except Exception:
        return _load_ansible_ini(text, path)


def _load_ansible_ini(text: str, path: Path) -> list[DiscoveredHost]:
    cp = configparser.ConfigParser(allow_no_value=True, strict=False, interpolation=None)
    cp.read_file(io.StringIO(text))
    hosts: dict[str, DiscoveredHost] = {}
    for section in cp.sections():
        if ":" in section:  # skip [group:vars] / [group:children]
            continue
        for name, value in cp.items(section):
            if not name or name in ("__name__",):
                continue
            # configparser splits on the first "=", so a line like
            # "alpha ansible_host=1.2.3.4" arrives as key="alpha ansible_host".
            line = name if value is None else f"{name}={value}"
            tokens = shlex.split(line)
            if not tokens:
                continue
            kv = _parse_kv_inline(" ".join(tokens[1:]))
            _merge_ansible_attrs(hosts, tokens[0], kv, group=section)
    return list(hosts.values())


def _parse_kv_inline(value: str) -> dict:
    out: dict[str, str] = {}
    for token in shlex.split(value or ""):
        if "=" not in token:
            continue
        k, v = token.split("=", 1)
        out[k] = v
    return out


def _merge_ansible_attrs(hosts: dict, name: str, attrs: dict, group: str | None):
    h = hosts.get(name)
    if h is None:
        h = DiscoveredHost(name=name, source="ansible")
        hosts[name] = h
    if "ansible_host" in attrs:
        h.hostname = _clean(attrs["ansible_host"])
    if "ansible_user" in attrs:
        h.user = _clean(attrs["ansible_user"])
    if "ansible_port" in attrs:
        try:
            h.port = int(str(attrs["ansible_port"]).strip())
        except ValueError:
            pass
    if "ansible_connection" in attrs and attrs["ansible_connection"] != "ssh":
        h.tags["ansible_connection"] = attrs["ansible_connection"]
    if group and not h.group:
        h.group = group


def _load_ansible_yaml(text: str) -> list[DiscoveredHost]:
    data = yaml.safe_load(text) or {}
    hosts: dict[str, DiscoveredHost] = {}

    def walk(node, group: str | None):
        if not isinstance(node, dict):
            return
        for hname, attrs in (node.get("hosts") or {}).items():
            if hname is None:
                continue
            attrs = attrs or {}
            h = hosts.get(hname) or DiscoveredHost(name=hname, source="ansible")
            h.hostname = _clean(attrs.get("ansible_host")) or h.hostname
            h.user = _clean(attrs.get("ansible_user")) or h.user
            if attrs.get("ansible_port"):
                try:
                    h.port = int(attrs["ansible_port"])
                except (TypeError, ValueError):
                    pass
            if group:
                h.group = group
            if isinstance(attrs, dict):
                for k, v in attrs.items():
                    if k.startswith("ansible_") or isinstance(v, (dict, list)):
                        continue
                    if isinstance(v, (str, int, float, bool)):
                        h.tags.setdefault(k, v)
            hosts[hname] = h
        for gname, gnode in (node.get("children") or {}).items():
            walk(gnode, group or gname)

    root = data.get("all", data)
    walk(root, None)
    return list(hosts.values())


def load_ssh_config(path: Path) -> list[DiscoveredHost]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    hosts: list[DiscoveredHost] = []
    current: DiscoveredHost | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^Host\s+(.+)$", line, re.I)
        if m:
            # A Host block can list several aliases; skip glob patterns.
            aliases = [a for a in m.group(1).split() if "*" not in a and "?" not in a]
            if current and current.name:
                hosts.append(current)
            current = DiscoveredHost(name=aliases[0] if aliases else "", source="ssh-config")
            continue
        if current is None:
            continue
        m = re.match(r"^(\w+)\s+(.+)$", line)
        if not m:
            continue
        key, value = m.group(1).lower(), m.group(2).strip()
        if key == "hostname":
            current.hostname = _clean(value)
        elif key == "user":
            current.user = _clean(value)
        elif key == "port":
            try:
                current.port = int(value)
            except ValueError:
                pass
    if current and current.name:
        hosts.append(current)

    # Remove placeholder / catch-all entries
    return [h for h in hosts if h.name and h.name != "*"]


def load_exclusions(path: Path) -> set[str]:
    """Host names explicitly excluded from the fleet by inventory.yaml."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace")) or {}
    except Exception:
        return set()
    if not isinstance(data, dict):
        return set()
    items = data.get("exclude") or []
    if not isinstance(items, list):
        return set()
    return {str(x).strip() for x in items if str(x).strip()}


def load_project_yaml(path: Path) -> list[DiscoveredHost]:
    data = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace")) or {}
    out: list[DiscoveredHost] = []
    for entry in data.get("hosts") or []:
        if isinstance(entry, str):
            entry = {"name": entry}
        wsl = []
        for w in entry.get("wsl") or []:
            if isinstance(w, str):
                wsl.append(WslTarget(distro=w))
            else:
                wsl.append(WslTarget(distro=w.get("distro", ""), user=_clean(w.get("user"))))
        out.append(
            DiscoveredHost(
                name=entry["name"],
                hostname=_clean(entry.get("hostname") or entry.get("ansible_host")),
                user=_clean(entry.get("user") or entry.get("ansible_user")),
                port=entry.get("port") or entry.get("ansible_port"),
                group=_clean(entry.get("group")),
                tags=entry.get("tags") or {},
                source="project-yaml",
                kind_hint=entry.get("kind", "auto"),
                wsl=wsl,
                enabled=bool(entry.get("enabled", True)),
            )
        )
    return out


def discover_inventory_dir(path: Path) -> list[DiscoveredHost]:
    """Given a directory, find the inventory file inside it."""
    if path.is_file():
        return load_ansible_inventory(path)
    for fname in INVENTORY_FILES:
        candidate = path / fname
        if candidate.is_file():
            return load_ansible_inventory(candidate)
    inv = path / "inventory"
    if inv.is_dir():
        for fname in INVENTORY_FILES:
            candidate = inv / fname
            if candidate.is_file():
                return load_ansible_inventory(candidate)
    return []


def load_all(settings) -> list[DiscoveredHost]:
    merged: dict[str, DiscoveredHost] = {}

    def add_many(items: list[DiscoveredHost]):
        for h in items:
            known = merged.get(h.name)
            if known is None:
                merged[h.name] = h
                continue
            # later sources fill gaps only
            known.hostname = known.hostname or h.hostname
            known.user = known.user or h.user
            known.port = known.port or h.port
            known.group = known.group or h.group
            known.tags.update(h.tags or {})
            known.wsl = known.wsl or h.wsl
            known.kind_hint = h.kind_hint if h.kind_hint != "auto" else known.kind_hint

    for p in settings.inventory_paths:
        p = Path(p)
        if p.is_dir():
            add_many(discover_inventory_dir(p))
        elif p.suffix.lower() in (".yaml", ".yml"):
            try:
                if _is_ansible_shape(p):
                    add_many(load_ansible_inventory(p))
                else:
                    add_many(load_project_yaml(p))
            except Exception:
                continue
        elif p.exists():
            add_many(load_ansible_inventory(p))

    add_many(load_ssh_config(settings.ssh_config_path))

    # inventory.yaml can exclude hosts outright (for example ssh aliases that
    # are not part of the fleet). Exclusions win over every source.
    excluded: set[str] = set()
    for p in settings.inventory_paths:
        p = Path(p)
        if p.suffix.lower() in (".yaml", ".yml") and p.exists():
            excluded |= load_exclusions(p)
    if excluded:
        merged = {k: v for k, v in merged.items() if k not in excluded}
    return list(merged.values())


def _is_ansible_shape(path: Path) -> bool:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return False
    return isinstance(data, dict) and "all" in data
