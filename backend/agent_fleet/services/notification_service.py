"""Notification Service for Agent Fleet.

Provides webhook notification delivery for attention anomaly state changes:
- Anomaly detected: `attention_created`
- Anomaly resolved: `attention_resolved`
- Synthetic test: `test`

Features:
- Stable UUID event_id for receiver deduplication.
- In-memory & file-backed state tracking to prevent duplicate webhook dispatches.
- Persistent Outbox & safe retries with exponential backoff.
- Non-blocking asynchronous / background dispatch.
- SSRF protection against private, loopback, and cloud metadata targets.
- Credential and token masking in config and logs.
- Zero network calls when webhook is disabled or URL is missing.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import socket
import time
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from agent_fleet.models import iso_utc, utcnow
from agent_fleet.settings import Settings, get_settings

try:
    from fastapi import HTTPException
except ImportError:
    class HTTPException(Exception):  # type: ignore
        def __init__(self, status_code: int, detail: str) -> None:
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail


class WebhookValidationError(HTTPException, ValueError):
    """Exception raised when webhook URL or configuration fails validation."""

    def __init__(self, detail: str) -> None:
        HTTPException.__init__(self, status_code=400, detail=detail)
        ValueError.__init__(self, detail)


logger = logging.getLogger(__name__)

# Fixed namespace UUID for deterministic event_id generation
NAMESPACE_NOTIFICATIONS = uuid.UUID("3c4d5e6f-7a8b-9c0d-1e2f-3a4b5c6d7e8f")


def utcnow_iso() -> str:
    """Return current UTC time in standard ISO format."""
    return iso_utc(utcnow())


def validate_webhook_url(url: str | None, allow_local: bool | None = None) -> tuple[bool, str | None]:
    """Validate webhook URL scheme, structure, and check against SSRF.

    Blocks loopback (127.0.0.1, ::1), localhost, cloud metadata (169.254.169.254),
    and private IP ranges unless explicitly allowed (e.g. in test environment).
    """
    if not url or not str(url).strip():
        return False, "Webhook URL cannot be empty"

    stripped = str(url).strip()

    if allow_local is None:
        allow_local = os.environ.get("AGENT_FLEET_ALLOW_LOCAL_WEBHOOKS", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )

    try:
        parts = urllib.parse.urlsplit(stripped)
    except Exception as exc:
        return False, f"Malformed URL: {exc}"

    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        return False, f"Invalid webhook URL scheme '{parts.scheme}'. Only HTTP and HTTPS are allowed."

    hostname = parts.hostname
    if not hostname:
        return False, "Invalid webhook URL: missing hostname"

    if allow_local:
        return True, None

    lowered_host = hostname.lower()
    if lowered_host == "localhost" or lowered_host.endswith(".localhost"):
        return False, f"Webhook URL points to forbidden local host: '{hostname}'"
    if lowered_host in ("metadata.google.internal", "instance-data"):
        return False, f"Webhook URL points to forbidden cloud metadata host: '{hostname}'"

    # Check direct IP addresses
    try:
        ip = ipaddress.ip_address(hostname)
        if str(ip) == "169.254.169.254":
            return False, f"Webhook URL points to forbidden cloud metadata IP: {hostname}"
        if ip.is_loopback:
            return False, f"Webhook URL points to forbidden loopback IP: {hostname}"
        if ip.is_link_local:
            return False, f"Webhook URL points to forbidden link-local/metadata IP: {hostname}"
        if ip.is_private:
            return False, f"Webhook URL points to forbidden private IP: {hostname}"
        if ip.is_reserved or ip.is_unspecified:
            return False, f"Webhook URL points to forbidden reserved IP: {hostname}"
    except ValueError:
        # Not a raw IP literal; domain name. Attempt DNS resolution to check resolved IP.
        try:
            addr_info = socket.getaddrinfo(hostname, None)
            for item in addr_info:
                sockaddr = item[4]
                resolved_ip_str = sockaddr[0]
                try:
                    resolved_ip = ipaddress.ip_address(resolved_ip_str)
                    if resolved_ip.is_loopback:
                        return False, f"Webhook URL domain '{hostname}' resolves to loopback IP: {resolved_ip_str}"
                    if resolved_ip.is_link_local:
                        return False, f"Webhook URL domain '{hostname}' resolves to metadata/link-local IP: {resolved_ip_str}"
                    if resolved_ip.is_private:
                        return False, f"Webhook URL domain '{hostname}' resolves to private IP: {resolved_ip_str}"
                    if str(resolved_ip) == "169.254.169.254":
                        return False, f"Webhook URL domain '{hostname}' resolves to metadata IP: {resolved_ip_str}"
                except ValueError:
                    pass
        except (socket.gaierror, OSError):
            pass

    return True, None


def mask_url_credentials(url: str | None) -> str | None:
    """Mask credentials, auth tokens, and sensitive query/path parameters for safe display."""
    if not url or not str(url).strip():
        return None
    try:
        parts = urllib.parse.urlsplit(str(url).strip())
        netloc = parts.netloc
        if parts.username or parts.password:
            user_part = "***"
            if parts.password:
                user_part = "***:***"
            host_port = parts.hostname or ""
            if parts.port:
                host_port = f"{host_port}:{parts.port}"
            netloc = f"{user_part}@{host_port}"

        # Mask path tokens for known webhook providers or long secret hashes
        path = parts.path
        path_parts = path.split("/")
        if len(path_parts) > 1 and path_parts[-1]:
            last_segment = path_parts[-1]
            if any(p in parts.netloc.lower() for p in ("slack.com", "discord.com", "feishu.cn", "larksuite.com")):
                path_parts[-1] = "***"
                path = "/".join(path_parts)
            elif len(last_segment) >= 20 and not last_segment.endswith((".json", ".html", ".xml")):
                path_parts[-1] = "***"
                path = "/".join(path_parts)

        # Mask sensitive query params
        query = parts.query
        if query:
            q_pairs = urllib.parse.parse_qsl(query, keep_blank_values=True)
            sanitized_q = []
            for k, v in q_pairs:
                if any(
                    sec in k.lower()
                    for sec in ("token", "key", "secret", "sig", "auth", "pass", "pwd", "api_key", "access_token")
                ):
                    sanitized_q.append((k, "***"))
                else:
                    sanitized_q.append((k, v))
            query = urllib.parse.urlencode(sanitized_q, safe="*")

        return urllib.parse.urlunsplit((parts.scheme, netloc, path, query, parts.fragment))
    except Exception:
        return "[URL_MASKING_FAILED]"


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
            query = urllib.parse.urlencode(sanitized_q, safe="*")
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
    """Service managing attention state tracking, persistent outbox, and webhook notifications."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._active_items: dict[str, dict[str, Any]] = {}
        self._dispatched_event_ids: list[str] = []
        self._outbox: dict[str, dict[str, Any]] = {}
        self._max_dispatched_cache: int = 1000
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="fleet-notify"
        )
        self._pending_futures: set[concurrent.futures.Future] = set()

        # Load persisted config and state on initialization
        self.load_config()
        self.load_state()

    @property
    def state_file(self) -> Path:
        return self.settings.data_dir / "notification_state.json"

    @property
    def config_file(self) -> Path:
        return self.settings.data_dir / "notification_config.json"

    @property
    def outbox_file(self) -> Path:
        return self.settings.data_dir / "notification_outbox.json"

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
        """Update and persist webhook notification configuration with SSRF validation."""
        if webhook_enabled is not None:
            self.settings.webhook_enabled = webhook_enabled

        if webhook_url is not None:
            stripped_url = webhook_url.strip()
            if not stripped_url:
                self.settings.webhook_url = None
            elif "***" in stripped_url and self.settings.webhook_url:
                # Do not overwrite real URL with mask placeholder
                pass
            else:
                valid, reason = validate_webhook_url(stripped_url)
                if not valid:
                    raise WebhookValidationError(f"Invalid webhook URL: {reason}")
                self.settings.webhook_url = stripped_url

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
        """Return notification configuration with secrets and tokens masked."""
        has_url = bool(self.settings.webhook_url and self.settings.webhook_url.strip())
        masked_url = mask_url_credentials(self.settings.webhook_url) if has_url else None
        has_secret = bool(self.settings.webhook_secret and self.settings.webhook_secret.strip())
        return {
            "webhook_enabled": bool(self.settings.webhook_enabled),
            "webhook_url": masked_url,
            "has_url": has_url,
            "masked_url": masked_url,
            "has_secret": has_secret,
            "webhook_secret_masked": "******" if has_secret else None,
            "base_url": self.settings.base_url or "http://localhost:8000",
        }

    def load_state(self) -> None:
        """Load attention tracking state and persistent outbox from disk."""
        if self.state_file.exists():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self._active_items = data.get("active_items") or {}
                    self._dispatched_event_ids = data.get("dispatched_event_ids") or []
            except Exception as exc:
                logger.warning("Could not read notification_state.json: %s", exc)

        if self.outbox_file.exists():
            try:
                with open(self.outbox_file, "r", encoding="utf-8") as f:
                    outbox_data = json.load(f)
                if isinstance(outbox_data, dict):
                    self._outbox = outbox_data
            except Exception as exc:
                logger.warning("Could not read notification_outbox.json: %s", exc)

    def save_state(self) -> None:
        """Save attention tracking state and outbox to disk."""
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

        self.save_outbox()

    def save_outbox(self) -> None:
        """Persist outbox records to disk."""
        try:
            self.settings.data_dir.mkdir(parents=True, exist_ok=True)
            tmp_path = self.outbox_file.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self._outbox, f, indent=2)
            tmp_path.replace(self.outbox_file)
        except Exception as exc:
            logger.warning("Failed to save notification_outbox.json: %s", exc)

    def clear_state(self) -> None:
        """Clear tracking state and outbox (useful for tests or state reset)."""
        self._active_items.clear()
        self._dispatched_event_ids.clear()
        self._outbox.clear()
        if self.state_file.exists():
            try:
                self.state_file.unlink()
            except Exception:
                pass
        if self.outbox_file.exists():
            try:
                self.outbox_file.unlink()
            except Exception:
                pass

    def get_outbox(self) -> dict[str, dict[str, Any]]:
        """Return raw outbox dictionary keyed by event_id."""
        return self._outbox

    def get_outbox_items(self) -> list[dict[str, Any]]:
        """Return list of all outbox event records."""
        return list(self._outbox.values())

    def _send_http_request(
        self,
        payload: dict[str, Any],
        target_url: str,
        secret: str | None = None,
        timeout: float = 3.0,
    ) -> tuple[bool, str | None]:
        """Perform a single HTTP webhook delivery attempt with HMAC signature."""
        payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "AgentFleet-Notifier/1.0",
        }
        if secret:
            sig = hmac.new(
                secret.encode("utf-8"),
                payload_bytes,
                hashlib.sha256,
            ).hexdigest()
            headers["X-Fleet-Signature"] = f"sha256={sig}"
            headers["Authorization"] = f"Bearer {secret}"

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
            clean_err = sanitize_error(exc, secret)
            return False, clean_err

    def _dispatch_outbox_event(
        self,
        event_id: str,
        url: str | None = None,
        secret: str | None = None,
        timeout: float = 3.0,
    ) -> tuple[bool, str | None]:
        """Attempt non-blocking delivery for an outbox event and update persistent failure state."""
        entry = self._outbox.get(event_id)
        if not entry:
            return False, f"Event {event_id} not found in outbox"

        if entry.get("status") == "sent":
            return True, None

        target_url = url or entry.get("target_url") or self.settings.webhook_url
        if not target_url or not str(target_url).strip():
            entry["status"] = "failed"
            entry["last_error"] = "Webhook URL is empty"
            entry["updated_at"] = utcnow_iso()
            self.save_outbox()
            return False, entry["last_error"]

        target_url = target_url.strip()
        valid, reason = validate_webhook_url(target_url)
        if not valid:
            entry["status"] = "failed"
            entry["last_error"] = f"Forbidden webhook target: {reason}"
            entry["updated_at"] = utcnow_iso()
            self.save_outbox()
            return False, entry["last_error"]

        target_secret = secret if secret is not None else self.settings.webhook_secret
        payload = entry.get("payload") or {}

        ok, err = self._send_http_request(
            payload=payload,
            target_url=target_url,
            secret=target_secret,
            timeout=timeout,
        )

        now = utcnow()
        entry["updated_at"] = iso_utc(now)

        if ok:
            entry["status"] = "sent"
            entry["last_error"] = None
            entry["next_retry_at"] = None
            if event_id not in self._dispatched_event_ids:
                self._dispatched_event_ids.append(event_id)
            self.save_outbox()
            self.save_state()
            return True, None
        else:
            entry["status"] = "failed"
            entry["retry_count"] = entry.get("retry_count", 0) + 1
            entry["last_error"] = err
            # Exponential backoff: 30s, 60s, 120s, 240s... capped at 1 hour
            backoff_seconds = min(3600, 30 * (2 ** max(0, entry["retry_count"] - 1)))
            entry["next_retry_at"] = iso_utc(now + timedelta(seconds=backoff_seconds))
            self.save_outbox()
            return False, err

    def retry_outbox(
        self,
        max_retries: int = 5,
        force: bool = False,
        timeout: float = 3.0,
    ) -> list[dict[str, Any]]:
        """Retry delivery for pending or failed events whose next_retry_at has arrived.

        Prevents duplicate storms by respecting exponential backoff unless force is True.
        """
        now = datetime.now(timezone.utc)
        results: list[dict[str, Any]] = []

        for event_id, entry in list(self._outbox.items()):
            if entry.get("status") == "sent":
                continue

            retry_count = entry.get("retry_count", 0)
            if retry_count >= max_retries and not force:
                continue

            next_retry_str = entry.get("next_retry_at")
            if next_retry_str and not force:
                try:
                    next_retry_dt = datetime.fromisoformat(next_retry_str.replace("Z", "+00:00"))
                    if next_retry_dt.tzinfo is None:
                        next_retry_dt = next_retry_dt.replace(tzinfo=timezone.utc)
                    if now < next_retry_dt:
                        # Backoff window still active; skip safely to avoid storms
                        continue
                except Exception:
                    pass

            ok, err = self._dispatch_outbox_event(event_id=event_id, timeout=timeout)
            results.append({"event_id": event_id, "ok": ok, "error": err})

        return results

    def wait_for_background_tasks(self, timeout: float = 5.0) -> None:
        """Wait for pending asynchronous dispatch tasks to finish."""
        pending = list(self._pending_futures)
        if pending:
            concurrent.futures.wait(pending, timeout=timeout)

    def dispatch_payload(
        self,
        payload: dict[str, Any],
        url: str | None = None,
        secret: str | None = None,
        timeout: float = 3.0,
        max_retries: int = 2,
        initial_backoff: float = 0.5,
    ) -> tuple[bool, str | None]:
        """Dispatch a webhook payload to the destination URL with optional inline retry.

        Returns (success: bool, error_message: str | None).
        Never crashes caller. Logs sanitized messages.
        """
        target_url = url or self.settings.webhook_url
        if not target_url or not target_url.strip():
            return False, "Webhook URL is empty"

        target_url = target_url.strip()
        valid, reason = validate_webhook_url(target_url)
        if not valid:
            return False, f"Invalid or forbidden webhook URL: {reason}"

        target_secret = secret if secret is not None else self.settings.webhook_secret
        backoff = initial_backoff
        last_err: str | None = None

        for attempt in range(max_retries + 1):
            ok, err = self._send_http_request(
                payload=payload,
                target_url=target_url,
                secret=target_secret,
                timeout=timeout,
            )
            if ok:
                event_id = payload.get("event_id")
                if event_id and event_id in self._outbox:
                    self._outbox[event_id]["status"] = "sent"
                    self._outbox[event_id]["updated_at"] = utcnow_iso()
                    self.save_outbox()
                return True, None

            last_err = err
            if attempt < max_retries:
                time.sleep(backoff)
                backoff *= 2
            else:
                break

        clean_url = sanitize_url(target_url)
        logger.warning(
            "Failed to dispatch webhook to %s after %d retries: %s",
            clean_url,
            max_retries,
            last_err,
        )
        return False, last_err

    def send_test_webhook(
        self,
        url: str | None = None,
        secret: str | None = None,
        base_url: str | None = None,
    ) -> dict[str, Any]:
        """Dispatch a synthetic test webhook event."""
        target_url = url or self.settings.webhook_url
        if not target_url or not str(target_url).strip():
            return {"ok": False, "error": "No webhook URL configured or provided"}

        target_url = str(target_url).strip()
        valid, reason = validate_webhook_url(target_url)
        if not valid:
            return {"ok": False, "error": f"No webhook URL or invalid webhook URL: {reason}"}

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
        current_items: list[dict[str, Any]] | dict[str, Any],
        settings: Settings | None = None,
        background: bool = False,
    ) -> list[dict[str, Any]]:
        """Compare current active attention anomalies with state and dispatch notifications.

        Completely decoupled from pagination:
        - If current_items is a summary or has `_all_items`, all items across the fleet are processed.
        - Outbox records every pending/sent/failed event to prevent duplicate notification storms.
        - Supports non-blocking background dispatch when background=True.
        """
        eff_settings = settings or self.settings
        dispatched_events: list[dict[str, Any]] = []

        # Decouple from pagination: extract all items if available
        if isinstance(current_items, dict):
            all_items = (
                current_items.get("_all_items")
                or current_items.get("all_items")
                or current_items.get("items", [])
            )
            if hasattr(all_items, "_all_items"):
                items_to_process = getattr(all_items, "_all_items")
            else:
                items_to_process = all_items
        elif hasattr(current_items, "_all_items"):
            items_to_process = getattr(current_items, "_all_items")
        else:
            items_to_process = current_items

        current_map: dict[str, dict[str, Any]] = {}
        for it in items_to_process:
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

            # Record in Outbox
            now_str = utcnow_iso()
            if event_id not in self._outbox:
                self._outbox[event_id] = {
                    "event_id": event_id,
                    "event_type": "attention_created",
                    "payload": event,
                    "target_url": eff_settings.webhook_url,
                    "status": "pending",
                    "retry_count": 0,
                    "next_retry_at": None,
                    "last_error": None,
                    "created_at": now_str,
                    "updated_at": now_str,
                }

            if is_enabled and event_id not in self._dispatched_event_ids:
                if background:
                    fut = self._executor.submit(
                        self._dispatch_outbox_event,
                        event_id=event_id,
                        url=eff_settings.webhook_url,
                        secret=eff_settings.webhook_secret,
                    )
                    self._pending_futures.add(fut)
                    fut.add_done_callback(lambda f: self._pending_futures.discard(f))
                else:
                    self._dispatch_outbox_event(
                        event_id=event_id,
                        url=eff_settings.webhook_url,
                        secret=eff_settings.webhook_secret,
                    )
                dispatched_events.append(event)
            elif not is_enabled:
                self._outbox[event_id]["status"] = "sent"
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

            now_str = utcnow_iso()
            if event_id not in self._outbox:
                self._outbox[event_id] = {
                    "event_id": event_id,
                    "event_type": "attention_resolved",
                    "payload": event,
                    "target_url": eff_settings.webhook_url,
                    "status": "pending",
                    "retry_count": 0,
                    "next_retry_at": None,
                    "last_error": None,
                    "created_at": now_str,
                    "updated_at": now_str,
                }

            if is_enabled and event_id not in self._dispatched_event_ids:
                if background:
                    fut = self._executor.submit(
                        self._dispatch_outbox_event,
                        event_id=event_id,
                        url=eff_settings.webhook_url,
                        secret=eff_settings.webhook_secret,
                    )
                    self._pending_futures.add(fut)
                    fut.add_done_callback(lambda f: self._pending_futures.discard(f))
                else:
                    self._dispatch_outbox_event(
                        event_id=event_id,
                        url=eff_settings.webhook_url,
                        secret=eff_settings.webhook_secret,
                    )
                dispatched_events.append(event)
            elif not is_enabled:
                self._outbox[event_id]["status"] = "sent"
                self._dispatched_event_ids.append(event_id)

            del self._active_items[iid]

        # 3. Update existing active item records
        for iid, it in current_map.items():
            if iid in self._active_items:
                self._active_items[iid] = it

        # Save state & outbox if changes occurred
        if new_ids or resolved_ids:
            self.save_state()

        return dispatched_events

    def process_attention_summary(
        self,
        summary: dict[str, Any],
        settings: Settings | None = None,
        background: bool = False,
    ) -> list[dict[str, Any]]:
        """Process notifications directly from a summary dict, ensuring unpaginated item delivery."""
        all_items = (
            summary.get("_all_items")
            or summary.get("all_items")
            or summary.get("items", [])
        )
        if hasattr(all_items, "_all_items"):
            all_items = getattr(all_items, "_all_items")
        return self.process_attention_items(all_items, settings=settings, background=background)

    def check_attention_changes(
        self,
        db=None,
        settings: Settings | None = None,
        background: bool = False,
    ) -> list[dict[str, Any]]:
        """Collect attention summary across the entire fleet and process notifications."""
        from agent_fleet.services import attention_service

        summary = attention_service.get_attention_summary(db=db, limit=None)
        return self.process_attention_summary(summary, settings=settings, background=background)


_notification_service_instance: NotificationService | None = None


def get_notification_service() -> NotificationService:
    """Return singleton NotificationService instance."""
    global _notification_service_instance
    if _notification_service_instance is None:
        _notification_service_instance = NotificationService()
    return _notification_service_instance


def process_attention_items(
    current_items: list[dict[str, Any]] | dict[str, Any],
    settings: Settings | None = None,
    background: bool = False,
) -> list[dict[str, Any]]:
    return get_notification_service().process_attention_items(
        current_items, settings=settings, background=background
    )


def process_attention_summary(
    summary: dict[str, Any],
    settings: Settings | None = None,
    background: bool = False,
) -> list[dict[str, Any]]:
    return get_notification_service().process_attention_summary(
        summary, settings=settings, background=background
    )


def check_attention_changes(
    db=None,
    settings: Settings | None = None,
    background: bool = False,
) -> list[dict[str, Any]]:
    return get_notification_service().check_attention_changes(
        db=db, settings=settings, background=background
    )


def dispatch_test_webhook(
    url: str | None = None,
    secret: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
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


def retry_outbox(
    max_retries: int = 5,
    force: bool = False,
    timeout: float = 3.0,
) -> list[dict[str, Any]]:
    return get_notification_service().retry_outbox(
        max_retries=max_retries, force=force, timeout=timeout
    )


def get_outbox_items() -> list[dict[str, Any]]:
    return get_notification_service().get_outbox_items()
