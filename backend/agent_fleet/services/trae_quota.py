"""Read Trae CN credit balances using an imported Cloud-IDE-JWT credential.

The website's entitlement summary is preferred over IDE pack totals. Endpoint
and field references: sddvcm/workbuddy-wild (internal/traework/client.go) and
Javis603/token-monitor (src/shared/providers/trae/limits.js). No generation,
check-in, or token-exchange operations are used.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone

import httpx

from agent_fleet.services.quota_providers import QuotaProviderError, _decode_jwt_payload, _iso

API = "https://api.trae.cn"
PATHS = (
    "/trae/api/v2/pay/user_current_entitlement_list",
    "/trae/api/v2/pay/ide_user_ent_usage",
)


def _header(value, label: str) -> str:
    if not isinstance(value, str) or any(c in value for c in "\r\n"):
        raise QuotaProviderError(f"Trae {label} must be a single-line string")
    return value.strip()


def parse_credential(raw: str) -> dict:
    text = (raw or "").strip()
    data = {}
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise QuotaProviderError("Trae credential JSON is invalid") from exc
        text = next((data[k] for k in ("access_token", "accessToken", "token", "authorization", "Authorization", "Cloud-IDE-Token") if data.get(k)), "")
    token = _header(text, "token").strip("\"'").strip()
    token = re.sub(r"^authorization\s*:\s*", "", token, flags=re.IGNORECASE)
    token = re.sub(r"^(?:cloud-ide-jwt|bearer)\s+", "", token, flags=re.IGNORECASE).strip()
    if not token or any(c.isspace() for c in token):
        raise QuotaProviderError("Trae credential requires a Cloud-IDE-JWT access token")
    device = data.get("device_id") or data.get("deviceId") or data.get("X-Device-Id") or ""
    return {"access_token": token, "device_id": _header(device, "device ID")}


def _amount(value) -> float:
    try:
        if value is None or isinstance(value, bool) or isinstance(value, str) and not value.strip():
            raise ValueError()
        number = float(value)
        if not math.isfinite(number) or number < 0:
            raise ValueError()
        return number
    except (ValueError, TypeError, OverflowError) as exc:
        raise QuotaProviderError("Trae returned invalid credit amounts") from exc


def _expiry(value) -> str | None:
    try:
        ts = _amount(value)
        if ts > 1e11:
            ts /= 1000
        if not 0 < ts < datetime(2100, 1, 1, tzinfo=timezone.utc).timestamp():
            return None
        return _iso(datetime.fromtimestamp(ts, timezone.utc))
    except (QuotaProviderError, ValueError, OverflowError, OSError):
        return None


def _unwrap(payload) -> dict:
    if not isinstance(payload, dict):
        raise QuotaProviderError("Trae returned an invalid quota document")
    for key in ("code", "Code"):
        if key in payload and payload[key] not in (0, "0", None):
            raise QuotaProviderError("Trae API rejected the request; check the token and device ID")
    metadata = payload.get("ResponseMetadata")
    if isinstance(metadata, dict) and metadata.get("Error"):
        raise QuotaProviderError("Trae API rejected the request")
    if any(k in payload for k in ("usage_summary", "usageSummary", "user_entitlement_pack_list", "userEntitlementPackList")):
        return payload
    for key in ("data", "result", "Data", "Result"):
        if isinstance(payload.get(key), dict):
            return _unwrap(payload[key])
    return payload


def parse_usage(payload: dict) -> dict:
    body = _unwrap(payload)
    summary = body.get("usage_summary", body.get("usageSummary"))
    if summary is not None:
        if not isinstance(summary, dict):
            raise QuotaProviderError("Trae returned an invalid credit summary")
        total = _amount(summary.get("total_amount", summary.get("totalAmount")))
        used = _amount(summary.get("consumed_amount", summary.get("consumedAmount")))
    else:
        total = used = 0.0

    raw_packs = body.get("user_entitlement_pack_list", body.get("userEntitlementPackList"))
    if raw_packs is None and summary is None:
        raise QuotaProviderError("Trae returned no credit summary or entitlement packs")
    if raw_packs is not None and not isinstance(raw_packs, list):
        raise QuotaProviderError("Trae returned invalid entitlement packs")
    packages = []
    user_ids = set()
    for pack in raw_packs or []:
        try:
            if not isinstance(pack, dict):
                raise QuotaProviderError("Trae returned an invalid entitlement pack")
            base = pack.get("entitlement_base_info", pack.get("entitlementBaseInfo"))
            usage = pack.get("usage")
            if not isinstance(base, dict) or not isinstance(usage, dict):
                raise QuotaProviderError("Trae returned incomplete entitlement details")
            quota = base.get("quota")
            if not isinstance(quota, dict):
                raise QuotaProviderError("Trae returned an entitlement without quota")
            raw_limit = quota.get("credits_limit", quota.get("creditsLimit"))
            raw_used = usage.get("credits_amount", usage.get("creditsAmount"))
            has_limit = "credits_limit" in quota or "creditsLimit" in quota
            has_usage = "credits_amount" in usage or "creditsAmount" in usage
            if not has_limit and not has_usage:
                continue  # Feature flags are not spendable credits.
            pack_total = _amount(raw_limit)
            pack_used = _amount(raw_used) if has_usage else 0.0
            if pack_total == 0 and pack_used == 0:
                continue
            if pack_total == 0:
                raise QuotaProviderError("Trae reported consumption without credit capacity")
            uid = base.get("user_id", base.get("userId"))
            if isinstance(uid, (str, int)) and not isinstance(uid, bool) and str(uid):
                user_ids.add(str(uid))
            packages.append({
                "name": pack.get("display_desc") or pack.get("group_name") or "Trae credits",
                "total": pack_total, "used": pack_used,
                "remaining": max(0, pack_total - pack_used), "unit": "credits",
                "reset_at": _expiry(base.get("end_time", base.get("endTime"))),
            })
        except QuotaProviderError:
            if summary is None:
                raise  # A partial pack total would overstate the balance.
            # The authoritative summary remains usable without optional details.
    if len(user_ids) > 1:
        raise QuotaProviderError("Trae returned conflicting account identities")
    if summary is None:
        if not packages:
            raise QuotaProviderError("Trae returned no usable credit packs")
        total = _amount(sum(p["total"] for p in packages))
        used = _amount(sum(p["used"] for p in packages))
    return {
        "total": total, "used": used, "remaining": max(0, total - used),
        "packages": packages, "user_id": next(iter(user_ids), None),
    }


async def fetch(raw: str) -> dict:
    credential = parse_credential(raw)
    headers = {
        "Authorization": "Cloud-IDE-JWT " + credential["access_token"],
        "Accept": "application/json", "Content-Type": "application/json",
        "X-User-Region": "CN", "User-Agent": "Mozilla/5.0",
    }
    if credential["device_id"]:
        headers["X-Device-Id"] = credential["device_id"]
    async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
        for index, path in enumerate(PATHS):
            try:
                response = await client.post(API + path, headers=headers, json={})
            except httpx.HTTPError as exc:
                raise QuotaProviderError(f"Trae quota request failed: {type(exc).__name__}") from exc
            if response.status_code in (404, 405) and index == 0:
                continue
            if response.status_code in (401, 403):
                raise QuotaProviderError("Trae login expired or was rejected; import a current access token")
            if response.status_code != 200:
                raise QuotaProviderError(f"Trae quota request returned HTTP {response.status_code}")
            try:
                usage = parse_usage(response.json())
            except ValueError as exc:
                raise QuotaProviderError("Trae returned invalid quota JSON") from exc
            break

    claims = _decode_jwt_payload(credential["access_token"])
    uid = usage["user_id"] or claims.get("user_id") or claims.get("sub")
    # Account identity comes from the verified response/token, never user input.
    if isinstance(uid, (str, int)) and not isinstance(uid, bool) and str(uid):
        account_id = "trae:cn:uid:" + str(uid)
    else:
        fingerprint = hashlib.sha256(credential["access_token"].encode()).hexdigest()
        account_id = "trae:cn:token:" + fingerprint
    email = claims.get("email") if isinstance(claims.get("email"), str) else None
    name = claims.get("name") if isinstance(claims.get("name"), str) else None
    total, used, remaining = usage["total"], usage["used"], usage["remaining"]
    fraction = max(0.0, min(1.0, remaining / total)) if total > 0 else 0.0
    now = _iso(datetime.now(timezone.utc))
    bucket = {"id": "credits", "name": "Trae 积分", "window": "cycle",
              "remaining_fraction": fraction, "used": used, "total": total,
              "remaining": remaining, "unit": "credits", "reset_at": None}
    display_name = name or email or (f"Trae CN · {uid}" if uid else "Trae CN")
    account = {"email": email, "name": display_name, "observed_at": now,
               "groups": [{"id": "credits", "name": "Trae credits", "buckets": [bucket]}],
               "packages": usage["packages"], "min_remaining_fraction": fraction}
    return {"credential": credential,
            "identity": {"account_id": account_id, "email": email, "name": account["name"], "plan": None},
            "quota": {"source": "trae", "observed_at": now, "accounts": [account]}}
