"""Tests for quota authentication settings API (Antigravity client secrets, etc)."""

from __future__ import annotations

import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from agent_fleet.main import app
from agent_fleet.services import antigravity_quota
from agent_fleet.settings import Settings


@pytest.fixture()
def client(fleet_db, monkeypatch):
    monkeypatch.setenv("AGENT_FLEET_INCLUDE_LOCAL_RUNTIME", "false")
    with TestClient(app) as c:
        yield c


def test_get_antigravity_settings_unconfigured(client, tmp_path, monkeypatch):
    # Isolated clients file
    test_clients_file = tmp_path / "antigravity_clients.json"
    monkeypatch.setattr(antigravity_quota, "clients_file", lambda: test_clients_file)
    monkeypatch.delenv(antigravity_quota.CLIENT_SECRETS_ENV, raising=False)

    res = client.get("/api/quota-settings/antigravity")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    clients = data["clients"]
    assert len(clients) >= 2
    # Default client should be first and marked
    default_c = next(c for c in clients if c["is_default"])
    assert default_c["client_id"] == antigravity_quota.DEFAULT_CLIENT
    assert default_c["configured"] is False
    assert default_c["masked_secret"] is None


def test_save_and_get_antigravity_settings(client, tmp_path, monkeypatch):
    test_clients_file = tmp_path / "antigravity_clients.json"
    monkeypatch.setattr(antigravity_quota, "clients_file", lambda: test_clients_file)
    monkeypatch.delenv(antigravity_quota.CLIENT_SECRETS_ENV, raising=False)

    cid = antigravity_quota.DEFAULT_CLIENT
    secret = "GOCSPX-secret123456789"

    # Save secret
    res_post = client.post(
        "/api/quota-settings/antigravity",
        json={"secrets": {cid: secret}},
    )
    assert res_post.status_code == 200
    data_post = res_post.json()
    assert data_post["ok"] is True
    updated_client = next(c for c in data_post["clients"] if c["client_id"] == cid)
    assert updated_client["configured"] is True
    assert updated_client["masked_secret"].startswith("GOCSPX")
    assert secret not in updated_client["masked_secret"]  # Never leaks raw secret

    # Check file on disk
    assert test_clients_file.is_file()
    on_disk = json.loads(test_clients_file.read_text(encoding="utf-8"))
    assert on_disk.get(cid) == secret

    # Check GET reflects saved state
    res_get = client.get("/api/quota-settings/antigravity")
    assert res_get.status_code == 200
    get_client = next(c for c in res_get.json()["clients"] if c["client_id"] == cid)
    assert get_client["configured"] is True
    assert get_client["source"] == "file"


def test_save_antigravity_rejects_unknown_client_id(client):
    res = client.post(
        "/api/quota-settings/antigravity",
        json={"secrets": {"unknown-client-id.apps.googleusercontent.com": "secret"}},
    )
    assert res.status_code == 400
    assert "不受支持的 Client ID" in res.json()["detail"]
