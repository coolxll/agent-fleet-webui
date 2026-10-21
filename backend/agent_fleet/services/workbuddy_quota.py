"""Workbuddy quota provider for agent-fleet.

Connects to Workbuddy / CodeBuddy billing API to query user credit packages,
remaining credits, usage, and reset windows.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
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


def find_local_workbuddy_auth() -> str | None:
    """Auto-detect local WorkBuddy / CodeBuddy login credential documents."""
    home = Path.home()
    candidate_dirs = [
        home / "Library/Application Support/CodeBuddyExtension/Data/Public/auth",
        home / ".local/share/CodeBuddyExtension/Data/Public/auth",
        home / ".codebuddy",
        home / ".workbuddy",
    ]
    appdata = os.environ.get("LOCALAPPDATA")
    if appdata:
        candidate_dirs.append(Path(appdata) / "CodeBuddyExtension/Data/Public/auth")

    file_priorities = [
        "Tencent-Cloud.coding-copilot.info",
        "workbuddy-desktop.info",
        "workbuddy-desktop-ai.info",
    ]

    for d in candidate_dirs:
        if not d.is_dir():
            continue
        for name in file_priorities:
            f = d / name
            if f.is_file():
                try:
                    txt = f.read_text(encoding="utf-8").strip()
                    if txt:
                        data = json.loads(txt)
                        auth = data.get("auth", {})
                        if auth.get("accessToken") or auth.get("access_token"):
                            return txt
                except Exception:
                    continue
        for f in d.glob("*.info"):
            try:
                txt = f.read_text(encoding="utf-8").strip()
                if txt:
                    data = json.loads(txt)
                    auth = data.get("auth", {})
                    if auth.get("accessToken") or auth.get("access_token"):
                        return txt
            except Exception:
                continue
    return None


def parse_credential(raw: str | None) -> dict:
    text = (raw or "").strip()
    if not text or text.lower() == "auto":
        local_auth = find_local_workbuddy_auth()
        if not local_auth:
            raise QuotaProviderError("Workbuddy credential is empty and no local CodeBuddyExtension login found")
        text = local_auth

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


def _package_remain_used(acct: dict) -> tuple[float, float, float]:
    def amount(key, default=0):
        value = acct.get(key)
        value = default if value is None else value
        try:
            number = float(value)
            if not math.isfinite(number) or number < 0:
                raise ValueError()
            return number
        except (ValueError, TypeError, OverflowError) as exc:
            raise QuotaProviderError("Workbuddy returned invalid package amounts") from exc

    prefix = "CycleCapacity" if amount("CycleCapacitySize") > 0 else "Capacity"
    size = amount(prefix + "Size")
    if acct.get(prefix + "Remain") is None and acct.get(prefix + "Used") is None and size > 0:
        raise QuotaProviderError("Workbuddy returned no package balance")
    used = amount(prefix + "Used")
    remain = min(size, amount(prefix + "Remain", max(0, size - used)))
    # Amounts must all describe the same cycle, never the lifetime package.
    used = max(used, size - remain)
    return max(0, size - used), used, size


def _resource_data(payload) -> dict:
    if not isinstance(payload, dict):
        raise QuotaProviderError("Workbuddy returned an invalid resource document")
    code = payload.get("code", payload.get("Code", 0))
    if code not in (0, "0"):
        raise QuotaProviderError("Workbuddy API returned an error code")
    obj = payload.get("data") or payload.get("Response") or payload
    if isinstance(obj, dict):
        obj = obj.get("Response", obj)
    if isinstance(obj, dict):
        obj = obj.get("Data") or obj.get("data") or obj
    if not isinstance(obj, dict) or not isinstance(obj.get("Accounts"), list):
        raise QuotaProviderError("Workbuddy returned no package list")
    if not all(isinstance(a, dict) for a in obj["Accounts"]):
        raise QuotaProviderError("Workbuddy returned invalid packages")
    return obj


async def fetch(raw_credential: str | dict) -> dict:
    if isinstance(raw_credential, dict):
        cred = raw_credential
    else:
        cred = parse_credential(raw_credential)

    is_global = _is_global(cred)
    base_domain = "www.workbuddy.ai" if is_global else "www.codebuddy.cn"
    paths = [
        "/billing/meter/get-user-resource",
        "/v2/billing/meter/get-user-resource",
    ]

    headers = {
        "Authorization": f"Bearer {cred['access_token']}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "WorkBuddy/5.5.6",
        "Origin": f"https://{base_domain}",
        "Referer": f"https://{base_domain}/",
        "X-Domain": base_domain,
        "X-Product": "SaaS",
        "X-IDE-Type": "WorkBuddy",
    }
    if cred.get("uid"):
        headers["X-User-Id"] = cred["uid"]

    now = datetime.now(timezone(timedelta(hours=8)))
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

    raw_accounts = []
    async with httpx.AsyncClient(timeout=25) as client:
        for page in range(1, 101):
            last_error = "no response"
            page_data = None
            for path in paths:
                try:
                    resp = await client.post(f"https://{base_domain}{path}", headers=headers,
                                             json={**body, "PageNumber": page})
                    if resp.status_code != 200:
                        last_error = f"HTTP {resp.status_code}"
                        continue
                    page_data = _resource_data(resp.json())
                    break
                except (httpx.HTTPError, ValueError, QuotaProviderError) as exc:
                    last_error = str(exc)
            if page_data is None:
                raise QuotaProviderError(f"Workbuddy API request failed: {last_error}")
            rows = page_data["Accounts"]
            raw_accounts.extend(rows)
            total_count = page_data.get("TotalCount")
            if isinstance(total_count, int) and len(raw_accounts) >= total_count:
                break
            if not rows:
                if isinstance(total_count, int) and len(raw_accounts) < total_count:
                    raise QuotaProviderError("Workbuddy returned an incomplete package list")
                break
            if total_count is None and len(rows) < body["PageSize"]:
                break
        else:
            raise QuotaProviderError("Workbuddy package pagination exceeded its limit")

    total_remain = 0
    total_used = 0
    total_size = 0
    future_reset_times = []
    pkg_groups: dict[str, dict] = {}

    now_utc = datetime.now(timezone.utc)

    for acct in raw_accounts:
        r, u, s = _package_remain_used(acct)
        pkg_name = acct.get("PackageName") or "积分套餐"
        end_time_raw = acct.get("CycleEndTime") or acct.get("PackageEndTime")
        end_iso = _parse_cycle_time(end_time_raw)

        if end_iso:
            dt = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
            if dt <= now_utc:
                continue
            if r > 0:
                future_reset_times.append(dt)
        total_remain += r
        total_used += u
        total_size += s

        if pkg_name not in pkg_groups:
            pkg_groups[pkg_name] = {
                "name": pkg_name,
                "total": 0,
                "used": 0,
                "remaining": 0,
                "count": 0,
                "reset_at": end_iso,
            }
        g = pkg_groups[pkg_name]
        g["total"] += s
        g["used"] += u
        g["remaining"] += r
        g["count"] += 1
        if end_iso:
            if not g["reset_at"] or end_iso < g["reset_at"]:
                g["reset_at"] = end_iso

    packages_detail = []
    for pkg_name, g in pkg_groups.items():
        display_name = f"{pkg_name} ({g['count']}个包汇总)" if g["count"] > 1 else pkg_name
        packages_detail.append({
            "name": display_name,
            "total": g["total"],
            "used": g["used"],
            "remaining": g["remaining"],
            "count": g["count"],
            "reset_at": g["reset_at"],
            "unit": "积分",
        })
    packages_detail.sort(key=lambda p: (p.get("remaining") or 0), reverse=True)

    if total_size > 0:
        if (total_size - total_remain) > total_used:
            total_used = total_size - total_remain

    # Identify daily grant packages (e.g. daily check-in / allowance gifts)
    daily_breakdown = []
    for pkg in packages_detail:
        name = pkg.get("name", "")
        is_daily = "每日" in name or "day" in name.lower() or "daily" in name.lower()
        if is_daily:
            daily_breakdown.append({
                "name": name,
                "used": pkg["used"],
                "total": pkg["total"],
                "remaining": pkg["remaining"],
                "reset_at": pkg["reset_at"],
                "unit": "积分",
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
        "reset_kind": "partial_expiry",
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
    realm = "global" if is_global else "cn"
    account_uid = cred.get("uid")
    display_name = cred.get("nickname") or cred.get("uid") or "Workbuddy"

    # Workbuddy credentials do not always include a user id. Do not invent an
    # email-shaped identity in that case: use a one-way token fingerprint for
    # stable, private deduplication. Realm is part of the key because CN and
    # Global user-id spaces are independent.
    if account_uid:
        account_id = f"workbuddy:{realm}:uid:{account_uid}"
        email = f"{account_uid}@workbuddy" if "@" not in str(account_uid) else account_uid
    else:
        token_fingerprint = hashlib.sha256(cred["access_token"].encode("utf-8")).hexdigest()
        account_id = f"workbuddy:{realm}:token:{token_fingerprint}"
        email = None

    account = {
        "email": email,
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
            "account_id": account_id,
            "name": account["name"],
            "plan": account["tier"]["name"],
        },
        "quota": {
            "source": "workbuddy",
            "observed_at": observed_at,
            "accounts": [account],
        },
    }
