import pytest

from agent_fleet import models
from agent_fleet.db import session_scope
from agent_fleet.services import sqlite_inventory


def test_seed_from_legacy_files(fleet_db):
    # 验证自动无损入库
    result = sqlite_inventory.seed_from_legacy_files_if_needed(force=True)
    assert isinstance(result, dict)

    hosts = sqlite_inventory.list_hosts()
    assert len(hosts) > 0

    host_names = [h["name"] for h in hosts]
    assert "corp172-dev" in host_names

    corp = sqlite_inventory.get_host("corp172-dev")
    assert corp is not None
    assert corp["ssh_user"] == "lynn"
    assert "8118" in corp.get("proxy", "")
    assert corp["runner_enabled"] is True


def test_sqlite_inventory_crud(fleet_db):
    test_host_name = "test-node-sqlite-crud"

    # Clean up before
    sqlite_inventory.delete_host(test_host_name)

    # 1. Create
    created = sqlite_inventory.create_host(
        {
            "name": test_host_name,
            "hostname": "192.168.1.100",
            "description": "Test SQLite Host",
            "ssh_user": "testadmin",
            "ssh_port": 2222,
            "proxy": "socks5://10.0.0.1:1080",
            "runner_enabled": True,
            "runner_max_concurrency": 4,
            "runner_labels": ["self-hosted", "test-label"],
            "target_repos": ["coolxll/demo"],
        }
    )
    assert created["name"] == test_host_name
    assert created["ssh_user"] == "testadmin"
    assert created["ssh_port"] == 2222
    assert created["proxy"] == "socks5://10.0.0.1:1080"
    assert created["runner_max_concurrency"] == 4
    assert created["runtimes_count"] == 1

    # 2. Get
    fetched = sqlite_inventory.get_host(test_host_name)
    assert fetched is not None
    assert fetched["id"] == created["id"]
    assert fetched["target_repos"] == ["coolxll/demo"]

    # 3. Update
    updated = sqlite_inventory.update_host(
        test_host_name,
        {
            "proxy": "http://127.0.0.1:8118",
            "runner_max_concurrency": 8,
            "target_repos": ["coolxll/demo", "coolxll/another"],
        },
    )
    assert updated["proxy"] == "http://127.0.0.1:8118"
    assert updated["runner_max_concurrency"] == 8
    assert len(updated["target_repos"]) == 2

    # 4. Export YAML
    yaml_text = sqlite_inventory.export_as_yaml()
    assert test_host_name in yaml_text
    assert "testadmin" in yaml_text

    # 5. Delete
    deleted = sqlite_inventory.delete_host(test_host_name)
    assert deleted is True
    assert sqlite_inventory.get_host(test_host_name) is None


def test_inventory_hosts_api(client):
    test_api_host = "test-node-api-host"
    sqlite_inventory.delete_host(test_api_host)

    # 1. List
    res = client.get("/api/inventory/hosts")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert "hosts" in data

    # 2. Create via API
    res = client.post(
        "/api/inventory/hosts",
        json={
            "name": test_api_host,
            "hostname": "test-api.local",
            "description": "API Test",
            "ssh_user": "apiuser",
            "proxy": "http://127.0.0.1:8118",
            "runner_enabled": True,
            "target_repos": ["coolxll/agent-fleet-webui"],
        },
    )
    assert res.status_code == 200
    created = res.json()["host"]
    assert created["name"] == test_api_host
    assert created["ssh_user"] == "apiuser"

    # 3. Get single
    res = client.get(f"/api/inventory/hosts/{test_api_host}")
    assert res.status_code == 200
    assert res.json()["host"]["name"] == test_api_host

    # 4. Update via API
    res = client.put(
        f"/api/inventory/hosts/{test_api_host}",
        json={"proxy": "socks5://100.93.132.98:1080", "runner_max_concurrency": 6},
    )
    assert res.status_code == 200
    updated = res.json()["host"]
    assert updated["proxy"] == "socks5://100.93.132.98:1080"
    assert updated["runner_max_concurrency"] == 6

    # 5. Export via API
    res = client.get("/api/inventory/export-yaml")
    assert res.status_code == 200
    assert test_api_host in res.json()["content"]

    # 6. Delete via API
    res = client.delete(f"/api/inventory/hosts/{test_api_host}")
    assert res.status_code == 200
    assert res.json()["deleted"] is True

    # 7. Check 404
    res = client.get(f"/api/inventory/hosts/{test_api_host}")
    assert res.status_code == 404
