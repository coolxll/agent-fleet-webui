"""End-to-End Onboarding Integration and Acceptance Tests (Issue #54).

Simulates a brand-new user journey starting from an empty, clean temporary SQLite database:
  1. Fresh State Verification: clean DB, empty/unconfigured fleet, attention summary clean.
  2. Authentication Enforcement: write protection with AGENT_FLEET_API_SECRET, 401 handling,
     Bearer token authentication.
  3. Host Onboarding & SSH Testing: add remote host, test SSH connectivity with mock failure
     (structured failure category & suggestion per #47) and mock success (latency & uname).
  4. First Fleet Probe Scan: never scanned -> in progress -> scan succeeded, facts persisted
     (agents, versions, models), attention anomaly list resolved per #59.
  5. Error Recovery: recoverable workflow (failed SSH/scan -> fix settings -> retest -> scan
     succeeds without data corruption).
"""

from __future__ import annotations

import subprocess
import time
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from agent_fleet import models
from agent_fleet.db import session_scope
from agent_fleet.main import app
from agent_fleet.settings import get_settings
from test_scan import FakeRunner, _codex_stream

TEST_API_SECRET = "fleet-sec-onboarding-token-998877"
AUTH_HEADER = {"Authorization": f"Bearer {TEST_API_SECRET}"}
INVALID_AUTH_HEADER = {"Authorization": "Bearer bad-invalid-token"}


@pytest.fixture()
def onboarding_client(fleet_db, monkeypatch):
    """Clean test client with local runtime disabled."""
    monkeypatch.setenv("AGENT_FLEET_INCLUDE_LOCAL_RUNTIME", "false")
    monkeypatch.delenv("AGENT_FLEET_API_SECRET", raising=False)
    get_settings.cache_clear()
    with TestClient(app) as c:
        yield c


def _mock_ssh_process(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
):
    def _run(cmd, *args, **kwargs):
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )

    return _run


def _wait_for_scan(
    client: TestClient,
    run_id: str,
    max_retries: int = 30,
    interval: float = 0.05,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Poll scan status until it reaches a terminal state."""
    for _ in range(max_retries):
        res = client.get(f"/api/scan/{run_id}", headers=headers or {})
        assert res.status_code == 200
        data = res.json()
        if data["status"] in ("completed", "failed", "partial", "interrupted"):
            return data
        time.sleep(interval)
    raise TimeoutError(f"Scan run {run_id} did not complete within allotted timeout.")


def test_complete_onboarding_journey_from_clean_state(onboarding_client, fleet_db, monkeypatch):
    """Complete end-to-end user onboarding lifecycle test covering Steps 1 through 5."""
    client = onboarding_client

    # =========================================================================
    # Step 1: Fresh State Verification
    # =========================================================================
    # Verify inventory is completely clean and empty
    res_hosts = client.get("/api/inventory/hosts")
    assert res_hosts.status_code == 200
    hosts_data = res_hosts.json()
    assert hosts_data["ok"] is True
    assert hosts_data["hosts"] == []
    assert hosts_data["total"] == 0

    # Verify overview shows zero hosts, zero runtimes, zero installations
    res_overview = client.get("/api/overview")
    assert res_overview.status_code == 200
    ov_data = res_overview.json()
    assert ov_data["totals"]["runtimes"] == 0
    assert ov_data["totals"]["online"] == 0
    assert ov_data["totals"]["installations"] == 0

    # Verify attention summary reflects unconfigured clean state
    res_attention = client.get("/api/attention")
    assert res_attention.status_code == 200
    att_data = res_attention.json()
    assert att_data["total"] == 0
    assert att_data["items"] == []

    # =========================================================================
    # Step 2: Authentication Enforcement
    # =========================================================================
    # Enable API Secret to enforce write boundary
    monkeypatch.setenv("AGENT_FLEET_API_SECRET", TEST_API_SECRET)
    get_settings.cache_clear()

    # Public settings reflects auth requirement
    res_settings = client.get("/api/settings")
    assert res_settings.status_code == 200
    assert res_settings.json()["auth_required"] is True

    # Attempt to add host without authorization -> 401 Unauthorized
    payload = {
        "name": "edge-corp-01",
        "hostname": "192.168.12.101",
        "ssh_user": "fleetops",
        "ssh_port": 22,
    }
    res_unauth = client.post("/api/inventory/hosts", json=payload)
    assert res_unauth.status_code == 401
    assert "WWW-Authenticate" in res_unauth.headers
    assert "Unauthorized" in res_unauth.json()["detail"]

    # Attempt with invalid Bearer token -> 401 Unauthorized
    res_bad_token = client.post(
        "/api/inventory/hosts",
        json=payload,
        headers=INVALID_AUTH_HEADER,
    )
    assert res_bad_token.status_code == 401

    # Attempt with invalid X-Agent-Fleet-Secret header -> 401 Unauthorized
    res_bad_header = client.post(
        "/api/inventory/hosts",
        json=payload,
        headers={"X-Agent-Fleet-Secret": "wrong-secret"},
    )
    assert res_bad_header.status_code == 401

    # Authenticated write with valid Bearer token -> 200 OK
    res_create = client.post(
        "/api/inventory/hosts",
        json=payload,
        headers=AUTH_HEADER,
    )
    assert res_create.status_code == 200
    created = res_create.json()
    assert created["ok"] is True
    host = created["host"]
    host_id = host["id"]
    assert host["name"] == "edge-corp-01"
    assert host["hostname"] == "192.168.12.101"

    # =========================================================================
    # Step 3: Host Onboarding & SSH Testing
    # =========================================================================
    # 3a. Mock SSH Failure: Permission denied (publickey)
    with patch(
        "agent_fleet.services.sqlite_inventory.subprocess.run",
        side_effect=_mock_ssh_process(
            returncode=255,
            stderr="Permission denied (publickey).",
        ),
    ):
        res_ssh_fail = client.post(
            f"/api/inventory/hosts/{host_id}/test-ssh",
            headers=AUTH_HEADER,
        )
        assert res_ssh_fail.status_code == 200
        fail_data = res_ssh_fail.json()
        assert fail_data["ok"] is False
        assert fail_data["failure_category"] == "permission_denied"
        assert "authorized_keys" in fail_data["suggestion"]

    # 3b. Mock SSH Failure: Operation timed out
    with patch(
        "agent_fleet.services.sqlite_inventory.subprocess.run",
        side_effect=_mock_ssh_process(
            returncode=255,
            stderr="ssh: connect to host 192.168.12.101 port 22: Operation timed out",
        ),
    ):
        res_timeout = client.post(
            f"/api/inventory/hosts/{host_id}/test-ssh",
            headers=AUTH_HEADER,
        )
        assert res_timeout.status_code == 200
        timeout_data = res_timeout.json()
        assert timeout_data["ok"] is False
        assert timeout_data["failure_category"] == "timeout"
        assert "firewall" in timeout_data["suggestion"].lower() or "network" in timeout_data["suggestion"].lower()

    # 3c. Mock SSH Success: Connected cleanly
    with patch(
        "agent_fleet.services.sqlite_inventory.subprocess.run",
        side_effect=_mock_ssh_process(
            returncode=0,
            stdout="Linux 6.6.0-generic x86_64\n",
        ),
    ):
        res_ssh_ok = client.post(
            f"/api/inventory/hosts/{host_id}/test-ssh",
            headers=AUTH_HEADER,
        )
        assert res_ssh_ok.status_code == 200
        ok_data = res_ssh_ok.json()
        assert ok_data["ok"] is True
        assert "fleetops@" in ok_data["host"]
        assert ok_data["uname"] == "Linux 6.6.0-generic x86_64"
        assert ok_data["latency_ms"] >= 0

    # =========================================================================
    # Step 4: First Fleet Probe Scan
    # =========================================================================
    # Prior to any probe scan, attention flags host as never scanned
    res_att_before = client.get("/api/attention")
    assert res_att_before.status_code == 200
    att_before = res_att_before.json()
    assert att_before["total"] == 1
    item = att_before["items"][0]
    assert item["type"] == "scan_stale"
    assert item["action_payload"]["subtype"] == "never_scanned"
    assert item["target_id"] == host_id

    # Configure scanner with FakeRunner simulating a host with Codex installed
    monkeypatch.setattr(
        "agent_fleet.services.scanner.build_runner",
        lambda rt, s: FakeRunner(_codex_stream()),
    )

    # Trigger scan via POST /api/scan
    res_scan = client.post(
        "/api/scan",
        json={"all": True},
        headers=AUTH_HEADER,
    )
    assert res_scan.status_code == 200
    scan_meta = res_scan.json()
    run_id = scan_meta["run_id"]
    assert scan_meta["total"] == 1

    # Verify scan transitions to completed successfully
    completed_run = _wait_for_scan(client, run_id, headers=AUTH_HEADER)
    assert completed_run["status"] == "completed"
    assert completed_run["succeeded"] == 1
    assert completed_run["failed"] == 0
    assert len(completed_run["tasks"]) == 1
    assert completed_run["tasks"][0]["status"] == "ok"
    assert completed_run["tasks"][0]["agents_found"] == 1

    # Verify scanned facts are properly stored in DB
    res_inst = client.get("/api/installations?agent_id=codex")
    assert res_inst.status_code == 200
    installations = res_inst.json()["installations"]
    assert len(installations) == 1
    inst = installations[0]
    assert inst["agent"] == "codex"
    assert inst["detected"] is True
    assert inst["version"] == "1.2.3"

    # Verify installation detail and configuration facts
    res_detail = client.get(f"/api/installations/{inst['id']}")
    assert res_detail.status_code == 200
    detail = res_detail.json()
    assert detail["status"] == "installed"
    assert detail["config"]["model"] == "gpt-5-fixture"
    assert detail["config"]["settings"].get("approval_policy") == "never"

    # Overview totals updated to reflect the scanned host
    ov_after = client.get("/api/overview").json()
    assert ov_after["totals"]["runtimes"] == 1
    assert ov_after["totals"]["online"] == 1
    assert ov_after["totals"]["installations"] == 1

    # Attention anomaly list resolves automatically: host is now healthy
    res_att_after = client.get("/api/attention")
    assert res_att_after.status_code == 200
    att_after = res_att_after.json()
    assert att_after["total"] == 0
    assert att_after["items"] == []

    # =========================================================================
    # Step 5: Error Recovery
    # =========================================================================
    # Simulate a network/configuration disruption:
    # Update host with wrong SSH port
    res_update = client.put(
        f"/api/inventory/hosts/{host_id}",
        json={"ssh_port": 9922},
        headers=AUTH_HEADER,
    )
    assert res_update.status_code == 200
    assert res_update.json()["host"]["ssh_port"] == 9922

    # SSH test fails on misconfigured port
    with patch(
        "agent_fleet.services.sqlite_inventory.subprocess.run",
        side_effect=_mock_ssh_process(
            returncode=255,
            stderr="ssh: connect to host 192.168.12.101 port 9922: Connection refused",
        ),
    ):
        res_test_bad = client.post(
            f"/api/inventory/hosts/{host_id}/test-ssh",
            headers=AUTH_HEADER,
        )
        assert res_test_bad.json()["ok"] is False
        assert res_test_bad.json()["failure_category"] == "connection_refused"

    # Simulate failing scan probe
    monkeypatch.setattr(
        "agent_fleet.services.scanner.build_runner",
        lambda rt, s: FakeRunner(error="Connection refused on port 9922"),
    )
    res_bad_scan = client.post("/api/scan", json={"all": True}, headers=AUTH_HEADER)
    assert res_bad_scan.status_code == 200
    bad_run_id = res_bad_scan.json()["run_id"]
    bad_run = _wait_for_scan(client, bad_run_id, headers=AUTH_HEADER)
    assert bad_run["status"] == "failed"
    assert bad_run["failed"] == 1

    # Attention immediately surfaces the scan_failed anomaly
    res_att_broken = client.get("/api/attention")
    assert res_att_broken.json()["total"] == 1
    broken_item = res_att_broken.json()["items"][0]
    assert broken_item["type"] == "scan_failed"
    assert "Connection refused" in broken_item["message"]

    # Verify data integrity: existing agent facts were NOT erased by the scan failure
    res_check_inst = client.get(f"/api/installations/{inst['id']}")
    assert res_check_inst.status_code == 200
    assert res_check_inst.json()["version"] == "1.2.3"

    # Fix host configuration back to port 22
    res_fix = client.put(
        f"/api/inventory/hosts/{host_id}",
        json={"ssh_port": 22},
        headers=AUTH_HEADER,
    )
    assert res_fix.status_code == 200
    assert res_fix.json()["host"]["ssh_port"] == 22

    # Retest SSH connectivity -> succeeds
    with patch(
        "agent_fleet.services.sqlite_inventory.subprocess.run",
        side_effect=_mock_ssh_process(
            returncode=0,
            stdout="Linux 6.6.0-generic x86_64\n",
        ),
    ):
        res_fixed_ssh = client.post(
            f"/api/inventory/hosts/{host_id}/test-ssh",
            headers=AUTH_HEADER,
        )
        assert res_fixed_ssh.json()["ok"] is True

    # Rescan fleet with fixed configuration
    monkeypatch.setattr(
        "agent_fleet.services.scanner.build_runner",
        lambda rt, s: FakeRunner(_codex_stream()),
    )
    res_recov_scan = client.post("/api/scan", json={"all": True}, headers=AUTH_HEADER)
    assert res_recov_scan.status_code == 200
    recov_run_id = res_recov_scan.json()["run_id"]
    recov_run = _wait_for_scan(client, recov_run_id, headers=AUTH_HEADER)
    assert recov_run["status"] == "completed"
    assert recov_run["succeeded"] == 1

    # Attention anomaly resolves back to zero errors
    res_att_recovered = client.get("/api/attention")
    assert res_att_recovered.status_code == 200
    assert res_att_recovered.json()["total"] == 0
    assert res_att_recovered.json()["items"] == []

    # Final verification: facts are completely consistent
    res_final_inst = client.get(f"/api/installations/{inst['id']}")
    assert res_final_inst.status_code == 200
    final_detail = res_final_inst.json()
    assert final_detail["status"] == "installed"
    assert final_detail["version"] == "1.2.3"
    assert final_detail["config"]["model"] == "gpt-5-fixture"


def test_fresh_state_verification(onboarding_client):
    """Step 1 Isolated Test: Empty SQLite DB initializes cleanly with 0 anomalies."""
    client = onboarding_client
    # Host inventory is empty
    assert client.get("/api/inventory/hosts").json()["total"] == 0
    # Attention is clean
    assert client.get("/api/attention").json()["total"] == 0
    # Overview totals zero
    ov = client.get("/api/overview").json()
    assert ov["totals"]["runtimes"] == 0
    assert ov["totals"]["online"] == 0
    assert ov["totals"]["installations"] == 0


def test_authentication_enforcement(onboarding_client, monkeypatch):
    """Step 2 Isolated Test: Verify write boundary enforcement and token verification."""
    client = onboarding_client
    secret = "secret-token-test-isolated"
    monkeypatch.setenv("AGENT_FLEET_API_SECRET", secret)
    get_settings.cache_clear()

    # Public endpoint reflects auth
    assert client.get("/api/settings").json()["auth_required"] is True

    # Reject without credentials
    res_unauth = client.post("/api/inventory/hosts", json={"name": "box-1"})
    assert res_unauth.status_code == 401
    assert "WWW-Authenticate" in res_unauth.headers

    # Reject with invalid token
    res_bad = client.post(
        "/api/inventory/hosts",
        json={"name": "box-1"},
        headers={"Authorization": "Bearer invalid"},
    )
    assert res_bad.status_code == 401

    # Accept with valid Bearer token
    res_ok = client.post(
        "/api/inventory/hosts",
        json={"name": "box-1", "hostname": "10.0.0.1"},
        headers={"Authorization": f"Bearer {secret}"},
    )
    assert res_ok.status_code == 200
    assert res_ok.json()["ok"] is True


def test_host_onboarding_and_ssh_diagnostics(onboarding_client, monkeypatch):
    """Step 3 Isolated Test: Verify SSH diagnostics categorization and suggestion."""
    client = onboarding_client
    host_res = client.post("/api/inventory/hosts", json={"name": "ssh-box", "hostname": "10.0.0.2"})
    assert host_res.status_code == 200
    host_id = host_res.json()["host"]["id"]

    # Test failure categorization for permission denied
    with patch(
        "agent_fleet.services.sqlite_inventory.subprocess.run",
        side_effect=_mock_ssh_process(returncode=255, stderr="Permission denied (publickey)."),
    ):
        res = client.post(f"/api/inventory/hosts/{host_id}/test-ssh")
        assert res.json()["ok"] is False
        assert res.json()["failure_category"] == "permission_denied"
        assert "authorized_keys" in res.json()["suggestion"]

    # Test success
    with patch(
        "agent_fleet.services.sqlite_inventory.subprocess.run",
        side_effect=_mock_ssh_process(returncode=0, stdout="Darwin 24.0.0 arm64\n"),
    ):
        res = client.post(f"/api/inventory/hosts/{host_id}/test-ssh")
        assert res.json()["ok"] is True
        assert res.json()["uname"] == "Darwin 24.0.0 arm64"
        assert res.json()["latency_ms"] >= 0


def test_fleet_probe_scan_and_attention_resolution(onboarding_client, monkeypatch):
    """Step 4 Isolated Test: Verify scan progression and attention anomaly resolution."""
    client = onboarding_client
    host_res = client.post("/api/inventory/hosts", json={"name": "scan-box", "hostname": "10.0.0.3"})
    host_id = host_res.json()["host"]["id"]

    # Never scanned -> attention warning
    att = client.get("/api/attention").json()
    assert att["total"] == 1
    assert att["items"][0]["action_payload"]["subtype"] == "never_scanned"

    monkeypatch.setattr(
        "agent_fleet.services.scanner.build_runner",
        lambda rt, s: FakeRunner(_codex_stream()),
    )
    res_scan = client.post("/api/scan", json={"all": True})
    run_id = res_scan.json()["run_id"]
    completed = _wait_for_scan(client, run_id)
    assert completed["status"] == "completed"

    # Scanned facts present
    insts = client.get("/api/installations?agent_id=codex").json()["installations"]
    assert len(insts) == 1
    assert insts[0]["version"] == "1.2.3"

    # Attention warning resolved
    assert client.get("/api/attention").json()["total"] == 0
