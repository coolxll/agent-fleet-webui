"""Tests for inventory raw content, validation, and auto-resolution API."""

from __future__ import annotations

from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from agent_fleet.main import app
from agent_fleet.inventory.loader import validate_inventory_yaml
from agent_fleet.settings import Settings


@pytest.fixture()
def client(fleet_db, monkeypatch):
    monkeypatch.setenv("AGENT_FLEET_INCLUDE_LOCAL_RUNTIME", "false")
    with TestClient(app) as c:
        yield c


def test_validate_inventory_yaml_helpers():
    # Valid project format
    valid_text = """
hosts:
  - name: server-1
    kind: linux
  - name: win-box
    kind: windows
    wsl:
      - distro: Ubuntu
"""
    ok, err, summary = validate_inventory_yaml(valid_text)
    assert ok is True
    assert err is None
    assert summary["host_count"] == 2
    assert summary["hosts"] == ["server-1", "win-box"]
    assert summary["wsl_count"] == 1

    # Empty text
    ok, err, _ = validate_inventory_yaml("   ")
    assert ok is False
    assert "不能为空" in err

    # Syntax error
    ok, err, _ = validate_inventory_yaml("hosts: [")
    assert ok is False
    assert "YAML 语法解析错误" in err

    # Invalid kind
    invalid_kind = """
hosts:
  - name: box-1
    kind: invalid-os
"""
    ok, err, _ = validate_inventory_yaml(invalid_kind)
    assert ok is False
    assert "kind 'invalid-os' 无效" in err

    # Missing name
    missing_name = """
hosts:
  - kind: linux
"""
    ok, err, _ = validate_inventory_yaml(missing_name)
    assert ok is False
    assert "缺少必填属性 'name'" in err


def test_get_inventory_raw(client):
    res = client.get("/api/inventory/raw")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert "path" in data
    assert "content" in data
    assert "example_content" in data


def test_validate_inventory_endpoint(client):
    res = client.post(
        "/api/inventory/validate",
        json={"content": "hosts:\n  - name: test-node\n    kind: linux\n"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["valid"] is True
    assert data["summary"]["host_count"] == 1

    # Invalid payload
    res_bad = client.post(
        "/api/inventory/validate",
        json={"content": "hosts: [invalid"},
    )
    assert res_bad.status_code == 200
    data_bad = res_bad.json()
    assert data_bad["valid"] is False
    assert "YAML" in data_bad["error"]


def test_save_inventory_rejects_invalid(client):
    res = client.post(
        "/api/inventory/raw",
        json={"content": "hosts:\n  - name: box\n    kind: bad-kind\n"},
    )
    assert res.status_code == 400
    assert "配置校验未通过" in res.json()["detail"]


def test_save_inventory_success_and_sync(client, tmp_path, monkeypatch):
    test_file = tmp_path / "inventory.yaml"
    mock_settings = Settings(
        inventory_paths=[test_file],
        db_path=tmp_path / "test.db",
    )
    monkeypatch.setattr("agent_fleet.api.routes.get_settings", lambda: mock_settings)
    monkeypatch.setattr("agent_fleet.services.inventory_sync.load_all", lambda s: [])

    content = "hosts:\n  - name: test-save-node\n    kind: linux\n"
    res = client.post("/api/inventory/raw", json={"content": content})
    assert res.status_code == 200, res.json()
    data = res.json()
    assert data["ok"] is True
    assert test_file.is_file()
    assert test_file.read_text(encoding="utf-8") == content


def test_inventory_yml_resolution(tmp_path):
    yml_file = tmp_path / "inventory.yml"
    yml_file.write_text("hosts:\n  - name: yml-node\n", encoding="utf-8")

    s = Settings(
        inventory_paths=[tmp_path / "inventory.yaml"],
        db_path=tmp_path / "test.db",
    )
    # Since inventory.yaml does not exist but inventory.yml exists, it should resolve to inventory.yml
    resolved = s.resolved_inventory_paths
    assert len(resolved) == 1
    assert resolved[0] == yml_file
    assert s.primary_inventory_path == yml_file
