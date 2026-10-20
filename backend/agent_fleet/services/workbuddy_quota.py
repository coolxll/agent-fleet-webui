"""Workbuddy quota provider for agent-fleet.

Connects to Workbuddy / CodeBuddy billing API to query user credit packages,
remaining credits, usage, and reset windows.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx

from agent_fleet.services.quota_providers import QuotaProviderError


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_cycle_time(time_str: str | None) -> str | None:
    if not time_str or not time_str.strip():
        return None
    time_str = time_str.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(time_str, fmt)
            # Upstream timestamps are China Standard Time (UTC+8)
            cst = timezone(timedelta(hours=8))
            return _iso(dt.replace(tzinfo=cst))
        except ValueError:
            pass
    return None


def parse_credential(raw: str) -> dict:
    text = (raw or "").strip()
    if not text:
        raise QuotaProviderError("Workbuddy credential is empty")

    if text.lower().startswith("bearer "):
        text = text[7:].strip()

    data: Any = None
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except Exception as exc:
            raise QuotaProviderError(f"Workbuddy credential JSON is invalid: {exc}") from exc

    if isinstance(data, dict):
        auth_section = data.get("auth") if isinstance(data.get("auth"), dict) else data
        account_section = data.get("account") if isinstance(data.get("account"), dict) else data

        access_token = (
            auth_section.get("accessToken")
            or auth_section.get("access_token")
            or data.get("accessToken")
            or data.get("access_token")
        )
        if not isinstance(access_token, str) or not access_token:
            raise QuotaProviderError("Workbuddy credential missing accessToken")

        refresh_token = (
            auth_section.get("refreshToken")
            or auth_section.get("refresh_token")
            or data.get("refreshToken")
            or data.get("refresh_token")
        )
        domain = auth_section.get("domain") or data.get("domain") or ""
        realm = auth_section.get("realm") or data.get("realm") or ""
        uid = (
            account_section.get("uid")
            or data.get("uid")
            or account_section.get("userId")
            or data.get("userId")
        )
        nickname = (
            account_section.get("nickname")
            or account_section.get("name")
            or data.get("nickname")
            or data.get("name")
        )

        return {
            "access_token": access_token.strip(),
            "refresh_token": refresh_token.strip() if isinstance(refresh_token, str) else None,
            "domain": str(domain).strip(),
            "realm": str(realm).strip(),
            "uid": str(uid).strip() if uid else None,
            "nickname": str(nickname).strip() if nickname else None,
        }

    # Plain token string
    return {
        "access_token": text,
        "domain": "",
        "realm": "",
        "uid": None,
        "nickname": None,
    }


def _is_global(credential: dict) -> bool:
    realm = (credential.get("realm") or "").lower()
    if realm == "global":
        return True
    domain = (credential.get("domain") or "").lower()
    return domain == "workbuddy.ai" or domain.endswith(".workbuddy.ai")


def _package_remain_used(acct: dict) -> tuple[int, int, int]:
    cycle_size = int(acct.get("CycleCapacitySize") or 0)
    cycle_remain = int(acct.get("CycleCapacityRemain") or 0)
    cycle_used = int(acct.get("CycleCapacityUsed") or 0)

    if cycle_size > 0:
        remain = max(0, min(cycle_size, cycle_remain))
        size = cycle_size
        used = size - remain
        if cycle_used > used:
            used = cycle_used
            if size >= used:
                remain = size - used
        return remain, used, size

    cap_size = int(acct.get("CapacitySize") or 0)
    cap_remain = int(acct.get("CapacityRemain") or 0)
    cap_used = int(acct.get("CapacityUsed") or 0)

    remain = cap_remain
    used = cap_used
    size = cap_size
    if used == 0 and size > remain:
        used = size - remain
    return remain, used, size


async def fetch(raw_credential: str | dict) -> dict:
    if isinstance(raw_credential, dict):
        cred = raw_credential
    else:
        cred = parse_credential(raw_credential)

    is_global = _is_global(cred)
    base_domain = "www.workbuddy.ai" if is_global else "www.codebuddy.cn"
    paths = (
        ["/billing/meter/get-user-resource", "/v2/billing/meter/get-user-resource"]
        if is_global
        else ["/v2/billing/meter/get-user-resource"]
    )

    headers = {
        "Authorization": f"Bearer {cred['access_token']}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Origin": f"https://{base_domain}",
        "Referer": f"https://{base_domain}/",
        "X-Domain": base_domain,
    }
    if cred.get("uid"):
        headers["X-User-Id"] = cred["uid"]

    now = datetime.now()
    begin = now.strftime("%Y-%m-%d %H:%M:%S")
    end = (now + timedelta(days=365 * 100)).strftime("%Y-%m-%d %H:%M:%S")
    body = {
        "PageNumber": 1,
        "PageSize": 100,
        "ProductCode": "p_tcaca",
        "Status": [0, 3],
        "PackageEndTimeRangeBegin": begin,
        "PackageEndTimeRangeEnd": end,
    }

    last_error = ""
    payload = None

    async with httpx.AsyncClient(timeout=25) as client:
        for path in paths:
            url = f"https://{base_domain}{path}"
            try:
                resp = await client.post(url, headers=headers, json=body)
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                continue

            if resp.status_code == 404 and len(paths) > 1 and path == paths[0]:
                continue

            if resp.status_code != 200:
                last_error = f"HTTP {resp.status_code}: {resp.text[:160]}"
                continue

            try:
                res_json = resp.json()
            except Exception as exc:
                last_error = f"invalid JSON: {exc}"
                continue

            # Check response envelope
            code = res_json.get("code") if "code" in res_json else res_json.get("Code", 0)
            if code != 0:
                msg = res_json.get("msg") or res_json.get("Message") or f"code={code}"
                raise QuotaProviderError(f"Workbuddy API error: {msg}")

            payload = res_json
            break

    if payload is None:
        raise QuotaProviderError(f"Workbuddy API request failed: {last_error or 'no response'}")

    # Extract accounts and dosages
    resp_obj = payload.get("Response") or payload.get("data") or payload
    data_obj = resp_obj.get("Data") if isinstance(resp_obj, dict) else {}
    raw_accounts = data_obj.get("Accounts") or []
    total_dosage = int(data_obj.get("TotalDosage") or 0)

    total_remain = 0
    total_used = 0
    total_size = 0
    packages_detail = []
    future_reset_times = []

    now_utc = datetime.now(timezone.utc)

    for acct in raw_accounts:
        r, u, s = _package_remain_used(acct)
        total_remain += r
        total_used += u
        total_size += s

        pkg_name = acct.get("PackageName") or "积分套餐"
        end_time_raw = acct.get("CycleEndTime") or acct.get("PackageEndTime")
        end_iso = _parse_cycle_time(end_time_raw)

        if end_iso:
            try:
                dt = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
                if dt > now_utc:
                    future_reset_times.append(dt)
            except Exception:
                pass

        packages_detail.append({
            "name": pkg_name,
            "total": s,
            "used": u,
            "remaining": r,
            "reset_at": end_iso,
        })

    if total_size > 0:
        if (total_size - total_remain) > total_used:
            total_used = total_size - total_remain

    if total_dosage > total_size:
        total_size = total_dosage
        if (total_size - total_remain) > total_used:
            total_used = total_size - total_remain

    # Identify packages with daily cycle or daily usage
    daily_breakdown = []
    for pkg in packages_detail:
        name = pkg.get("name", "")
        # If the package is a daily grant, or expires within 24 hours
        is_daily = "每日" in name or "day" in name.lower() or "daily" in name.lower()
        if not is_daily and pkg.get("reset_at"):
            try:
                dt = datetime.fromisoformat(pkg["reset_at"].replace("Z", "+00:00"))
                if 0 < (dt - now_utc).total_seconds() <= 86400 * 1.5:
                    is_daily = True
            except Exception:
                pass

        if is_daily or pkg["used"] > 0:
            daily_breakdown.append({
                "name": name,
                "used": pkg["used"],
                "total": pkg["total"],
                "remaining": pkg["remaining"],
                "reset_at": pkg["reset_at"],
            })

    # Calculate remaining fraction
    remaining_fraction = round(total_remain / total_size, 4) if total_size > 0 else 0.0
    remaining_fraction = max(0.0, min(1.0, remaining_fraction))

    earliest_reset = min(future_reset_times) if future_reset_times else None
    earliest_reset_iso = _iso(earliest_reset) if earliest_reset else None

    # Construct Quota Groups
    credits_bucket = {
        "id": "credits",
        "name": "积分额度",
        "window": "cycle",
        "remaining_fraction": remaining_fraction,
        "reset_at": earliest_reset_iso,
        "used": total_used,
        "total": total_size,
        "remaining": total_remain,
        "unit": "credits",
    }

    groups = [
        {
            "id": "credits_group",
            "name": f"积分余额: {total_remain:,} / {total_size:,} credits",
            "buckets": [credits_bucket],
            "packages": packages_detail,
            "daily_consumption": daily_breakdown,
        }
    ]

    observed_at = _iso(datetime.now(timezone.utc))
    account_uid = cred.get("uid") or "workbuddy-user"
    display_name = cred.get("nickname") or cred.get("uid") or "Workbuddy"

    account = {
        "email": f"{account_uid}@workbuddy" if "@" not in str(account_uid) else account_uid,
        "name": display_name,
        "tier": {"id": "workbuddy", "name": "WorkBuddy Global" if is_global else "WorkBuddy CN"},
        "observed_at": observed_at,
        "groups": groups,
        "min_remaining_fraction": remaining_fraction,
        "packages": packages_detail,
        "daily_consumption": daily_breakdown,
    }

    return {
        "credential": {
            "access_token": cred["access_token"],
            "refresh_token": cred.get("refresh_token"),
            "domain": cred.get("domain") or base_domain,
            "realm": "global" if is_global else "cn",
            "uid": cred.get("uid"),
            "nickname": cred.get("nickname"),
        },
        "identity": {
            "email": account["email"],
            "name": account["name"],
            "plan": account["tier"]["name"],
        },
        "quota": {
            "source": "workbuddy",
            "observed_at": observed_at,
            "accounts": [account],
        },
    }
