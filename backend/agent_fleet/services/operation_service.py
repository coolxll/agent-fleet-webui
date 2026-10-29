"""Operation Audit Log service.

Provides persistent, auditable recording and querying of remote write actions
(agent upgrades, runner lifecycle, task dispatch/cancel, SSH operations).
Enforces status transition safety: operations cannot be marked as "succeeded"
without verifiable remote evidence. Sensitive credentials, passwords, and tokens
are aggressively sanitized.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from sqlalchemy import select

from agent_fleet import models
from agent_fleet.db import session_scope

logger = logging.getLogger(__name__)

VALID_STATUSES = {"accepted", "running", "succeeded", "failed", "unknown"}

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "accepted": {"running", "succeeded", "failed", "unknown"},
    "running": {"succeeded", "failed", "unknown"},
    "unknown": {"succeeded", "failed"},
    "succeeded": set(),
    "failed": set(),
}

SECRET_KEY_NAMES = {
    "api_key",
    "apikey",
    "secret",
    "password",
    "token",
    "auth_token",
    "access_token",
    "authorization",
    "credential",
    "private_key",
    "client_secret",
}

SECRET_PATTERNS = [
    (
        re.compile(r"-----BEGIN [A-Z ]+ PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+ PRIVATE KEY-----"),
        "***MASKED_PRIVATE_KEY***",
    ),
    (
        re.compile(r"(bearer\s+)[a-zA-Z0-9_\-\.]{8,}", re.IGNORECASE),
        r"\1***MASKED_TOKEN***",
    ),
    (
        re.compile(r"(ghp_[a-zA-Z0-9]{20,})"),
        "***MASKED_GITHUB_TOKEN***",
    ),
    (
        re.compile(r"(sk-[a-zA-Z0-9_\-]{20,})"),
        "***MASKED_API_KEY***",
    ),
    (
        re.compile(r"((?:password|passwd|secret|token|api[_-]?key)\s*[:=]\s*)[^\s,;&]+", re.IGNORECASE),
        r"\1***MASKED***",
    ),
]


def sanitize_text(text: str | None) -> str | None:
    """Sanitize secrets, tokens, and credentials from string error/output messages."""
    if not text:
        return text
    masked = text
    for pattern, repl in SECRET_PATTERNS:
        masked = pattern.sub(repl, masked)
    return masked


def sanitize_structure(data: Any) -> Any:
    """Recursively sanitize credentials and sensitive parameters from dictionaries/lists."""
    if data is None:
        return None
    if isinstance(data, str):
        return sanitize_text(data)
    if isinstance(data, dict):
        result: dict[str, Any] = {}
        for k, v in data.items():
            k_str = str(k)
            k_lower = k_str.lower()
            if any(s in k_lower for s in SECRET_KEY_NAMES):
                result[k_str] = "***MASKED***"
            else:
                result[k_str] = sanitize_structure(v)
        return result
    if isinstance(data, (list, tuple)):
        return [sanitize_structure(item) for item in data]
    return data


def _serialize_operation(op: models.OperationLog) -> dict[str, Any]:
    """Serialize an OperationLog entity to a JSON-safe dictionary."""
    return {
        "id": op.id,
        "operation_type": op.operation_type,
        "target_type": op.target_type,
        "target_id": op.target_id,
        "target_name": op.target_name,
        "status": op.status,
        "requested_by": op.requested_by,
        "started_at": models.iso_utc(op.started_at),
        "finished_at": models.iso_utc(op.finished_at),
        "error": op.error,
        "details": op.details or {},
    }


def record_operation(
    operation_type: str,
    target_type: str,
    target_id: str,
    target_name: str | None = None,
    status: str = "accepted",
    requested_by: str | None = None,
    details: dict[str, Any] | None = None,
    error: str | None = None,
    started_at: datetime | None = None,
    op_id: str | None = None,
    verification_evidence: Any = None,
) -> dict[str, Any]:
    """Create and persist an OperationLog record in SQLite.

    If status is 'succeeded', verification_evidence is strictly required.
    """
    if status not in VALID_STATUSES:
        raise ValueError(
            f"Invalid operation status '{status}'. Must be one of {sorted(VALID_STATUSES)}"
        )

    if status == "succeeded" and not verification_evidence:
        raise ValueError(
            "Operation cannot be marked as 'succeeded' without remote verification evidence"
        )

    clean_details = sanitize_structure(details) or {}
    if verification_evidence is not None:
        clean_details["verification"] = sanitize_structure(verification_evidence)

    clean_error = sanitize_text(error)
    start_time = started_at or models.utcnow()
    finished_time = models.utcnow() if status in ("succeeded", "failed", "unknown") else None

    with session_scope() as s:
        op = models.OperationLog(
            id=op_id or models.new_op_id(),
            operation_type=operation_type,
            target_type=target_type,
            target_id=target_id,
            target_name=target_name,
            status=status,
            requested_by=requested_by,
            started_at=start_time,
            finished_at=finished_time,
            error=clean_error,
            details=clean_details,
        )
        s.add(op)
        s.flush()
        return _serialize_operation(op)


def update_operation_status(
    op_id: str,
    status: str,
    error: str | None = None,
    details: dict[str, Any] | None = None,
    verification_evidence: Any = None,
    finished_at: datetime | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    """Update status, outcomes, and verification evidence for an operation.

    Enforces that 'succeeded' transitions require verifiable evidence.
    Enforces status transition rules according to the operation FSM:
    - Terminal states ('succeeded', 'failed') cannot regress to 'running' or 'accepted'.
    - If an invalid transition is attempted and strict=False, the transition is safely ignored
      without corrupting the existing record.
    - If strict=True, an invalid transition raises ValueError.
    - Updates are idempotent when called with the current status.
    """
    if status not in VALID_STATUSES:
        raise ValueError(
            f"Invalid operation status '{status}'. Must be one of {sorted(VALID_STATUSES)}"
        )

    with session_scope() as s:
        op = s.get(models.OperationLog, op_id)
        if not op:
            raise ValueError(f"Operation '{op_id}' not found")

        is_same_status = (op.status == status)
        is_allowed = status in ALLOWED_TRANSITIONS.get(op.status, set())

        if not is_same_status and not is_allowed:
            if strict:
                raise ValueError(
                    f"Invalid operation status transition from '{op.status}' to '{status}'"
                )
            logger.warning(
                "Ignoring invalid operation status transition for '%s': '%s' -> '%s'",
                op_id,
                op.status,
                status,
            )
        else:
            if status == "succeeded" and not verification_evidence:
                existing_verification = (op.details or {}).get("verification")
                if not existing_verification:
                    raise ValueError(
                        "Operation cannot be marked as 'succeeded' without remote verification evidence"
                    )
            op.status = status
            if (status in ("succeeded", "failed", "unknown") or finished_at) and not op.finished_at:
                op.finished_at = finished_at or models.utcnow()
            elif finished_at:
                op.finished_at = finished_at

        if error is not None:
            op.error = sanitize_text(error)

        curr_details = dict(op.details or {})
        if details:
            curr_details.update(sanitize_structure(details))

        if verification_evidence is not None:
            curr_details["verification"] = sanitize_structure(verification_evidence)

        op.details = curr_details

        s.flush()
        return _serialize_operation(op)


def reconcile_orphaned_operations(db: Any = None) -> int:
    """Reconcile orphaned operations after a controller restart.

    Queries all OperationLog records where status is 'running' or 'accepted',
    and transitions them to 'unknown' with an explanatory error notice.
    Returns the count of reconciled operations.
    """
    reconcile_msg = (
        "Controller restarted while operation was running or unconfirmed; requires verification"
    )

    def _reconcile(session) -> int:
        stmt = select(models.OperationLog).where(
            models.OperationLog.status.in_(["accepted", "running"])
        )
        orphans = list(session.scalars(stmt).all())
        now = models.utcnow()
        for op in orphans:
            op.status = "unknown"
            if op.error:
                op.error = sanitize_text(f"{op.error} | {reconcile_msg}")
            else:
                op.error = sanitize_text(reconcile_msg)
            if not op.finished_at:
                op.finished_at = now
        session.flush()
        return len(orphans)

    if db is not None:
        return _reconcile(db)

    with session_scope() as s:
        return _reconcile(s)


def get_operation(op_id: str) -> dict[str, Any] | None:
    """Retrieve a single operation by ID."""
    with session_scope() as s:
        op = s.get(models.OperationLog, op_id)
        if not op:
            return None
        return _serialize_operation(op)


def list_operations(
    limit: int = 50,
    target_type: str | None = None,
    target_id: str | None = None,
    status: str | None = None,
    operation_type: str | None = None,
) -> list[dict[str, Any]]:
    """List recent operations matching optional target and status filters."""
    with session_scope() as s:
        stmt = select(models.OperationLog).order_by(models.OperationLog.started_at.desc())

        if target_type:
            stmt = stmt.where(models.OperationLog.target_type == target_type)
        if target_id:
            stmt = stmt.where(models.OperationLog.target_id == target_id)
        if status:
            stmt = stmt.where(models.OperationLog.status == status)
        if operation_type:
            stmt = stmt.where(models.OperationLog.operation_type == operation_type)

        stmt = stmt.limit(limit)
        results = s.execute(stmt).scalars().all()
        return [_serialize_operation(op) for op in results]
