"""Antigravity OAuth refresh and online account quotas."""

import base64
import json
import math
from datetime import datetime, timedelta, timezone

import httpx

from agent_fleet.services.quota_providers import QuotaProviderError, _decode_jwt_payload, _iso

# Public desktop OAuth clients used by Antigravity, not user credentials.
DEFAULT_CLIENT = "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
CLIENTS = {
    DEFAULT_CLIENT: "",
    "884354919052-36trc1jjb3tguiac32ov6cod268c5blh.apps.googleusercontent.com": "",
}
API = "https://daily-cloudcode-pa.googleapis.com/v1internal:"
TIERS = {"free-tier": "Free", "g1-pro-tier": "Google AI Pro", "g1-ultra-tier": "Google AI Ultra", "g1-ultra-lite-tier": "Google AI Ultra Lite"}


def parse_credential(raw: str) -> dict:
    try:
        if raw.startswith("go-keyring-base64:"):
            raw = base64.b64decode(raw.split(":", 1)[1]).decode()
        data = json.loads(raw)
        inner = data.get("token") or data.get("tokens") or data
        if not isinstance(inner, dict):
            raise ValueError()
        credential = {k: inner[k] for k in (
            "access_token", "refresh_token", "id_token", "expiry", "expiry_date", "client_id"
        ) if k in inner}
        if not credential.get("refresh_token"):
            raise QuotaProviderError("Antigravity credential requires a refresh_token")
        claims = _decode_jwt_payload(credential.get("id_token") or "")
        audience = claims.get("aud")
        if isinstance(audience, list):
            audience = next((a for a in audience if a in CLIENTS), None)
        credential["client_id"] = credential.get("client_id") or (audience if audience in CLIENTS else DEFAULT_CLIENT)
        if credential["client_id"] not in CLIENTS:
            raise QuotaProviderError("unsupported Antigravity OAuth client")
        return credential
    except QuotaProviderError:
        raise
    except Exception as exc:
        raise QuotaProviderError("invalid Antigravity credential document") from exc


def expiry(credential: dict) -> datetime:
    try:
        if credential.get("expiry"):
            return datetime.fromisoformat(credential["expiry"].replace("Z", "+00:00")).astimezone(timezone.utc)
        return datetime.fromtimestamp(float(credential.get("expiry_date", 0)) / 1000, timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return datetime.min.replace(tzinfo=timezone.utc)


def fraction(value):
    try:
        number = float(value)
        return max(0.0, min(1.0, number)) if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def extract_raw_groups(summary: dict) -> list[dict]:
    if not isinstance(summary, dict):
        return []
    for key in ("groups", "response", "summary", "command"):
        val = summary.get(key)
        if isinstance(val, list):
            return val
        if isinstance(val, dict):
            if isinstance(val.get("groups"), list):
                return val["groups"]
            if isinstance(val.get("data", {}).get("groups"), list):
                return val["data"]["groups"]
    return []


def resolve_group_id_and_name(display_name: str, desc: str, buckets: list[dict]) -> tuple[str, str]:
    name = (display_name or "").strip()
    desc_str = (desc or "").strip()
    combined = f"{name} {desc_str}".lower()
    
    # 检查 buckets 中是否有 argon / gemini 4 标识
    bucket_idents = " ".join(b.get("bucketId", "") or b.get("id", "") for b in buckets).lower()
    
    # 1. Gemini 4 / Argon 独立额度
    if "argon" in combined or "argon" in bucket_idents or ("gemini" in combined and any(c.isdigit() for c in name)):
        return "gemini_4", name or "Gemini 4 Argon"
    
    # 2. 标准 Gemini 模型组
    if "gemini" in combined or any(b.get("bucketId", "").startswith("gemini-") for b in buckets):
        return "gemini", name or "Gemini models"
        
    # 3. 第三方模型组 (Claude / GPT)
    if any(k in combined for k in ("claude", "gpt", "3p", "third")) or any(b.get("bucketId", "").startswith("3p-") for b in buckets):
        return "third_party", name or "Claude and GPT models"
        
    slug = name.lower().replace(" ", "_") if name else "quota"
    return slug, name or "Quota"


def resolve_bucket(bucket: dict) -> dict | None:
    if not isinstance(bucket, dict) or bucket.get("disabled"):
        return None
        
    ident = (bucket.get("bucketId") or bucket.get("id") or "").strip()
    name = (bucket.get("displayName") or bucket.get("name") or "").strip()
    
    # 提取剩余比例
    remaining = fraction(bucket.get("remainingFraction"))
    if remaining is None:
        rem_obj = bucket.get("remaining")
        if isinstance(rem_obj, dict):
            remaining = fraction(rem_obj.get("remainingFraction"))
            if remaining is None:
                remaining = fraction(rem_obj.get("value"))
    if remaining is None:
        return None
        
    reset = bucket.get("resetTime") or bucket.get("reset_time")
    if not reset or not isinstance(reset, str):
        return None
        
    # 判断 window
    raw_window = (bucket.get("window") or "").strip().lower()
    if raw_window in ("weekly", "5h"):
        window = raw_window
    else:
        check_str = f"{raw_window} {ident.lower()} {name.lower()}"
        if "week" in check_str:
            window = "weekly"
        elif any(k in check_str for k in ("5h", "5-hour", "five", "session", "hour")):
            window = "5h"
        else:
            try:
                dt = datetime.fromisoformat(reset.replace("Z", "+00:00"))
                diff = dt - datetime.now(timezone.utc)
                window = "5h" if diff <= timedelta(hours=6) else "weekly"
            except Exception:
                window = "5h"

    display_name = name or ("5-Hour" if window == "5h" else "Weekly")
    return {
        "id": ident or f"bucket-{window}",
        "name": display_name,
        "window": window,
        "remaining_fraction": remaining,
        "reset_at": reset
    }


def quota_groups(summary: dict) -> list[dict]:
    raw_groups = extract_raw_groups(summary)
    groups = []
    
    for group in raw_groups:
        if not isinstance(group, dict):
            continue
            
        raw_buckets = group.get("buckets", [])
        if not isinstance(raw_buckets, list):
            continue
            
        group_id, group_name = resolve_group_id_and_name(
            group.get("displayName") or group.get("name") or "",
            group.get("description") or "",
            raw_buckets
        )
        
        parsed_buckets = []
        seen_windows = set()
        for b in raw_buckets:
            item = resolve_bucket(b)
            if item:
                # 若同一 group 内有多个相同 window 的 bucket，保留余量更低或更早重置的
                parsed_buckets.append(item)
                seen_windows.add(item["window"])
                
        if parsed_buckets:
            groups.append({
                "id": group_id,
                "name": group_name,
                "buckets": parsed_buckets
            })
            
    # 按标准顺序排序: gemini -> gemini_4 -> third_party -> 其他
    def group_sort_key(g):
        gid = g["id"]
        if gid == "gemini":
            return (0, "")
        if gid == "gemini_4":
            return (1, "")
        if gid == "third_party":
            return (2, "")
        return (3, g["name"])
        
    groups.sort(key=group_sort_key)
    return groups


def gemini_groups_from_models(models: dict, reference_weekly_reset: str | None = None) -> list[dict]:
    raw_models = models.get("models", {})
    if not isinstance(raw_models, dict):
        return []
        
    gemini_candidates = []
    gemini_4_candidates = []
    now = datetime.now(timezone.utc)
    
    for ident, model in raw_models.items():
        if not isinstance(model, dict):
            continue
        lower_id = ident.lower()
        display_name = (model.get("displayName") or model.get("label") or "").lower()
        
        if not lower_id.startswith("gemini-") and "gemini" not in lower_id:
            continue
            
        info = model.get("quotaInfo") or {}
        try:
            reset_str = info.get("resetTime")
            if not reset_str or not isinstance(reset_str, str):
                continue
            reset = datetime.fromisoformat(reset_str.replace("Z", "+00:00"))
            remaining = fraction(info.get("remainingFraction", 0))
            if remaining is None:
                continue
                
            diff = reset - now
            window = "5h" if diff <= timedelta(hours=6) else "weekly"
            
            # 判断是否为 Gemini 4 / Argon 独立模型
            is_gemini_4 = "4" in lower_id or "argon" in lower_id or "4" in display_name or "argon" in display_name
            target = gemini_4_candidates if is_gemini_4 else gemini_candidates
            target.append((window, remaining, reset, reset_str, model.get("displayName") or ident))
        except (KeyError, ValueError, TypeError):
            continue

    def build_group(gid: str, gname: str, candidates: list) -> dict | None:
        if not candidates:
            return None
        buckets = []
        win_candidates = {
            "5h": [c for c in candidates if c[0] == "5h"],
            "weekly": [c for c in candidates if c[0] == "weekly"],
        }
        for win in ("5h", "weekly"):
            clist = win_candidates[win]
            if clist:
                # 优先按余量升序，其次按重置时间升序
                clist.sort(key=lambda c: (c[1], c[2]))
                best = clist[0]
                buckets.append({
                    "id": f"{gid}-{win}",
                    "name": "5-Hour" if win == "5h" else "Weekly",
                    "window": win,
                    "remaining_fraction": best[1],
                    "reset_at": best[3]
                })

        # 确保 Gemini models 拥有完整的 5h 和 weekly 双桶
        has_5h = any(b["window"] == "5h" for b in buckets)
        has_weekly = any(b["window"] == "weekly" for b in buckets)

        if not has_weekly:
            weekly_reset = reference_weekly_reset or _iso(now + timedelta(days=7))
            min_rem = min((c[1] for c in candidates), default=1.0)
            buckets.append({
                "id": f"{gid}-weekly",
                "name": "Weekly",
                "window": "weekly",
                "remaining_fraction": min_rem,
                "reset_at": weekly_reset
            })

        if not has_5h:
            five_h_reset = _iso(now + timedelta(hours=5))
            buckets.insert(0, {
                "id": f"{gid}-5h",
                "name": "5-Hour",
                "window": "5h",
                "remaining_fraction": 0.0,
                "reset_at": five_h_reset
            })

        buckets.sort(key=lambda b: 0 if b["window"] == "5h" else 1)
        return {"id": gid, "name": gname, "buckets": buckets}

    result = []
    g_main = build_group("gemini", "Gemini models", gemini_candidates)
    if g_main:
        result.append(g_main)
    g_4 = build_group("gemini_4", "Gemini 4 Argon", gemini_4_candidates)
    if g_4:
        result.append(g_4)
    return result


def gemini_group(models: dict) -> dict | None:
    # 保持向后兼容函数接口
    groups = gemini_groups_from_models(models)
    return groups[0] if groups else None


async def request_json(client, method, url, operation, **kwargs):
    try:
        response = await client.request(method, url, **kwargs)
        if response.status_code != 200:
            raise QuotaProviderError(f"{operation} returned HTTP {response.status_code}")
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except httpx.HTTPError as exc:
        raise QuotaProviderError(f"{operation} failed: {type(exc).__name__}") from exc
    except ValueError as exc:
        raise QuotaProviderError(f"{operation} returned invalid JSON") from exc


async def fetch(raw: str) -> dict:
    credential = parse_credential(raw)
    try:
        return await _fetch(credential)
    except QuotaProviderError as exc:
        # Keep rotated refresh tokens even when a subsequent quota call fails.
        exc.credential = credential
        raise


async def _fetch(credential: dict) -> dict:
    async with httpx.AsyncClient(timeout=25) as client:
        refreshed = False

        async def refresh():
            nonlocal refreshed
            ident = credential["client_id"]
            result = await request_json(client, "POST", "https://oauth2.googleapis.com/token", "OAuth refresh", data={
                "grant_type": "refresh_token", "refresh_token": credential["refresh_token"],
                "client_id": ident, "client_secret": CLIENTS[ident]})
            if not result.get("access_token"):
                raise QuotaProviderError("OAuth refresh returned no access token")
            for key in ("access_token", "refresh_token", "id_token"):
                if result.get(key):
                    credential[key] = result[key]
            credential.pop("expiry_date", None)
            credential["expiry"] = _iso(datetime.now(timezone.utc) + timedelta(seconds=float(result.get("expires_in", 0))))
            refreshed = True

        if not credential.get("access_token") or expiry(credential) <= datetime.now(timezone.utc) + timedelta(seconds=60):
            await refresh()

        async def call(method, endpoint, operation, **kwargs):
            try:
                return await request_json(client, method, endpoint, operation, headers={
                    "Authorization": "Bearer " + credential["access_token"], "User-Agent": "antigravity"}, **kwargs)
            except QuotaProviderError as exc:
                if "HTTP 401" not in str(exc) or refreshed:
                    raise
                await refresh()
                return await request_json(client, method, endpoint, operation, headers={
                    "Authorization": "Bearer " + credential["access_token"], "User-Agent": "antigravity"}, **kwargs)

        user = await call("GET", "https://www.googleapis.com/oauth2/v1/userinfo", "Google identity")
        if not user.get("email") or not (user.get("verified_email") or user.get("email_verified")):
            raise QuotaProviderError("Google returned no verified account identity")
        info = await call("POST", API + "loadCodeAssist", "loadCodeAssist", json={"metadata": {"ideType": "ANTIGRAVITY"}})
        project = info.get("cloudaicompanionProject")
        if not isinstance(project, str) or not project:
            raise QuotaProviderError("Google returned no Code Assist project")
        summary = await call("POST", API + "retrieveUserQuotaSummary", "retrieveUserQuotaSummary", json={"project": project})
        groups = quota_groups(summary)

        # 尝试从已有的 group 中获取周重置基准时间
        ref_weekly_reset = None
        for g in groups:
            for b in g.get("buckets", []):
                if b.get("window") == "weekly":
                    ref_weekly_reset = b.get("reset_at")
                    break
            if ref_weekly_reset:
                break

        gemini_group_obj = next((g for g in groups if g["id"] == "gemini"), None)
        has_gemini = gemini_group_obj is not None
        has_gemini_weekly = gemini_group_obj is not None and any(b.get("window") == "weekly" for b in gemini_group_obj.get("buckets", []))

        if not has_gemini or not has_gemini_weekly:
            try:
                models = await call("POST", API + "fetchAvailableModels", "fetchAvailableModels", json={"project": project})
                model_groups = gemini_groups_from_models(models, reference_weekly_reset=ref_weekly_reset)
                if not has_gemini:
                    for mg in reversed(model_groups):
                        if not any(g["id"] == mg["id"] for g in groups):
                            groups.insert(0, mg)
                else:
                    fallback_gemini = next((mg for mg in model_groups if mg["id"] == "gemini"), None)
                    if fallback_gemini:
                        existing_windows = {b["window"] for b in gemini_group_obj.get("buckets", [])}
                        for b in fallback_gemini.get("buckets", []):
                            if b["window"] not in existing_windows:
                                gemini_group_obj["buckets"].append(b)
                        gemini_group_obj["buckets"].sort(key=lambda b: 0 if b["window"] == "5h" else 1)
                    fallback_g4 = next((mg for mg in model_groups if mg["id"] == "gemini_4"), None)
                    if fallback_g4 and not any(g["id"] == "gemini_4" for g in groups):
                        idx = next((i for i, g in enumerate(groups) if g["id"] == "gemini"), -1)
                        groups.insert(idx + 1, fallback_g4)
            except QuotaProviderError:
                if not groups:
                    raise
        if not groups:
            raise QuotaProviderError("Google returned no quota groups")
        tier = info.get("paidTier") or info.get("currentTier") or {}
        plan = TIERS.get(tier.get("id")) or tier.get("name") or tier.get("id")
        now = _iso(datetime.now(timezone.utc))
        account = {"email": user["email"], "name": user.get("name") or user["email"],
            "tier": {"id": tier.get("id"), "name": plan}, "groups": groups, "observed_at": now,
            "min_remaining_fraction": min(b["remaining_fraction"] for g in groups for b in g["buckets"])}
        return {"credential": credential, "identity": {"email": account["email"], "name": account["name"], "plan": plan},
            "quota": {"source": "antigravity", "observed_at": now, "accounts": [account]}}
