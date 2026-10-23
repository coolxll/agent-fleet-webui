"""Online quota providers for imported accounts.

An imported account is a credential the user explicitly pasted or loaded from
a file. The provider verifies it against the vendor's online API immediately,
so an invalid credential is never stored. Raw credentials never leave this
module except into the local 0600 account store.
"""

from __future__ import annotations

import base64
import json
import math
from datetime import datetime, timezone
from typing import Any

import httpx


class QuotaProviderError(Exception):
    """A provider refused the credential or returned an unusable answer."""


class QuotaAuthError(QuotaProviderError):
    """The usage endpoint rejected the current access token."""


PROVIDERS: dict[str, dict] = {
    "codex": {
        "id": "codex",
        "name": "ChatGPT (Codex)",
        "agent": "codex",
        "credential_label": "auth.json / access token",
        "credential_hint": (
            "Paste ~/.codex/auth.json contents, or just a ChatGPT access token. "
            "The credential is verified against the online usage API before saving."
        ),
        "online": True,
    },
    "antigravity": {
        "id": "antigravity",
        "name": "Antigravity",
        "agent": "agy",
        "credential_label": "OAuth credential JSON",
        "credential_hint": "Import OAuth credentials with a refresh_token. The backend refreshes tokens and queries Google directly.",
        "online": True,
    },
    "workbuddy": {
        "id": "workbuddy",
        "name": "WorkBuddy / CodeBuddy",
        "agent": "codebuddy",
        "credential_label": "Workbuddy / Codebuddy auth JSON or token",
        "credential_hint": (
            "Leave blank or enter 'auto' to auto-detect from local CodeBuddyExtension, "
            "or paste auth.json / Bearer accessToken. Supports both CodeBuddy CN and Workbuddy Global."
        ),
        "online": True,
    },
    "codebuddy": {
        "id": "codebuddy",
        "name": "CodeBuddy",
        "agent": "codebuddy",
        "credential_label": "Codebuddy auth JSON or token",
        "credential_hint": (
            "Leave blank or enter 'auto' to auto-detect from local CodeBuddyExtension, "
            "or paste auth.json / Bearer accessToken. Supports both CodeBuddy CN and Workbuddy Global."
        ),
        "online": True,
    },
    "trae": {
        "id": "trae",
        "name": "Trae CN",
        "agent": "trae",
        "credential_label": "Cloud-IDE-JWT access token / JSON",
        "credential_hint": (
            "Trae 国内版：在 www.trae.cn 个人中心的积分请求中复制 Authorization 值，"
            "可直接粘贴 Cloud-IDE-JWT token，或导入含 access_token 和可选 device_id 的 JSON。"
        ),
        "online": True,
    },
    "qodercn": {
        "id": "qodercn",
        "name": "QoderCN",
        "agent": "qoder",
        "credential_label": "QoderCN auth / token",
        "credential_hint": (
            "Leave blank or enter 'auto' to auto-detect from ~/.qoder-cn/.auth/, "
            "or paste decrypted user credentials / Bearer token."
        ),
        "online": True,
    },
}


def provider_meta() -> list[dict]:
    return list(PROVIDERS.values())


def provider_name(provider_id: str) -> str:
    return PROVIDERS.get(provider_id, {}).get("name", provider_id)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _decode_jwt_payload(token: str) -> dict:
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        data = json.loads(base64.urlsafe_b64decode(part))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _codex_identity(credential: dict) -> dict:
    claims = _decode_jwt_payload(credential.get("id_token") or "")
    access_claims = _decode_jwt_payload(credential.get("access_token") or "")
    profile = access_claims.get("https://api.openai.com/profile")
    if not isinstance(profile, dict):
        profile = {}
    auth = access_claims.get("https://api.openai.com/auth")
    if not isinstance(auth, dict):
        auth = {}
    id_auth = claims.get("https://api.openai.com/auth")
    if not isinstance(id_auth, dict):
        id_auth = {}
    email = (
        claims.get("email")
        or profile.get("email")
        or credential.get("email")
    )
    name = claims.get("name") or profile.get("name") or email
    account_id = (
        credential.get("account_id")
        or auth.get("chatgpt_account_id")
        or id_auth.get("chatgpt_account_id")
    )
    plan = (
        auth.get("chatgpt_plan_type")
        or id_auth.get("chatgpt_plan_type")
    )
    return {
        "email": email if isinstance(email, str) else None,
        "name": name if isinstance(name, str) else None,
        "account_id": account_id if isinstance(account_id, str) else None,
        "plan_type": plan if isinstance(plan, str) else None,
    }


def _parse_codex_credential(raw: str) -> dict:
    text = (raw or "").strip()
    if not text:
        raise QuotaProviderError("credential is empty")
    if text.lower().startswith("bearer "):
        text = text[7:].strip()
    data: Any = None
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except Exception as exc:
            raise QuotaProviderError(f"credential JSON is invalid: {exc}") from exc
    if isinstance(data, dict):
        tokens = data.get("tokens") if isinstance(data.get("tokens"), dict) else data
        access = tokens.get("access_token") or tokens.get("accessToken")
        if not isinstance(access, str) or not access:
            raise QuotaProviderError("credential has no tokens.access_token")
        return {
            "access_token": access,
            "refresh_token": tokens.get("refresh_token")
            if isinstance(tokens.get("refresh_token"), str)
            else None,
            "id_token": tokens.get("id_token")
            if isinstance(tokens.get("id_token"), str)
            else None,
            "account_id": tokens.get("account_id")
            if isinstance(tokens.get("account_id"), str)
            else None,
            "email": data.get("email") if isinstance(data.get("email"), str) else None,
        }
    if text.count(".") != 2:
        raise QuotaProviderError(
            "expected ~/.codex/auth.json contents or a JWT access token"
        )
    return {"access_token": text}


def _codex_window(value, group_id: str) -> dict | None:
    if not isinstance(value, dict):
        return None
    try:
        raw_used = value.get("used_percent")
        if isinstance(raw_used, bool):
            return None
        used = float(raw_used)
        if not math.isfinite(used):
            return None
    except (TypeError, ValueError, OverflowError):
        return None
    used = max(0.0, min(100.0, used))
    remaining = round(1.0 - used / 100.0, 6)

    def valid_number(number):
        return isinstance(number, (int, float)) and not isinstance(number, bool) and math.isfinite(number)

    reset_at = value.get("reset_at")
    if not valid_number(reset_at):
        reset_after = value.get("reset_after_seconds")
        reset_at = datetime.now(timezone.utc).timestamp() + reset_after if valid_number(reset_after) else None
    seconds = value.get("limit_window_seconds")
    window = {18000: "5h", 604800: "weekly", 86400: "daily"}.get(seconds, "quota") if valid_number(seconds) else "quota"
    label = {"5h": "5-Hour", "weekly": "Weekly", "daily": "Daily"}.get(window, "Usage")
    bucket = {
        "id": group_id,
        "name": label,
        "window": window,
        "remaining_fraction": remaining,
    }
    if reset_at is not None:
        try:
            bucket["reset_at"] = _iso(datetime.fromtimestamp(float(reset_at), timezone.utc))
        except (ValueError, OverflowError, OSError):
            pass
    return {
        "id": group_id,
        "name": f"{label} limit",
        "buckets": [bucket],
    }


async def _fetch_codex(credential: dict) -> dict:
    identity = _codex_identity(credential)
    headers = {
        "Authorization": f"Bearer {credential['access_token']}",
        "Accept": "application/json",
    }
    if identity.get("account_id"):
        headers["ChatGPT-Account-Id"] = identity["account_id"]
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            response = await client.get(
                "https://chatgpt.com/backend-api/wham/usage", headers=headers
            )
    except httpx.HTTPError as exc:
        raise QuotaProviderError(f"usage request failed: {type(exc).__name__}") from exc
    if response.status_code != 200:
        if response.status_code in (401, 403):
            raise QuotaAuthError(f"usage API returned HTTP {response.status_code}")
        detail = ""
        try:
            body = response.json()
            if isinstance(body, dict):
                detail = str(body.get("detail") or body.get("error") or "")[:160]
        except Exception:
            pass
        raise QuotaProviderError(
            f"usage API returned HTTP {response.status_code}"
            + (f": {detail}" if detail else "")
        )
    try:
        payload = response.json()
    except Exception as exc:
        raise QuotaProviderError("usage API returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise QuotaProviderError("usage API returned an unexpected document")

    email = payload.get("email") or identity.get("email")
    plan = payload.get("plan_type") or identity.get("plan_type")
    rate = payload.get("rate_limit") if isinstance(payload.get("rate_limit"), dict) else {}
    groups = []
    primary = _codex_window(rate.get("primary_window"), "primary")
    secondary = _codex_window(rate.get("secondary_window"), "secondary")
    if primary:
        groups.append(primary)
    if secondary:
        groups.append(secondary)
    # Optional independently metered features use the same rate-window
    # schema as the primary pool (OpenAI Codex AdditionalRateLimitDetails).
    feature_rates = [("code_review", "Code review", payload.get("code_review_rate_limit"))]
    additional = payload.get("additional_rate_limits")
    if isinstance(additional, list):
        for index, entry in enumerate(additional):
            if isinstance(entry, dict):
                name = entry.get("limit_name") or entry.get("metered_feature") or "Additional quota"
                feature_rates.append((f"additional-{index}", str(name), entry.get("rate_limit")))
    for ident, name, feature_rate in feature_rates:
        if not isinstance(feature_rate, dict):
            continue
        buckets = []
        for key in ("primary_window", "secondary_window"):
            parsed = _codex_window(feature_rate.get(key), f"{ident}-{key}")
            if parsed:
                buckets.extend(parsed["buckets"])
        if buckets:
            groups.append({"id": ident, "name": name, "buckets": buckets})
    observed_at = _iso(datetime.now(timezone.utc))
    if not groups:
        raise QuotaProviderError("usage API returned no rate-limit windows")

    fractions = [
        bucket["remaining_fraction"]
        for group in groups
        for bucket in group["buckets"]
        if isinstance(bucket.get("remaining_fraction"), (int, float))
    ]
    account = {
        "email": email or identity.get("email") or "unknown",
        "name": identity.get("name") or email or "Codex account",
        "tier": {"id": plan, "name": plan} if plan else None,
        "observed_at": observed_at,
        "groups": groups,
    }
    if fractions:
        account["min_remaining_fraction"] = min(fractions)
    return {
        "credential": {
            "access_token": credential["access_token"],
            "refresh_token": credential.get("refresh_token"),
            "id_token": credential.get("id_token"),
            "account_id": identity.get("account_id"),
        },
        "identity": {
            "email": account["email"],
            "name": account["name"],
            "plan": plan,
        },
        "quota": {
            "source": "codex",
            "observed_at": observed_at,
            "accounts": [account],
        },
    }


async def fetch_account(provider: str, raw_credential: str) -> dict:
    if provider == "antigravity":
        from agent_fleet.services.antigravity_quota import fetch

        return await fetch(raw_credential)
    if provider == "codex":
        normalized = _parse_codex_credential(raw_credential)
        return await _fetch_codex(normalized)
    if provider in ("workbuddy", "codebuddy"):
        from agent_fleet.services.workbuddy_quota import fetch

        return await fetch(raw_credential)
    if provider == "trae":
        from agent_fleet.services.trae_quota import fetch

        return await fetch(raw_credential)
    if provider == "qodercn":
        from agent_fleet.services.qoder_quota import fetch

        return await fetch(raw_credential)
    raise QuotaProviderError(f"unsupported provider: {provider}")
