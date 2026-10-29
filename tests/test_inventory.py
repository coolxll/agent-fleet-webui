"""Inventory loading from all three supported sources."""

from __future__ import annotations

from sqlalchemy import select

from agent_fleet.db import session_scope
from agent_fleet.inventory import (
    discover_inventory_dir,
    load_all,
    load_ansible_inventory,
    load_project_yaml,
    load_ssh_config,
)


def test_ansible_yaml_inventory():
    hosts = load_ansible_inventory(Path("tests/fixtures/inventory/ansible_hosts.yaml"))
    by_name = {h.name: h for h in hosts}
    assert set(by_name) == {"rn-direct", "la-tri", "wujie"}
    assert by_name["rn-direct"].hostname == "64.188.31.181"
    assert by_name["rn-direct"].user == "coolxll"
    assert by_name["rn-direct"].port == 22
    assert by_name["la-tri"].user == "root"
    assert all(h.source == "ansible" for h in hosts)


def test_ansible_ini_inventory(tmp_path):
    p = tmp_path / "hosts"
    p.write_text(
        "[vps]\n"
        "alpha ansible_host=10.0.0.1 ansible_user=root ansible_port=2222\n"
        "beta ansible_host=10.0.0.2\n"
        "\n"
        "[vps:vars]\n"
        "ansible_python_interpreter=/usr/bin/python3\n"
    )
    hosts = {h.name: h for h in load_ansible_inventory(p)}
    assert hosts["alpha"].hostname == "10.0.0.1"
    assert hosts["alpha"].user == "root"
    assert hosts["alpha"].port == 2222
    assert hosts["beta"].hostname == "10.0.0.2"
    # group vars must not become hosts
    assert "ansible_python_interpreter" not in hosts


def test_ssh_config_parsing():
    from pathlib import Path

    hosts = load_ssh_config(Path("tests/fixtures/inventory/ssh_config"))
    by_name = {h.name: h for h in hosts}
    assert set(by_name) == {"la-tri", "racknerd", "ddrk"}
    assert by_name["ddrk"].port == 22222
    assert by_name["ddrk"].user == "lynn"
    assert by_name["la-tri"].hostname == "38.244.63.166"
    # glob patterns are not hosts
    assert "wildcard-*" not in by_name


def test_ssh_config_missing_file(tmp_path):
    assert load_ssh_config(tmp_path / "nope") == []


def test_project_yaml_with_wsl(tmp_path):
    p = tmp_path / "inv.yaml"
    p.write_text(
        "hosts:\n"
        "  - name: box1\n"
        "    kind: windows\n"
        "    wsl:\n"
        "      - distro: Ubuntu-22.04\n"
        "        user: dev\n"
        "  - name: box2\n"
        "    kind: linux\n"
        "    enabled: false\n"
    )
    hosts = {h.name: h for h in load_project_yaml(p)}
    assert hosts["box1"].kind_hint == "windows"
    assert hosts["box1"].wsl[0].distro == "Ubuntu-22.04"
    assert hosts["box1"].wsl[0].user == "dev"
    assert hosts["box2"].enabled is False


def test_kind_hint_does_not_override_a_probe(fleet_db):
    """A declared kind is a pre-scan guess; the observation wins afterwards."""
    import datetime as _dt

    from agent_fleet import models
    from agent_fleet.services.inventory_sync import sync_inventory
    from agent_fleet.inventory.loader import DiscoveredHost

    settings = fleet_db
    monkey = None
    with session_scope() as s:
        host = models.Host(id=models.new_id(), name="hint-box", inventory_source="project-yaml")
        s.add(host)
        s.flush()
        rt = models.Runtime(
            id=models.new_id(),
            host_id=host.id,
            key="native",
            kind="wsl",
            transport="ssh",
            last_scan_at=models.utcnow(),
        )
        s.add(rt)
        s.commit()

    # a host that declares kind=linux while the probe already saw wsl
    orig = load_project_yaml
    try:
        import agent_fleet.services.inventory_sync as mod

        mod.load_project_yaml = lambda path: [DiscoveredHost(name="hint-box", kind_hint="linux")]
        sync_inventory(settings)
    finally:
        mod.load_project_yaml = orig

    with session_scope() as s:
        row = s.execute(select(models.Runtime)).scalars().first()
        assert row.kind == "wsl", "a probe observation must not be overwritten"


def test_inventory_directory_discovery(tmp_path):
    d = tmp_path / "inventory"
    d.mkdir()
    (d / "hosts.yaml").write_text(
        "all:\n  hosts:\n    one:\n      ansible_host: 1.2.3.4\n"
    )
    hosts = discover_inventory_dir(d)
    assert [h.name for h in hosts] == ["one"]


def test_declared_controller_becomes_local_and_prunes_phantom(fleet_db):
    """When a host is declared with role=controller, it becomes local transport and prunes dummy local-controller."""
    from agent_fleet import models
    from agent_fleet.inventory.loader import DiscoveredHost
    from agent_fleet.services.inventory_sync import add_local_runtime, sync_inventory
    import agent_fleet.services.inventory_sync as mod

    settings = fleet_db
    # First simulate an existing phantom local-controller
    with session_scope() as s:
        dummy = models.Host(
            id=models.new_id(),
            name="local-controller",
            inventory_source="local",
            group="controller",
        )
        s.add(dummy)
        s.flush()
        rt = models.Runtime(
            id=models.new_id(),
            host_id=dummy.id,
            key="native",
            kind="unknown",
            transport="local",
        )
        s.add(rt)
        s.commit()

    # Discovered inventory has macbook-air tagged role: controller
    orig = mod.load_all
    try:
        mod.load_all = lambda s: [
            DiscoveredHost(
                name="macbook-air",
                tags={"role": "controller"},
                source="project-yaml",
            )
        ]
        controller_name = add_local_runtime(settings)
        assert controller_name == "macbook-air"
        result = sync_inventory(settings)
        assert result["hosts_seen"] == 1
    finally:
        mod.load_all = orig

    with session_scope() as s:
        all_hosts = s.execute(select(models.Host)).scalars().all()
        host_names = {h.name for h in all_hosts}
        assert "macbook-air" in host_names
        assert "local-controller" not in host_names, "phantom local-controller must be pruned"

        mac = s.execute(select(models.Host).where(models.Host.name == "macbook-air")).scalar_one()
        mac_rt = s.execute(select(models.Runtime).where(models.Runtime.host_id == mac.id)).scalar_one()
        assert mac_rt.transport == "local"


from pathlib import Path  # noqa: E402

