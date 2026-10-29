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
from agent_fleet.services import notification_service
from agent_fleet.services.notification_service import (
    NotificationService,
    build_details_url,
    build_event_payload,
    generate_event_id,
    sanitize_error,
    sanitize_url,
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
