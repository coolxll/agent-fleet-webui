"""Tests for API access control, write authorization boundary, and credential protection."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_fleet.api.auth import extract_token_from_headers, verify_token
from agent_fleet.main import app
from agent_fleet.settings import get_settings
from starlette.datastructures import Headers


@pytest.fixture()
def client(fleet_db, monkeypatch):
    """Test client with clean DB and local runtime disabled."""
    monkeypatch.setenv("AGENT_FLEET_INCLUDE_LOCAL_RUNTIME", "false")
    with TestClient(app) as c:
        yield c


def test_anonymous_mode_backward_compatible(client, monkeypatch):
    """When api_secret is not set, read and write APIs remain backward compatible."""
    monkeypatch.delenv("AGENT_FLEET_API_SECRET", raising=False)
    get_settings.cache_clear()

    # GET is allowed
    res_get = client.get("/api/health")
    assert res_get.status_code == 200
    assert res_get.json().get("ok") is True

    # POST is allowed without credentials
    res_post = client.post("/api/inventory/validate", json={"content": "hosts: []"})
    assert res_post.status_code == 200

    # Public settings endpoint shows auth is not required
    res_settings = client.get("/api/settings")
    assert res_settings.status_code == 200
    assert res_settings.json()["auth_required"] is False


def test_write_endpoints_reject_unauthorized_requests(client, monkeypatch):
    """When api_secret is configured, write requests without valid credentials return 401."""
    test_secret = "fleet-sec-token-xyz-12345"
    monkeypatch.setenv("AGENT_FLEET_API_SECRET", test_secret)
    get_settings.cache_clear()

    # 1. POST /api/tasks/{task_id}/handoff without auth -> 401
    res = client.post("/api/tasks/task-dummy-01/handoff", json={"notes": "test"})
    assert res.status_code == 401
    assert "Unauthorized" in res.json()["detail"]
    assert "WWW-Authenticate" in res.headers

    # 2. Host modification endpoints without auth -> 401
    res = client.post("/api/hosts/host-dummy-01/enabled", json={"enabled": False})
    assert res.status_code == 401

    res = client.delete("/api/hosts/host-dummy-01")
    assert res.status_code == 401

    # 3. Upgrade endpoints without auth -> 401
    res = client.post("/api/fleet/upgrade-all", json={})
    assert res.status_code == 401

    res = client.post("/api/installations/inst-dummy-01/upgrade", json={})
    assert res.status_code == 401

    res = client.post("/api/upgrades/batch", json={"targets": []})
    assert res.status_code == 401

    # 4. Inventory write endpoints without auth -> 401
    res = client.post("/api/inventory/raw", json={"content": "hosts: []"})
    assert res.status_code == 401

    # 5. Wrong secret via X-Agent-Fleet-Secret -> 401
    res = client.post(
        "/api/fleet/upgrade-all",
        json={},
        headers={"X-Agent-Fleet-Secret": "invalid-wrong-secret"},
    )
    assert res.status_code == 401

    # 6. Wrong secret via Bearer Authorization -> 401
    res = client.post(
        "/api/fleet/upgrade-all",
        json={},
        headers={"Authorization": "Bearer invalid-wrong-secret"},
    )
    assert res.status_code == 401

    # 7. Credential passed in query parameter must NOT be accepted (CSRF / URL leak prevention)
    res = client.post(f"/api/fleet/upgrade-all?api_secret={test_secret}", json={})
    assert res.status_code == 401


def test_write_endpoints_succeed_with_valid_credentials(client, monkeypatch):
    """When api_secret is configured, valid credentials allow write operations."""
    test_secret = "fleet-sec-token-xyz-12345"
    monkeypatch.setenv("AGENT_FLEET_API_SECRET", test_secret)
    get_settings.cache_clear()

    # Using Header: X-Agent-Fleet-Secret
    res_custom = client.post(
        "/api/inventory/validate",
        json={"content": "hosts: []"},
        headers={"X-Agent-Fleet-Secret": test_secret},
    )
    assert res_custom.status_code == 200
    assert res_custom.json().get("valid") is True

    # Using Header: Authorization: Bearer <secret>
    res_bearer = client.post(
        "/api/inventory/validate",
        json={"content": "hosts: []"},
        headers={"Authorization": f"Bearer {test_secret}"},
    )
    assert res_bearer.status_code == 200
    assert res_bearer.json().get("valid") is True


def test_readonly_endpoints_remain_accessible_by_default(client, monkeypatch):
    """When api_secret is configured, read-only GET requests remain accessible by default."""
    test_secret = "fleet-sec-token-xyz-12345"
    monkeypatch.setenv("AGENT_FLEET_API_SECRET", test_secret)
    monkeypatch.setenv("AGENT_FLEET_ALLOW_ANONYMOUS_READONLY", "true")
    get_settings.cache_clear()

    # GET requests work without credentials
    res_health = client.get("/api/health")
    assert res_health.status_code == 200

    res_hosts = client.get("/api/hosts")
    assert res_hosts.status_code == 200

    res_agents = client.get("/api/agents")
    assert res_agents.status_code == 200

    res_settings = client.get("/api/settings")
    assert res_settings.status_code == 200


def test_strict_mode_blocks_readonly_without_secret(client, monkeypatch):
    """When allow_anonymous_readonly is False, even GET endpoints require credentials."""
    test_secret = "fleet-sec-token-xyz-12345"
    monkeypatch.setenv("AGENT_FLEET_API_SECRET", test_secret)
    monkeypatch.setenv("AGENT_FLEET_ALLOW_ANONYMOUS_READONLY", "false")
    get_settings.cache_clear()

    # Anonymous GET is blocked with 401
    res_blocked = client.get("/api/hosts")
    assert res_blocked.status_code == 401

    # Authenticated GET succeeds
    res_ok = client.get("/api/hosts", headers={"X-Agent-Fleet-Secret": test_secret})
    assert res_ok.status_code == 200


def test_settings_endpoint_never_leaks_api_secret(client, monkeypatch):
    """Verify that /api/settings and settings representation never disclose the raw secret."""
    sensitive_secret = "super-confidential-secret-key-999888"
    monkeypatch.setenv("AGENT_FLEET_API_SECRET", sensitive_secret)
    get_settings.cache_clear()

    settings = get_settings()

    # 1. API endpoint response check
    res = client.get("/api/settings")
    assert res.status_code == 200
    data = res.json()

    assert data["auth_required"] is True
    assert "api_secret" not in data
    assert sensitive_secret not in res.text

    # 2. settings.safe_dict() check
    safe_data = settings.safe_dict()
    assert "api_secret" not in safe_data
    assert safe_data["auth_required"] is True

    # 3. String & repr representation masking check (for logging safety)
    rep = repr(settings)
    assert sensitive_secret not in rep
    assert "'***'" in rep

    s = str(settings)
    assert sensitive_secret not in s


def test_credential_sanitization_and_timing_defense():
    """Unit checks for token extraction sanitization and timing attack prevention."""
    expected = "valid-secret-key"

    # CRLF or null bytes are rejected
    malformed_headers = Headers({"x-agent-fleet-secret": "valid-secret-key\r\ninjected-header: 1"})
    assert extract_token_from_headers(malformed_headers) is None

    null_headers = Headers({"authorization": "Bearer valid-secret-key\0malicious"})
    assert extract_token_from_headers(null_headers) is None

    # Normal header extracted properly
    valid_headers = Headers({"x-agent-fleet-secret": "  valid-secret-key  "})
    assert extract_token_from_headers(valid_headers) == "valid-secret-key"

    # Constant time verification
    assert verify_token("valid-secret-key", expected) is True
    assert verify_token("wrong-secret-key", expected) is False
    assert verify_token(None, expected) is False
    assert verify_token("", expected) is False
    assert verify_token(expected, None) is False
