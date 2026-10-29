"""Notification Service for Agent Fleet.

Provides webhook notification delivery for attention anomaly state changes:
- Anomaly detected: `attention_created`
- Anomaly resolved: `attention_resolved`
- Synthetic test: `test`

Features:
- Stable UUID event_id for receiver deduplication.
- In-memory & file-backed state tracking to prevent duplicate webhook dispatches.
- Bounded timeout (3.0s) with exponential retry.
- Sanitized logging (never leaks secrets or tokens).
- Zero network calls when webhook is disabled or URL is missing.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import urllib.parse
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from agent_fleet.models import utcnow, iso_utc
from agent_fleet.settings import Settings, get_settings

logger = logging.getLogger(__name__)

# Fixed namespace UUID for deterministic event_id generation
NAMESPACE_NOTIFICATIONS = uuid.UUID("3c4d5e6f-7a8b-9c0d-1e2f-3a4b5c6d7e8f")


def utcnow_iso() -> str:
    """Return current UTC time in standard ISO format."""
    return iso_utc(utcnow())


def sanitize_url(url: str | None) -> str:
    """Strip authentication credentials or sensitive tokens from URL for safe logging."""
    if not url:
        return ""
    try:
        parts = urllib.parse.urlsplit(url)
        # Strip user:pass@ if present
        netloc = parts.hostname or ""
        if parts.port:
            netloc = f"{netloc}:{parts.port}"
        # If there are query parameters, mask them if they look sensitive
        query = parts.query
        if query:
            q_pairs = urllib.parse.parse_qsl(query, keep_blank_values=True)
            sanitized_q = []
            for k, v in q_pairs:
                if any(sec in k.lower() for sec in ("token", "key", "secret", "sig", "auth")):
                    sanitized_q.append((k, "***"))
                else:
                    sanitized_q.append((k, v))
            query = urllib.parse.urlencode(sanitized_q)
        return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))
    except Exception:
        return "[URL_SANITIZATION_FAILED]"


def sanitize_error(exc: Exception | None, secret: str | None = None) -> str:
    """Sanitize error message to prevent secret or password leakage."""
    if exc is None:
        return "Unknown error"
    msg = str(exc)
    if secret and secret in msg:
        msg = msg.replace(secret, "***")
    return msg


def generate_event_id(event_type: str, item_id: str, timestamp_key: str | None = None) -> str:
    """Generate a stable, deterministic UUID for a given event, item, and timestamp key."""
    parts = [event_type, item_id]
    if timestamp_key:
        parts.append(str(timestamp_key))
    raw_key = ":".join(parts)
    return str(uuid.uuid5(NAMESPACE_NOTIFICATIONS, raw_key))


def build_details_url(base_url: str | None, item: dict[str, Any]) -> str | None:
    """Construct details URL pointing to relevant WebUI view."""
    if not base_url or not str(base_url).strip():
        return None
    base = str(base_url).rstrip("/")
    action_view = item.get("action_view")
    target_id = item.get("target_id")
    if action_view == "machines":
        return f"{base}/#machines?host={target_id}" if target_id else f"{base}/#machines"
    elif action_view == "tasks":
        return f"{base}/#tasks?task={target_id}" if target_id else f"{base}/#tasks"
    return f"{base}/"


def build_event_payload(
    event_type: str,
    item: dict[str, Any],
    base_url: str | None = None,
    event_id: str | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Construct standard notification event payload dictionary."""
    ts = timestamp or utcnow_iso()
    iid = item.get("id") or "unknown_item"
    if not event_id:
        timestamp_key = item.get("first_detected_at") if event_type == "attention_created" else ts
        event_id = generate_event_id(event_type, iid, timestamp_key)

    if event_type == "attention_created":
        severity = str(item.get("severity", "warning")).upper()
        msg = item.get("message", "Attention anomaly detected")
        summary = f"[{severity}] {msg}"
    elif event_type == "attention_resolved":
        msg = item.get("message", "Attention anomaly resolved")
        summary = f"[RESOLVED] {msg}"
    elif event_type == "test":
        summary = "[TEST] Agent Fleet WebUI webhook connection test"
    else:
        summary = f"[{event_type.upper()}] {item.get('message', 'Notification event')}"

    return {
        "event_id": event_id,
        "event_type": event_type,
        "timestamp": ts,
        "item": item,
        "summary": summary,
        "details_url": build_details_url(base_url, item),
    }


class NotificationService:
    """Service managing attention state tracking and webhook notifications."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._active_items: dict[str, dict[str, Any]] = {}
        self._dispatched_event_ids: list[str] = []
        self._max_dispatched_cache: int = 1000

        # Load persisted config and state on initialization
        self.load_config()
        self.load_state()

    @property
    def state_file(self) -> Path:
        return self.settings.data_dir / "notification_state.json"

    @property
    def config_file(self) -> Path:
        return self.settings.data_dir / "notification_config.json"

    def load_config(self) -> dict[str, Any]:
        """Load persistent config overrides from data_dir/notification_config.json if available."""
        if not self.config_file.exists():
            return {
                "webhook_enabled": self.settings.webhook_enabled,
                "webhook_url": self.settings.webhook_url,
                "webhook_secret": self.settings.webhook_secret,
                "base_url": self.settings.base_url,
            }

        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                if "webhook_enabled" in data:
                    self.settings.webhook_enabled = bool(data["webhook_enabled"])
                if "webhook_url" in data:
                    self.settings.webhook_url = data["webhook_url"]
                if "webhook_secret" in data:
                    self.settings.webhook_secret = data["webhook_secret"]
                if "base_url" in data and data["base_url"]:
                    self.settings.base_url = str(data["base_url"])
        except Exception as exc:
            logger.warning("Could not read notification_config.json: %s", exc)

        return {
            "webhook_enabled": self.settings.webhook_enabled,
            "webhook_url": self.settings.webhook_url,
            "webhook_secret": self.settings.webhook_secret,
            "base_url": self.settings.base_url,
        }

    def save_config(
        self,
        webhook_enabled: bool | None = None,
        webhook_url: str | None = None,
        webhook_secret: str | None = None,
        base_url: str | None = None,
    ) -> dict[str, Any]:
        """Update and persist webhook notification configuration."""
        if webhook_enabled is not None:
            self.settings.webhook_enabled = webhook_enabled
        if webhook_url is not None:
            self.settings.webhook_url = webhook_url.strip() if webhook_url.strip() else None
        if webhook_secret is not None:
            # Mask placeholder "***" is ignored
            if webhook_secret not in ("***", "******"):
                self.settings.webhook_secret = webhook_secret.strip() if webhook_secret.strip() else None
        if base_url is not None:
            self.settings.base_url = base_url.strip() or "http://localhost:8000"

        # Persist to disk
        config_data = {
            "webhook_enabled": self.settings.webhook_enabled,
            "webhook_url": self.settings.webhook_url,
            "webhook_secret": self.settings.webhook_secret,
            "base_url": self.settings.base_url,
        }
        try:
            self.settings.data_dir.mkdir(parents=True, exist_ok=True)
            tmp_path = self.config_file.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(config_data, f, indent=2)
            tmp_path.replace(self.config_file)
        except Exception as exc:
            logger.error("Failed to save notification_config.json: %s", exc)

        return self.get_sanitized_config()

    def get_sanitized_config(self) -> dict[str, Any]:
        """Return notification configuration with secrets masked."""
        has_secret = bool(self.settings.webhook_secret and self.settings.webhook_secret.strip())
        return {
            "webhook_enabled": bool(self.settings.webhook_enabled),
            "webhook_url": self.settings.webhook_url,
            "has_secret": has_secret,
            "webhook_secret_masked": "******" if has_secret else None,
            "base_url": self.settings.base_url or "http://localhost:8000",
        }

    def load_state(self) -> None:
        """Load attention tracking state from disk."""
        if not self.state_file.exists():
            return
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._active_items = data.get("active_items") or {}
                self._dispatched_event_ids = data.get("dispatched_event_ids") or []
        except Exception as exc:
            logger.warning("Could not read notification_state.json: %s", exc)

    def save_state(self) -> None:
        """Save attention tracking state to disk."""
        try:
            self.settings.data_dir.mkdir(parents=True, exist_ok=True)
            data = {
                "active_items": self._active_items,
                "dispatched_event_ids": self._dispatched_event_ids[-self._max_dispatched_cache:],
            }
            tmp_path = self.state_file.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            tmp_path.replace(self.state_file)
        except Exception as exc:
            logger.warning("Failed to save notification_state.json: %s", exc)

    def clear_state(self) -> None:
        """Clear tracking state (useful for tests or state reset)."""
        self._active_items.clear()
        self._dispatched_event_ids.clear()
        if self.state_file.exists():
            try:
                self.state_file.unlink()
            except Exception:
                pass

    def dispatch_payload(
        self,
        payload: dict[str, Any],
        url: str | None = None,
        secret: str | None = None,
        timeout: float = 3.0,
        max_retries: int = 2,
        initial_backoff: float = 0.5,
    ) -> tuple[bool, str | None]:
        """Dispatch a webhook payload to the destination URL with retry and timeout.

        Returns (success: bool, error_message: str | None).
        Never crashes caller. Logs sanitized messages.
        """
        target_url = url or self.settings.webhook_url
        if not target_url or not target_url.strip():
            return False, "Webhook URL is empty"

        target_secret = secret if secret is not None else self.settings.webhook_secret
        payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "AgentFleet-Notifier/1.0",
        }
        if target_secret:
            sig = hmac.new(
                target_secret.encode("utf-8"),
                payload_bytes,
                hashlib.sha256,
            ).hexdigest()
            headers["X-Fleet-Signature"] = f"sha256={sig}"
            headers["Authorization"] = f"Bearer {target_secret}"

        backoff = initial_backoff
        last_err: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                with httpx.Client(timeout=timeout) as client:
                    resp = client.post(target_url, content=payload_bytes, headers=headers)
                    if resp.is_error:
                        try:
                            resp.raise_for_status()
                        except Exception:
                            raise httpx.HTTPStatusError(
                                f"HTTP error {resp.status_code}",
                                request=getattr(resp, "_request", None),
                                response=resp,
                            )
                    return True, None
            except Exception as exc:
                last_err = exc
                if attempt < max_retries:
                    time.sleep(backoff)
                    backoff *= 2
                else:
                    break

        clean_url = sanitize_url(target_url)
        clean_err = sanitize_error(last_err, target_secret)
        logger.warning(
            "Failed to dispatch webhook to %s after %d retries: %s",
            clean_url,
            max_retries,
            clean_err,
        )
        return False, clean_err

    def send_test_webhook(
        self,
        url: str | None = None,
        secret: str | None = None,
        base_url: str | None = None,
    ) -> dict[str, Any]:
        """Dispatch a synthetic test webhook event."""
        target_url = url or self.settings.webhook_url
        if not target_url or not target_url.strip():
            return {"ok": False, "error": "No webhook URL configured or provided"}

        effective_base_url = base_url or self.settings.base_url
        effective_secret = secret if secret is not None else self.settings.webhook_secret
        if effective_secret in ("***", "******"):
            effective_secret = self.settings.webhook_secret

        now = utcnow_iso()
        test_item = {
            "id": "test:webhook:synthetic",
            "type": "test_notification",
            "severity": "info",
            "target_type": "fleet",
            "target_id": "controller",
            "target_name": "Fleet Controller",
            "message": "Synthetic test event verifying Agent Fleet webhook delivery.",
            "first_detected_at": now,
            "last_updated_at": now,
            "action_view": "overview",
            "action_payload": {"test": True},
        }

        test_payload = build_event_payload(
            event_type="test",
            item=test_item,
            base_url=effective_base_url,
            timestamp=now,
        )

        ok, err = self.dispatch_payload(
            payload=test_payload,
            url=target_url,
            secret=effective_secret,
        )
        if ok:
            return {
                "ok": True,
                "message": "Test webhook delivered successfully",
                "event_id": test_payload["event_id"],
            }
        else:
            return {
                "ok": False,
                "error": f"Delivery failed: {err}",
            }

    def process_attention_items(
        self,
        current_items: list[dict[str, Any]],
        settings: Settings | None = None,
    ) -> list[dict[str, Any]]:
        """Compare current active attention anomalies with state and dispatch notifications.

        - New anomaly -> "attention_created"
        - Resolved anomaly -> "attention_resolved"
        Returns list of dispatched events.
        """
        eff_settings = settings or self.settings
        dispatched_events: list[dict[str, Any]] = []

        current_map: dict[str, dict[str, Any]] = {}
        for it in current_items:
            iid = it.get("id")
            if iid:
                current_map[iid] = it

        # Determine newly detected anomalies
        new_ids = [iid for iid in current_map if iid not in self._active_items]
        # Determine resolved anomalies
        resolved_ids = [iid for iid in self._active_items if iid not in current_map]

        is_enabled = bool(eff_settings.webhook_enabled and eff_settings.webhook_url)

        # 1. Handle newly detected anomalies
        for iid in new_ids:
            item = current_map[iid]
            first_detected = item.get("first_detected_at") or utcnow_iso()
            event_id = generate_event_id("attention_created", iid, first_detected)

            event = build_event_payload(
                event_type="attention_created",
                item=item,
                base_url=eff_settings.base_url,
                event_id=event_id,
            )

            if is_enabled and event_id not in self._dispatched_event_ids:
                ok, _ = self.dispatch_payload(
                    payload=event,
                    url=eff_settings.webhook_url,
                    secret=eff_settings.webhook_secret,
                )
                if ok:
                    self._dispatched_event_ids.append(event_id)
                dispatched_events.append(event)
            elif not is_enabled:
                # If notifications are disabled, track event_id to prevent stampede if enabled later
                self._dispatched_event_ids.append(event_id)

            self._active_items[iid] = item

        # 2. Handle resolved anomalies
        for iid in resolved_ids:
            prev_item = self._active_items[iid]
            resolved_time = utcnow_iso()
            event_id = generate_event_id("attention_resolved", iid, resolved_time)

            event = build_event_payload(
                event_type="attention_resolved",
                item=prev_item,
                base_url=eff_settings.base_url,
                event_id=event_id,
                timestamp=resolved_time,
            )

            if is_enabled and event_id not in self._dispatched_event_ids:
                ok, _ = self.dispatch_payload(
                    payload=event,
                    url=eff_settings.webhook_url,
                    secret=eff_settings.webhook_secret,
                )
                if ok:
                    self._dispatched_event_ids.append(event_id)
                dispatched_events.append(event)
            elif not is_enabled:
                self._dispatched_event_ids.append(event_id)

            del self._active_items[iid]

        # 3. Update existing active item records
        for iid, it in current_map.items():
            if iid in self._active_items:
                self._active_items[iid] = it

        # Save state if any change occurred
        if new_ids or resolved_ids:
            self.save_state()

        return dispatched_events

    def check_attention_changes(self, db=None, settings: Settings | None = None) -> list[dict[str, Any]]:
        """Collect attention summary and process notifications."""
        from agent_fleet.services import attention_service

        summary = attention_service.get_attention_summary(db=db, limit=500)
        items = summary.get("items", [])
        return self.process_attention_items(items, settings=settings)


_notification_service_instance: NotificationService | None = None


def get_notification_service() -> NotificationService:
    """Return singleton NotificationService instance."""
    global _notification_service_instance
    if _notification_service_instance is None:
        _notification_service_instance = NotificationService()
    return _notification_service_instance


def process_attention_items(current_items: list[dict[str, Any]], settings: Settings | None = None) -> list[dict[str, Any]]:
    return get_notification_service().process_attention_items(current_items, settings=settings)


def check_attention_changes(db=None, settings: Settings | None = None) -> list[dict[str, Any]]:
    return get_notification_service().check_attention_changes(db=db, settings=settings)


def dispatch_test_webhook(url: str | None = None, secret: str | None = None, base_url: str | None = None) -> dict[str, Any]:
    return get_notification_service().send_test_webhook(url=url, secret=secret, base_url=base_url)


def get_notification_config() -> dict[str, Any]:
    return get_notification_service().get_sanitized_config()


def update_notification_config(
    webhook_enabled: bool | None = None,
    webhook_url: str | None = None,
    webhook_secret: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    return get_notification_service().save_config(
        webhook_enabled=webhook_enabled,
        webhook_url=webhook_url,
        webhook_secret=webhook_secret,
        base_url=base_url,
    )
