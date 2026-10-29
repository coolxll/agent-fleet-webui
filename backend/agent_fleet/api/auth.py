"""API Authentication and Security Access Control.

Provides secret-based authentication middleware, dependencies, and settings inspection
to protect controller write operations and sensitive endpoints.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from starlette.datastructures import Headers
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

from agent_fleet.settings import get_settings

logger = logging.getLogger("agent_fleet.security")

router = APIRouter(tags=["auth"])


def extract_token_from_headers(headers: Headers) -> str | None:
    """Extract API secret token from X-Agent-Fleet-Secret or Authorization Bearer header."""
    # Priority 1: Custom header X-Agent-Fleet-Secret
    custom_secret = headers.get("x-agent-fleet-secret")
    if custom_secret:
        token = custom_secret.strip()
        if token and not any(c in token for c in ("\r", "\n", "\0")):
            return token

    # Priority 2: Standard Authorization: Bearer <secret>
    auth_header = headers.get("authorization")
    if auth_header:
        parts = auth_header.strip().split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1].strip()
            if token and not any(c in token for c in ("\r", "\n", "\0")):
                return token

    return None


def verify_token(provided: str | None, expected: str | None) -> bool:
    """Constant-time token verification to mitigate timing attacks."""
    if not provided or not expected:
        return False
    return secrets.compare_digest(provided, expected)


class APIAuthMiddleware(BaseHTTPMiddleware):
    """Enforces API access control boundary on controller endpoints.

    When api_secret is configured:
    - CORS preflight (OPTIONS) is allowed.
    - Read-only methods (GET, HEAD) are allowed if allow_anonymous_readonly is True.
    - Write methods (POST, PUT, DELETE, PATCH) require a valid secret.
    - If allow_anonymous_readonly is False, all methods require a valid secret.
    When api_secret is not configured (default / dev mode), all requests are allowed.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        settings = get_settings()

        # If auth is not enabled, seamlessly allow request
        if not settings.is_auth_enabled:
            return await call_next(request)

        path = request.url.path

        # Only apply authentication boundary to API prefix
        if not path.startswith(settings.api_prefix):
            return await call_next(request)

        # Allow CORS preflight requests
        if request.method == "OPTIONS":
            return await call_next(request)

        # Check read-only permission
        is_read_only = request.method in ("GET", "HEAD")
        if settings.allow_anonymous_readonly and is_read_only:
            return await call_next(request)

        # Verify token for write operations or non-anonymous read
        token = extract_token_from_headers(request.headers)
        if not verify_token(token, settings.api_secret):
            client_host = request.client.host if request.client else "unknown"
            logger.warning(
                "Unauthorized access rejected: %s %s from client %s",
                request.method,
                path,
                client_host,
            )
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Unauthorized: invalid or missing API secret"},
                headers={"WWW-Authenticate": 'Bearer realm="AgentFleet"'},
            )

        return await call_next(request)


def verify_api_secret(request: Request) -> None:
    """FastAPI route dependency for access control."""
    settings = get_settings()
    if not settings.is_auth_enabled:
        return

    is_read_only = request.method in ("GET", "HEAD")
    if settings.allow_anonymous_readonly and is_read_only:
        return

    token = extract_token_from_headers(request.headers)
    if not verify_token(token, settings.api_secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: invalid or missing API secret",
            headers={"WWW-Authenticate": 'Bearer realm="AgentFleet"'},
        )


@router.get("/settings")
def get_public_settings() -> dict[str, Any]:
    """Retrieve controller configuration status without exposing sensitive secrets."""
    settings = get_settings()
    return settings.safe_dict()
