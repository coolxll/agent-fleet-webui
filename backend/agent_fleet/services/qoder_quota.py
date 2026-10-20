"""QoderCN quota provider for agent-fleet.

Connects to Qoder OpenAPI to query user credit quota, org resource package,
plan details, and reset cycles. Supports auto-reading local credentials from
~/.qoder-cn/.auth/ or direct user input.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from agent_fleet.services.quota_providers import QuotaProviderError


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _decrypt_user_payload(ciphertext_b64: str, machine_id: str) -> dict:
    if len(machine_id) < 16:
        raise QuotaProviderError(f"machine_id too short ({len(machine_id)} chars)")

    key = machine_id[:16].encode("utf-8")
    try:
        raw_cipher = base64.b64decode(ciphertext_b64)
    except Exception as exc:
        raise QuotaProviderError(f"failed to base64 decode user file: {exc}") from exc

    if len(raw_cipher) % 16 != 0:
        raise QuotaProviderError(f"ciphertext not 16-byte block aligned: len={len(raw_cipher)}")

    cipher = Cipher(algorithms.AES(key), modes.CBC(key))
    decryptor = cipher.decryptor()
    try:
        padded = decryptor.update(raw_cipher) + decryptor.finalize()
    except Exception as exc:
        raise QuotaProviderError(f"AES decryption failed: {exc}") from exc

    if not padded:
        raise QuotaProviderError("decrypted payload is empty")

    pad_len = padded[-1]
    if pad_len == 0 or pad_len > 16:
        raise QuotaProviderError(f"invalid PKCS7 padding length: {pad_len}")

    if padded[-pad_len:] != bytes([pad_len]) * pad_len:
        raise QuotaProviderError("invalid PKCS7 padding bytes")

    plain = padded[:-pad_len]
    try:
        return json.loads(plain.decode("utf-8"))
    except Exception as exc:
        raise QuotaProviderError(f"decrypted data is not valid JSON: {exc}") from exc


def _find_local_qoder_auth() -> tuple[str, str] | None:
    """Finds machine_id and user ciphertext from standard QoderCN locations."""
    home = Path.home()
    candidates = [
        home / ".qoder-cn" / ".auth",
        home / ".qoder-cn" / "shared_client",
        home / ".qodercn" / ".auth",
        home / "Library" / "Application Support" / "QoderCN" / "SharedClientCache",
        home / "Library" / "Application Support" / "qoder-cn",
    ]

    for d in candidates:
        id_file = None
        for id_name in ["machine_id", "id"]:
            p = d / id_name
            if p.is_file():
                id_file = p
                break

        user_file = d / "user"
        if id_file and user_file.is_file():
            try:
                mid = id_file.read_text(encoding="utf-8").strip()
                user_b64 = user_file.read_text(encoding="utf-8").strip()
                if mid and user_b64:
                    return mid, user_b64
            except Exception:
                continue

    return None


def parse_credential(raw: str | None) -> dict:
    text = (raw or "").strip()

    # Auto-detection from local files if empty or 'auto'
    if not text or text.lower() == "auto":
        found = _find_local_qoder_auth()
        if not found:
            raise QuotaProviderError(
                "No QoderCN credentials provided, and could not find ~/.qoder-cn/.auth/ files"
            )
        mid, user_b64 = found
        data = _decrypt_user_payload(user_b64, mid)
        return {
            "access_token": data.get("access_token") or data.get("accessToken"),
            "security_oauth_token": data.get("security_oauth_token") or data.get("securityOAuthToken"),
            "uid": data.get("uid"),
            "name": data.get("name"),
            "organization_name": data.get("organization_name"),
            "organization_id": data.get("organization_id"),
            "user_type": data.get("user_type"),
        }

    if text.lower().startswith("bearer "):
        text = text[7:].strip()

    data: Any = None
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except Exception as exc:
            raise QuotaProviderError(f"QoderCN credential JSON is invalid: {exc}") from exc

    if isinstance(data, dict):
        # Case 1: user provided { "machine_id": "...", "user": "..." }
        if "machine_id" in data and "user" in data:
            decrypted = _decrypt_user_payload(data["user"], data["machine_id"])
            return {
                "access_token": decrypted.get("access_token") or decrypted.get("accessToken"),
                "security_oauth_token": (
                    decrypted.get("security_oauth_token") or decrypted.get("securityOAuthToken")
                ),
                "uid": decrypted.get("uid"),
                "name": decrypted.get("name"),
                "organization_name": decrypted.get("organization_name"),
                "organization_id": decrypted.get("organization_id"),
                "user_type": decrypted.get("user_type"),
            }

        access_token = data.get("access_token") or data.get("accessToken")
        if not access_token:
            raise QuotaProviderError("QoderCN credential JSON missing access_token")

        return {
            "access_token": access_token.strip(),
            "security_oauth_token": (
                data.get("security_oauth_token") or data.get("securityOAuthToken")
            ),
            "uid": data.get("uid"),
            "name": data.get("name"),
            "organization_name": data.get("organization_name"),
            "organization_id": data.get("organization_id"),
            "user_type": data.get("user_type"),
        }

    # Plain token string
    return {
        "access_token": text,
        "security_oauth_token": None,
        "uid": None,
        "name": None,
        "organization_name": None,
        "organization_id": None,
        "user_type": None,
    }


async def fetch(raw_credential: str | dict | None) -> dict:
    if isinstance(raw_credential, dict):
        cred = raw_credential
    else:
        cred = parse_credential(raw_credential)

    headers = {
        "Authorization": f"Bearer {cred['access_token']}",
        "Accept": "application/json",
        "User-Agent": "qodercli/1.1.74",
    }
    if cred.get("security_oauth_token"):
        headers["X-Security-Oauth-Token"] = cred["security_oauth_token"]

    usage_data = None
    userinfo_data = None
    plan_data = None

    async with httpx.AsyncClient(timeout=25) as client:
        # 1. Fetch usage Presentation (Primary)
        for url in [
            "https://openapi.qoder.com.cn/sash/api/v2/me/usage",
            "https://openapi.qoder.com.cn/api/v2/quota/usage",
            "https://gateway.qoder.com.cn/api/v2/quota/usage",
        ]:
            try:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, dict):
                        usage_data = data
                        break
            except Exception:
                continue

        if not usage_data:
            raise QuotaProviderError("Failed to query QoderCN usage API (HTTP error or invalid response)")

        # 2. Fetch userinfo (Best-effort)
        try:
            resp = await client.get("https://openapi.qoder.com.cn/api/v1/userinfo", headers=headers)
            if resp.status_code == 200:
                userinfo_data = resp.json()
        except Exception:
            pass

        # 3. Fetch user plan (Best-effort)
        try:
            resp = await client.get("https://openapi.qoder.com.cn/api/v2/user/plan", headers=headers)
            if resp.status_code == 200:
                plan_data = resp.json()
        except Exception:
            pass

    # Unpack usage details
    qoder_usage = usage_data.get("qoderUsage") or usage_data
    user_quota = qoder_usage.get("userQuota") or {}
    total = float(user_quota.get("total") or 0)
    used = float(user_quota.get("used") or 0)
    remaining = float(user_quota.get("remaining") or (total - used))
    unit = user_quota.get("unit") or "credits"

    # Reset time
    expires_at_ms = qoder_usage.get("expiresAt")
    reset_at_iso = None
    if isinstance(expires_at_ms, (int, float)) and expires_at_ms > 0:
        # If timestamp is in milliseconds
        ts = expires_at_ms / 1000.0 if expires_at_ms > 1e11 else float(expires_at_ms)
        reset_at_iso = _iso(datetime.fromtimestamp(ts, timezone.utc))

    remaining_fraction = round(remaining / total, 4) if total > 0 else 0.0
    remaining_fraction = max(0.0, min(1.0, remaining_fraction))

    # Identify plan and identity
    plan_name = None
    if isinstance(plan_data, dict):
        plan_name = plan_data.get("plan_tier_name")
    if not plan_name and isinstance(qoder_usage.get("userType"), str):
        plan_name = qoder_usage["userType"].capitalize()
    if not plan_name:
        plan_name = cred.get("user_type") or "Qoder"

    user_name = None
    if isinstance(userinfo_data, dict):
        user_name = userinfo_data.get("name") or userinfo_data.get("username")
    if not user_name:
        user_name = cred.get("name") or cred.get("uid") or "Qoder User"

    org_name = None
    if isinstance(userinfo_data, dict):
        org_name = userinfo_data.get("organization_name")
    if not org_name:
        org_name = cred.get("organization_name")

    uid = (
        (userinfo_data.get("id") if isinstance(userinfo_data, dict) else None)
        or qoder_usage.get("userId")
        or cred.get("uid")
        or "qoder-user"
    )

    # Build quota buckets
    buckets = [
        {
            "id": "qoder_credits",
            "name": "个人额度",
            "window": "cycle",
            "remaining_fraction": remaining_fraction,
            "reset_at": reset_at_iso,
            "used": used,
            "remaining": remaining,
            "total": total,
            "unit": unit,
        }
    ]

    packages_detail = [
        {
            "name": "个人额度 (Personal Quota)",
            "total": total,
            "used": used,
            "remaining": remaining,
            "reset_at": reset_at_iso,
        }
    ]

    # Check orgResourcePackage
    org_pkg = qoder_usage.get("orgResourcePackage") or {}
    if org_pkg.get("available"):
        org_cap = float(org_pkg.get("cap") or 0)
        org_used = float(org_pkg.get("used") or 0)
        org_remain = float(org_pkg.get("remaining") or (org_cap - org_used))
        org_frac = round(org_remain / org_cap, 4) if org_cap > 0 else 0.0
        buckets.append({
            "id": "org_package",
            "name": "企业资源包",
            "window": "cycle",
            "remaining_fraction": max(0.0, min(1.0, org_frac)),
            "reset_at": reset_at_iso,
            "used": org_used,
            "remaining": org_remain,
            "total": org_cap,
            "unit": org_pkg.get("unit") or "credits",
        })
        packages_detail.append({
            "name": f"企业资源包 ({org_name or 'Org'})",
            "total": org_cap,
            "used": org_used,
            "remaining": org_remain,
            "reset_at": reset_at_iso,
        })

    daily_consumption = [
        {
            "name": "周期累计已用",
            "used": used,
            "total": total,
            "remaining": remaining,
            "reset_at": reset_at_iso,
        }
    ]

    groups = [
        {
            "id": "credits_group",
            "name": f"积分余额: {int(remaining):,} / {int(total):,} credits",
            "buckets": buckets,
            "packages": packages_detail,
            "daily_consumption": daily_consumption,
        }
    ]

    observed_at = _iso(datetime.now(timezone.utc))

    account = {
        "email": f"{uid}@qoder.cn" if "@" not in str(uid) else uid,
        "name": f"{user_name} ({org_name})" if org_name else user_name,
        "tier": {"id": "qoder", "name": f"Qoder {plan_name}"},
        "observed_at": observed_at,
        "groups": groups,
        "min_remaining_fraction": remaining_fraction,
        "packages": packages_detail,
        "daily_consumption": daily_consumption,
    }

    return {
        "credential": {
            "access_token": cred["access_token"],
            "security_oauth_token": cred.get("security_oauth_token"),
            "uid": uid,
            "name": user_name,
            "organization_name": org_name,
            "organization_id": cred.get("organization_id"),
        },
        "identity": {
            "email": account["email"],
            "name": account["name"],
            "plan": account["tier"]["name"],
        },
        "quota": {
            "source": "qodercn",
            "observed_at": observed_at,
            "accounts": [account],
        },
    }
