"""Unit tests for Notification Service and Webhook APIs."""

import hashlib
import hmac
import json
import uuid
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_fleet.main import app
from agent_fleet.services import attention_service, notification_service
from agent_fleet.services.notification_service import (
    NotificationService,
    WebhookValidationError,
    build_details_url,
    build_event_payload,
    generate_event_id,
    mask_url_credentials,
    sanitize_error,
    sanitize_url,
    validate_webhook_url,
)
from agent_fleet.settings import Settings


@pytest.fixture(autouse=True)
def reset_notification_service(tmp_path):
    """Ensure every test runs with an isolated NotificationService instance and clean state."""
    service = notification_service.get_notification_service()
    service.settings.data_dir = tmp_path
    service.settings.webhook_url = None
    service.settings.webhook_secret = None
    service.settings.webhook_enabled = False
    service.settings.base_url = "http://localhost:8000"
    service.clear_state()
    yield service
    service.clear_state()


def test_event_formatting_and_stable_event_ids():
    """Verify event payload structure and stable deterministic event UUIDs."""
    item = {
        "id": "host:node-1:scan_failed",
        "type": "scan_failed",
        "severity": "error",
        "target_type": "host",
        "target_id": "node-1",
        "target_name": "worker-1",
        "message": "Scan failed on worker-1: SSH timeout",
        "first_detected_at": "2026-10-29T10:00:00Z",
        "last_updated_at": "2026-10-29T10:00:00Z",
        "action_view": "machines",
        "action_payload": {"host_id": "node-1"},
    }

    # Verify stable event_id generation
    id1 = generate_event_id("attention_created", item["id"], item["first_detected_at"])
    id2 = generate_event_id("attention_created", item["id"], item["first_detected_at"])
    assert id1 == id2
    # Verify it is a valid UUID
    parsed_uuid = uuid.UUID(id1)
    assert str(parsed_uuid) == id1

    # Verify event payload structure
    payload = build_event_payload(
        event_type="attention_created",
        item=item,
        base_url="http://controller.local:8000",
    )

    assert payload["event_id"] == id1
    assert payload["event_type"] == "attention_created"
    assert "timestamp" in payload
    assert payload["item"] == item
    assert payload["summary"] == "[ERROR] Scan failed on worker-1: SSH timeout"
    assert payload["details_url"] == "http://controller.local:8000/#machines?host=node-1"

    # Verify resolved payload formatting
    res_payload = build_event_payload(
        event_type="attention_resolved",
        item=item,
        base_url="http://controller.local:8000",
    )
    assert res_payload["event_type"] == "attention_resolved"
    assert res_payload["summary"] == "[RESOLVED] Scan failed on worker-1: SSH timeout"


def test_successful_dispatch_to_mock_http_server():
    """Verify successful dispatch with headers, HMAC signature, and Bearer token."""
    service = notification_service.get_notification_service()
    service.settings.webhook_url = "https://hooks.example.com/alerts"
    service.settings.webhook_secret = "super-secret-key-123"
    service.settings.webhook_enabled = True

    event = {
        "event_id": str(uuid.uuid4()),
        "event_type": "attention_created",
        "timestamp": "2026-10-29T10:00:00Z",
        "item": {"id": "task:1:task_blocked", "message": "Task blocked"},
        "summary": "[WARNING] Task blocked",
        "details_url": "http://localhost:8000/#tasks",
    }

    mock_resp = httpx.Response(200, json={"status": "ok"})

    with patch.object(httpx.Client, "post", return_value=mock_resp) as mock_post:
        ok, err = service.dispatch_payload(payload=event)
        assert ok is True
        assert err is None
        assert mock_post.call_count == 1

        args, kwargs = mock_post.call_args
        assert args[0] == "https://hooks.example.com/alerts"
        headers = kwargs["headers"]
        assert headers["Content-Type"] == "application/json"
        assert headers["User-Agent"] == "AgentFleet-Notifier/1.0"
        assert headers["Authorization"] == "Bearer super-secret-key-123"

        # Check HMAC-SHA256 signature
        body_bytes = kwargs["content"]
        expected_sig = hmac.new(
            b"super-secret-key-123", body_bytes, hashlib.sha256
        ).hexdigest()
        assert headers["X-Fleet-Signature"] == f"sha256={expected_sig}"


def test_timeout_and_retry_behavior_without_raising():
    """Verify bounded timeout, max retries, exponential backoff without crashing the caller."""
    service = notification_service.get_notification_service()
    service.settings.webhook_url = "https://hooks.example.com/failing"
    service.settings.webhook_enabled = True

    event = {"event_id": str(uuid.uuid4()), "event_type": "test"}

    # Simulate network timeout on all calls
    with patch.object(
        httpx.Client,
        "post",
        side_effect=httpx.TimeoutException("Connection timed out after 3.0s"),
    ) as mock_post, patch("time.sleep") as mock_sleep:
        ok, err = service.dispatch_payload(
            payload=event,
            max_retries=2,
            initial_backoff=0.1,
        )

        assert ok is False
        assert err is not None
        assert "Connection timed out" in err
        # 1 initial attempt + 2 retries = 3 attempts total
        assert mock_post.call_count == 3
        # Exponential backoff sleeps twice (0.1, 0.2)
        assert mock_sleep.call_count == 2
        mock_sleep.assert_any_call(0.1)
        mock_sleep.assert_any_call(0.2)


def test_disabled_state_no_http_calls_emitted():
    """Verify that when webhook is disabled or URL is missing, zero network calls are made."""
    service = notification_service.get_notification_service()
    service.settings.webhook_enabled = False
    service.settings.webhook_url = None

    items = [
        {
            "id": "host:node-1:scan_failed",
            "type": "scan_failed",
            "severity": "error",
            "message": "Scan failed",
        }
    ]

    with patch.object(httpx.Client, "post") as mock_post:
        dispatched = service.process_attention_items(items)
        assert len(dispatched) == 0
        mock_post.assert_not_called()

    # Even with URL set, if webhook_enabled is False, no calls are made
    service.settings.webhook_url = "https://hooks.example.com/webhook"
    service.settings.webhook_enabled = False

    with patch.object(httpx.Client, "post") as mock_post:
        dispatched = service.process_attention_items(items)
        assert len(dispatched) == 0
        mock_post.assert_not_called()


def test_attention_lifecycle_and_deduplication():
    """Verify state tracking: created on first appearance, deduplicated on re-poll, resolved on recovery."""
    service = notification_service.get_notification_service()
    service.settings.webhook_enabled = True
    service.settings.webhook_url = "https://hooks.example.com/webhook"

    item = {
        "id": "host:node-1:scan_failed",
        "type": "scan_failed",
        "severity": "error",
        "message": "SSH connect failed",
        "first_detected_at": "2026-10-29T10:00:00Z",
    }

    mock_resp = httpx.Response(200, json={"ok": True})

    with patch.object(httpx.Client, "post", return_value=mock_resp) as mock_post:
        # Poll 1: New anomaly detected -> attention_created dispatched
        events = service.process_attention_items([item])
        assert len(events) == 1
        assert events[0]["event_type"] == "attention_created"
        assert mock_post.call_count == 1

        # Poll 2: Same anomaly still active -> deduplicated, zero new calls
        mock_post.reset_mock()
        events_poll2 = service.process_attention_items([item])
        assert len(events_poll2) == 0
        mock_post.assert_not_called()

        # Poll 3: Anomaly resolved (empty list) -> attention_resolved dispatched
        mock_post.reset_mock()
        events_poll3 = service.process_attention_items([])
        assert len(events_poll3) == 1
        assert events_poll3[0]["event_type"] == "attention_resolved"
        assert mock_post.call_count == 1

        # Poll 4: Still resolved -> zero new calls
        mock_post.reset_mock()
        events_poll4 = service.process_attention_items([])
        assert len(events_poll4) == 0
        mock_post.assert_not_called()


def test_api_config_and_test_webhook_endpoints(client: TestClient):
    """Verify GET/PUT /api/notifications/config and POST /api/notifications/test-webhook."""
    # 1. GET initial config (defaults)
    res = client.get("/api/notifications/config")
    assert res.status_code == 200
    data = res.json()
    assert data["webhook_enabled"] is False
    assert data["webhook_url"] is None
    assert data["has_secret"] is False
    assert data["webhook_secret_masked"] is None

    # 2. PUT config update with secret
    update_res = client.put(
        "/api/notifications/config",
        json={
            "webhook_enabled": True,
            "webhook_url": "https://alerts.corp.internal/webhook",
            "webhook_secret": "my-secret-token-xyz",
            "base_url": "http://fleet.internal:8000",
        },
    )
    assert update_res.status_code == 200
    cfg = update_res.json()
    assert cfg["webhook_enabled"] is True
    assert cfg["webhook_url"] == "https://alerts.corp.internal/webhook"
    assert cfg["has_secret"] is True
    assert cfg["webhook_secret_masked"] == "******"
    # Never leak plaintext secret
    assert "my-secret-token-xyz" not in json.dumps(cfg)

    # 3. POST /api/notifications/test-webhook without URL (when not configured) returns 400
    with patch.object(
        notification_service.get_notification_service().settings,
        "webhook_url",
        None,
    ):
        no_url_res = client.post("/api/notifications/test-webhook", json={})
        assert no_url_res.status_code == 400

    # 4. POST /api/notifications/test-webhook successful dispatch
    mock_resp = httpx.Response(200, json={"received": True})
    with patch("agent_fleet.services.notification_service.httpx.Client") as mock_client_cls:
        mock_instance = MagicMock()
        mock_instance.__enter__.return_value = mock_instance
        mock_instance.post.return_value = mock_resp
        mock_client_cls.return_value = mock_instance

        test_res = client.post(
            "/api/notifications/test-webhook",
            json={"webhook_url": "https://test.destination.com/hook"},
        )
        assert test_res.status_code == 200
        res_json = test_res.json()
        assert res_json["ok"] is True
        assert "event_id" in res_json
        assert mock_instance.post.call_count == 1

    # 5. POST /api/notifications/test-webhook failing remote returns 502
    with patch("agent_fleet.services.notification_service.httpx.Client") as mock_client_cls:
        mock_instance = MagicMock()
        mock_instance.__enter__.return_value = mock_instance
        mock_instance.post.side_effect = httpx.ConnectError("Connection refused by remote host")
        mock_client_cls.return_value = mock_instance

        test_res = client.post(
            "/api/notifications/test-webhook",
            json={"webhook_url": "https://down.destination.com/hook"},
        )
        assert test_res.status_code == 502
        assert "Connection refused" in test_res.json()["detail"]


def test_url_and_secret_sanitization():
    """Verify credentials and secret tokens are stripped from logs and error messages."""
    dirty_url = "https://user:password123@example.com/hook?token=secret456&sig=789&debug=true"
    clean_url = sanitize_url(dirty_url)
    assert "password123" not in clean_url
    assert "user:" not in clean_url
    assert "secret456" not in clean_url
    assert "789" not in clean_url
    assert "debug=true" in clean_url

    err = Exception("Failed calling https://hooks.example.com with secret my-confidential-token-999")
    clean_msg = sanitize_error(err, secret="my-confidential-token-999")
    assert "my-confidential-token-999" not in clean_msg
    assert "***" in clean_msg


def test_pagination_decoupling_prevents_false_resolved():
    """Verify that paginated GET requests (limit < total) never falsely resolve items beyond the limit."""
    service = notification_service.get_notification_service()
    service.settings.webhook_enabled = True
    service.settings.webhook_url = "https://hooks.example.com/alerts"

    # Create 25 mock attention items
    mock_items = [
        {
            "id": f"host:node-{i}:scan_failed",
            "type": "scan_failed",
            "severity": "error",
            "message": f"Scan failed on node {i}",
            "first_detected_at": "2026-10-29T10:00:00Z",
        }
        for i in range(25)
    ]

    mock_resp = httpx.Response(200, json={"status": "ok"})

    with patch.object(attention_service, "_collect_attention_items", return_value=list(mock_items)):
        with patch.object(httpx.Client, "post", return_value=mock_resp) as mock_post:
            # Poll 1: Fetch with limit=10 (only 10 of 25 returned in items list)
            summary1 = attention_service.get_attention_summary(limit=10)
            assert summary1["total"] == 25
            assert len(summary1["items"]) == 10

            # Pass paginated items list directly (simulating routes.py behavior)
            events1 = service.process_attention_items(summary1["items"])
            # All 25 active items must be processed and tracked, not just 10
            assert len(events1) == 25
            assert len(service._active_items) == 25

            # Poll 2: Fetch with smaller limit=5
            mock_post.reset_mock()
            summary2 = attention_service.get_attention_summary(limit=5)
            assert summary2["total"] == 25
            assert len(summary2["items"]) == 5

            # Process again: items 6..25 must NOT be marked resolved!
            events2 = service.process_attention_items(summary2["items"])
            assert len(events2) == 0  # Zero new events, zero false resolves
            assert len(service._active_items) == 25
            mock_post.assert_not_called()

            # Poll 3: Use helper process_attention_summary
            events3 = attention_service.process_attention_summary(summary2)
            assert len(events3) == 0
            assert len(service._active_items) == 25

    # Poll 4: Node 0 actually recovers (genuine resolution of 1 item)
    recovered_items = [it for it in mock_items if it["id"] != "host:node-0:scan_failed"]
    assert len(recovered_items) == 24

    with patch.object(attention_service, "_collect_attention_items", return_value=list(recovered_items)):
        with patch.object(httpx.Client, "post", return_value=mock_resp) as mock_post:
            summary4 = attention_service.get_attention_summary(limit=5)
            assert summary4["total"] == 24
            assert len(summary4["items"]) == 5

            events4 = service.process_attention_items(summary4["items"])
            # Exactly 1 event should be dispatched: attention_resolved for node-0!
            assert len(events4) == 1
            assert events4[0]["event_type"] == "attention_resolved"
            assert events4[0]["item"]["id"] == "host:node-0:scan_failed"
            assert len(service._active_items) == 24
            assert "host:node-0:scan_failed" not in service._active_items


def test_non_blocking_background_dispatch():
    """Verify non-blocking background dispatch returns immediately without blocking caller."""
    service = notification_service.get_notification_service()
    service.settings.webhook_enabled = True
    service.settings.webhook_url = "https://hooks.example.com/webhook"

    item = {
        "id": "host:bg-test:scan_failed",
        "type": "scan_failed",
        "severity": "error",
        "message": "SSH failure",
        "first_detected_at": "2026-10-29T12:00:00Z",
    }

    mock_resp = httpx.Response(200, json={"status": "ok"})

    with patch.object(httpx.Client, "post", return_value=mock_resp) as mock_post:
        # Calling with background=True must return events immediately
        events = service.process_attention_items([item], background=True)
        assert len(events) == 1
        event_id = events[0]["event_id"]

        # Wait for background task to complete
        service.wait_for_background_tasks(timeout=3.0)

        assert mock_post.call_count == 1
        outbox = service.get_outbox()
        assert event_id in outbox
        assert outbox[event_id]["status"] == "sent"


def test_outbox_failure_persistence_and_safe_retries_without_storms():
    """Verify failed deliveries are recorded in persistent outbox with backoff and safe retries."""
    service = notification_service.get_notification_service()
    service.settings.webhook_enabled = True
    service.settings.webhook_url = "https://hooks.example.com/outbox-fail"

    item = {
        "id": "host:fail-node:scan_failed",
        "type": "scan_failed",
        "severity": "error",
        "message": "Timeout to agent",
        "first_detected_at": "2026-10-29T12:00:00Z",
    }

    # Simulate HTTP failure
    with patch.object(
        httpx.Client,
        "post",
        side_effect=httpx.ConnectTimeout("Connection to hook timed out"),
    ) as mock_post:
        events = service.process_attention_items([item], background=False)
        assert len(events) == 1
        event_id = events[0]["event_id"]

        # Outbox must record the event and failure state
        outbox = service.get_outbox()
        assert event_id in outbox
        entry = outbox[event_id]
        assert entry["status"] == "failed"
        assert entry["retry_count"] == 1
        assert "Connection to hook timed out" in entry["last_error"]
        assert entry["next_retry_at"] is not None

        # Verify persisted file on disk
        assert service.outbox_file.exists()

        # Re-poll with the same anomaly active
        mock_post.reset_mock()
        events_repoll = service.process_attention_items([item], background=False)
        assert len(events_repoll) == 0
        # No extra network call during ordinary polling
        mock_post.assert_not_called()

        # Retry with force=False before next_retry_at arrives -> must be skipped (no storm)
        retry_res1 = service.retry_outbox(force=False)
        assert len(retry_res1) == 0
        mock_post.assert_not_called()

    # Now simulated recovery of webhook endpoint
    mock_success = httpx.Response(200, json={"received": True})
    with patch.object(httpx.Client, "post", return_value=mock_success) as mock_post:
        # Force retry to simulate retry after backoff elapsed
        retry_res2 = service.retry_outbox(force=True)
        assert len(retry_res2) == 1
        assert retry_res2[0]["ok"] is True
        assert mock_post.call_count == 1

        # Outbox status updated to sent
        updated_entry = service.get_outbox()[event_id]
        assert updated_entry["status"] == "sent"
        assert updated_entry["last_error"] is None
        assert updated_entry["next_retry_at"] is None


def test_ssrf_validation_and_url_security(client: TestClient):
    """Verify SSRF protection blocks private, loopback, and cloud metadata destinations."""
    # 1. Direct validation checks
    assert validate_webhook_url("ftp://example.com/hook")[0] is False
    assert validate_webhook_url("javascript:alert(1)")[0] is False
    assert validate_webhook_url("http://127.0.0.1:8080/hook")[0] is False
    assert validate_webhook_url("http://localhost:8080/hook")[0] is False
    assert validate_webhook_url("http://sub.localhost:8080/hook")[0] is False
    assert validate_webhook_url("http://[::1]:8080/hook")[0] is False
    assert validate_webhook_url("http://169.254.169.254/latest/meta-data")[0] is False
    assert validate_webhook_url("http://10.0.0.1/hook")[0] is False
    assert validate_webhook_url("http://192.168.1.1/hook")[0] is False
    assert validate_webhook_url("http://172.16.0.1/hook")[0] is False

    # Valid external URLs allowed
    assert validate_webhook_url("https://hooks.example.com/alerts")[0] is True
    assert validate_webhook_url("https://alerts.corp.internal/webhook")[0] is True

    # allow_local flag allows local in test environments
    assert validate_webhook_url("http://127.0.0.1:8080/hook", allow_local=True)[0] is True

    # 2. PUT /api/notifications/config blocks SSRF URLs with 400
    res_loopback = client.put(
        "/api/notifications/config",
        json={"webhook_url": "http://127.0.0.1:8000/internal-admin"},
    )
    assert res_loopback.status_code == 400
    assert "Invalid webhook URL" in res_loopback.json()["detail"]

    res_metadata = client.put(
        "/api/notifications/config",
        json={"webhook_url": "http://169.254.169.254/latest/meta-data"},
    )
    assert res_metadata.status_code == 400
    assert "cloud metadata" in res_metadata.json()["detail"]

    # 3. POST /api/notifications/test-webhook with SSRF URL returns 400
    res_test = client.post(
        "/api/notifications/test-webhook",
        json={"webhook_url": "http://127.0.0.1:8000/evil"},
    )
    assert res_test.status_code == 400


def test_credential_masking_in_config(client: TestClient):
    """Verify that credentials and tokens embedded in URLs are masked in config representations."""
    # 1. URL masking unit checks
    basic_auth_url = "https://user:mypassword123@hooks.example.com/webhook"
    masked1 = mask_url_credentials(basic_auth_url)
    assert "mypassword123" not in masked1
    assert "user" not in masked1
    assert "***:***@hooks.example.com/webhook" in masked1

    query_token_url = "https://hooks.example.com/alert?token=my-secret-key-456&env=prod"
    masked2 = mask_url_credentials(query_token_url)
    assert "my-secret-key-456" not in masked2
    assert "token=***" in masked2
    assert "env=prod" in masked2

    slack_url = "https://hooks.slack.com/services/T12345678/B12345678/abcdefghijklmnopqrstuvwxyz"
    masked3 = mask_url_credentials(slack_url)
    assert "abcdefghijklmnopqrstuvwxyz" not in masked3
    assert "https://hooks.slack.com/services/T12345678/B12345678/***" == masked3

    # 2. Configure via API and verify GET response masks URL
    res_put = client.put(
        "/api/notifications/config",
        json={
            "webhook_enabled": True,
            "webhook_url": "https://hooks.example.com/alert?token=super-secret-key-999",
            "webhook_secret": "my-hmac-secret",
        },
    )
    assert res_put.status_code == 200
    data = res_put.json()
    assert data["has_url"] is True
    assert "super-secret-key-999" not in json.dumps(data)
    assert "token=***" in data["masked_url"]
    assert data["has_secret"] is True
    assert data["webhook_secret_masked"] == "******"

    # 3. Updating with masked URL retained does not corrupt stored URL
    res_reput = client.put(
        "/api/notifications/config",
        json={
            "webhook_enabled": True,
            "webhook_url": data["masked_url"],
            "webhook_secret": "******",
        },
    )
    assert res_reput.status_code == 200
    service = notification_service.get_notification_service()
    assert service.settings.webhook_url == "https://hooks.example.com/alert?token=super-secret-key-999"
    assert service.settings.webhook_secret == "my-hmac-secret"

