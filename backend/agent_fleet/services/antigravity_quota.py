"""Antigravity OAuth refresh and online account quotas."""

import base64
import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from agent_fleet.services.quota_providers import QuotaProviderError, _decode_jwt_payload, _iso
from agent_fleet.settings import get_settings

# Public desktop OAuth client IDs used by Antigravity. The matching client
# secrets are not source code: load them from the local config file or env.
DEFAULT_CLIENT = "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
CLIENT_IDS = {
    DEFAULT_CLIENT,
    "884354919052-36trc1jjb3tguiac32ov6cod268c5blh.apps.googleusercontent.com",
}
CLIENT_SECRETS_ENV = "AGENT_FLEET_ANTIGRAVITY_CLIENTS"
CLIENT_SECRETS_FILE = "antigravity_clients.json"


def clients_file() -> Path:
    return get_settings().db_path.parent / CLIENT_SECRETS_FILE


def _read_client_secrets() -> dict[str, str]:
    raw = os.environ.get(CLIENT_SECRETS_ENV, "").strip()
    source = CLIENT_SECRETS_ENV
    if raw:
        try:
            data = json.loads(raw)
        except Exception as exc:
            raise QuotaProviderError(f"{CLIENT_SECRETS_ENV} is not valid JSON: {exc}") from exc
    else:
        path = clients_file()
        if not path.is_file():
            return {}
        source = str(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise QuotaProviderError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise QuotaProviderError(
            f"{source} must contain a JSON object mapping client_id to client_secret"
        )
    return {str(k): v for k, v in data.items() if isinstance(v, str) and v}


def client_secrets() -> dict[str, str]:
    return {cid: secret for cid, secret in _read_client_secrets().items() if cid in CLIENT_IDS}


def _require_client_secret(client_id: str) -> str:
    secret = client_secrets().get(client_id)
    if secret:
        return secret
    raise QuotaProviderError(
        f"no Antigravity client secret configured for {client_id}; "
        f"set {CLIENT_SECRETS_ENV} or create {clients_file()}"
    )


API = "https://daily-cloudcode-pa.googleapis.com/v1internal:"
PROD_API = "https://cloudcode-pa.googleapis.com/v1internal:"
USER_AGENT = "antigravity/2.24.0 (aidev_client; os_type=darwin; arch=arm64)"
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
            audience = next((a for a in audience if a in CLIENT_IDS), None)
        credential["client_id"] = credential.get("client_id") or (audience if audience in CLIENT_IDS else DEFAULT_CLIENT)
        if credential["client_id"] not in CLIENT_IDS:
            raise QuotaProviderError("unsupported Antigravity OAuth client")
        _require_client_secret(credential["client_id"])
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
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return max(0.0, min(1.0, number)) if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def extract_raw_groups(summary: dict) -> list[dict] | None:
    if not isinstance(summary, dict):
        return None
    if isinstance(summary.get("groups"), list):
        return summary["groups"]
    for key in ("response", "summary", "command", "data"):
        nested = summary.get(key)
        if isinstance(nested, dict):
            groups = extract_raw_groups(nested)
            if groups is not None:
                return groups
    return None


SUMMARY_BUCKETS = {
    "gemini-weekly": ("gemini", "weekly"),
    "gemini-5h": ("gemini", "5h"),
    "3p-weekly": ("third_party", "weekly"),
    "3p-5h": ("third_party", "5h"),
}
ARGON_BUCKET = "gemini-argon-wtu-weekly"


def resolve_group_id_and_name(display_name: str, desc: str, buckets: list[dict]) -> tuple[str, str]:
    name = (display_name or "").strip()
    if name.lower() == "gemini 4 argon":
        return "gemini_4", name
    ids = {b.get("bucketId") or b.get("id") for b in buckets if isinstance(b, dict)}
    if ids & {"gemini-weekly", "gemini-5h"}:
        return "gemini", name or "Gemini models"
    if ids & {"3p-weekly", "3p-5h"}:
        return "third_party", name or "Claude and GPT models"
    return name.lower().replace(" ", "_") or "quota", name or "Quota"


def _reset_time(value) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value if dt.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def resolve_bucket(bucket: dict) -> dict | None:
    if not isinstance(bucket, dict):
        return None
    disabled = bucket.get("disabled", False)
    if not isinstance(disabled, bool) or disabled:
        return None
    ident = bucket.get("bucketId") or bucket.get("id") or ""
    if not isinstance(ident, str):
        return None
    ident = ident.strip()
    remaining = fraction(bucket.get("remainingFraction"))
    if remaining is None and isinstance(bucket.get("remaining"), dict):
        remaining = fraction(bucket["remaining"].get("remainingFraction"))
    if remaining is None:
        return None
    known = SUMMARY_BUCKETS.get(ident)
    window = bucket.get("window") or (known[1] if known else None)
    if ident == ARGON_BUCKET:
        if window is not None and window != "weekly":
            return None
        window = "weekly"
    if window not in ("weekly", "5h") or (known and window != known[1]):
        return None
    return {
        "id": ident or f"bucket-{window}",
        "name": bucket.get("displayName") or bucket.get("name") or ("Weekly" if window == "weekly" else "5-Hour"),
        "window": window,
        "remaining_fraction": remaining,
        "reset_at": _reset_time(bucket.get("resetTime") or bucket.get("reset_time")),
    }


def quota_groups(summary: dict) -> list[dict]:
    groups = []
    for group in extract_raw_groups(summary) or []:
        if not isinstance(group, dict) or not isinstance(group.get("buckets"), list):
            continue
        raw_buckets = [b for b in group["buckets"] if isinstance(b, dict)]
        gid, name = resolve_group_id_and_name(group.get("displayName") or group.get("name") or "", "", raw_buckets)
        if gid == "gemini_4":
            # Separate Argon quota is evidenced by its own summary group, not
            # by a model catalogue entry. Never borrow shared Gemini buckets.
            raw_buckets = [b for b in raw_buckets if (b.get("window") == "weekly" or
                           (b.get("bucketId") or b.get("id")) == ARGON_BUCKET)]
            if len(raw_buckets) != 1:
                continue
            ident = raw_buckets[0].get("bucketId") or raw_buckets[0].get("id")
            if not ident or ident in SUMMARY_BUCKETS:
                continue
        else:
            # Only known shared bucket IDs establish a shared pool and period.
            raw_buckets = [b for b in raw_buckets if SUMMARY_BUCKETS.get(b.get("bucketId") or b.get("id"), (None,))[0] == gid]
        buckets = {}
        for raw in raw_buckets:
            item = resolve_bucket(raw)
            if item and (item["window"] not in buckets or item["remaining_fraction"] < buckets[item["window"]]["remaining_fraction"]):
                buckets[item["window"]] = item
        if buckets:
            groups.append({"id": gid, "name": name, "buckets": sorted(buckets.values(), key=lambda b: b["window"] != "weekly")})
    order = {"gemini": 0, "gemini_4": 1, "third_party": 2}
    return sorted(groups, key=lambda g: (order.get(g["id"], 3), g["name"]))


def merge_quota_summaries(summaries: list[dict]) -> list[dict]:
    groups = {}
    blocked = set()
    for summary in summaries:
        for raw_group in extract_raw_groups(summary) or []:
            if not isinstance(raw_group, dict) or not isinstance(raw_group.get("buckets"), list):
                continue
            for bucket in raw_group["buckets"]:
                if isinstance(bucket, dict) and bucket.get("disabled") is True:
                    ident = bucket.get("bucketId") or bucket.get("id")
                    if ident in SUMMARY_BUCKETS:
                        blocked.add(SUMMARY_BUCKETS[ident])
                    elif (raw_group.get("displayName") or raw_group.get("name") or "").lower() == "gemini 4 argon":
                        if bucket.get("window") == "weekly" or ident == ARGON_BUCKET:
                            blocked.add(("gemini_4", "weekly"))
        for group in quota_groups(summary):
            existing = groups.setdefault(group["id"], {**group, "buckets": []})
            for bucket in group["buckets"]:
                key = (group["id"], bucket["window"])
                if key not in blocked and not any(b["window"] == bucket["window"] for b in existing["buckets"]):
                    existing["buckets"].append(bucket)
    return sorted((g for g in groups.values() if g["buckets"]), key=lambda g: {"gemini": 0, "gemini_4": 1, "third_party": 2}.get(g["id"], 3))


def gemini_groups_from_models(models: dict) -> list[dict]:
    """Legacy fallback: model quotas do not identify a weekly/5h window.

    The reset can represent the currently limiting window. In particular, a
    weekly limit remains weekly during its last six hours. Do not infer its
    period, or a separate Argon pool, from this endpoint.
    """
    raw_models = models.get("models", {})
    if not isinstance(raw_models, dict):
        return []
    buckets = []
    for ident, model in raw_models.items():
        if not isinstance(model, dict) or not isinstance(ident, str):
            continue
        label = model.get("displayName") or ident
        combined = f"{ident} {label}".lower()
        if "gemini" not in combined or "argon" in combined or re.search(r"gemini[- ]4(?:[- .]|$)", combined):
            continue
        info = model.get("quotaInfo")
        if not isinstance(info, dict):
            continue
        remaining = fraction(info.get("remainingFraction"))
        if remaining is None:
            continue
        buckets.append({"id": ident, "name": label, "window": "model", "remaining_fraction": remaining,
                        "reset_at": _reset_time(info.get("resetTime"))})
    return [{"id": "gemini", "name": "Gemini models", "buckets": buckets}] if buckets else []


def gemini_group(models: dict) -> dict | None:
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
                "client_id": ident, "client_secret": _require_client_secret(ident)})
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
                    "Authorization": "Bearer " + credential["access_token"], "User-Agent": USER_AGENT}, **kwargs)
            except QuotaProviderError as exc:
                if "HTTP 401" in str(exc) and not refreshed:
                    await refresh()
                    return await request_json(client, method, endpoint, operation, headers={
                        "Authorization": "Bearer " + credential["access_token"], "User-Agent": USER_AGENT}, **kwargs)
                raise

        user = await call("GET", "https://www.googleapis.com/oauth2/v1/userinfo", "Google identity")
        if not user.get("email") or not (user.get("verified_email") or user.get("email_verified")):
            raise QuotaProviderError("Google returned no verified account identity")
        info = await call("POST", API + "loadCodeAssist", "loadCodeAssist", json={"metadata": {"ideType": "ANTIGRAVITY"}})
        project = info.get("cloudaicompanionProject")
        if not isinstance(project, str) or not project:
            raise QuotaProviderError("Google returned no Code Assist project")

        # Prefer the primary Antigravity host. A secondary host only fills
        # missing buckets; a smaller fraction does not prove a fresher reset.
        summaries = []
        for base in (API, PROD_API):
            try:
                res = await call("POST", base + "retrieveUserQuotaSummary", "retrieveUserQuotaSummary", json={"project": project})
                if extract_raw_groups(res) is not None:
                    summaries.append(res)
            except QuotaProviderError:
                continue
        groups = merge_quota_summaries(summaries)

        # An explicit summary is authoritative, including disabled/missing
        # windows. Legacy per-model quota must never overwrite those windows.
        if not summaries:
            try:
                models = await call("POST", API + "fetchAvailableModels", "fetchAvailableModels", json={"project": project})
                groups = gemini_groups_from_models(models)
            except QuotaProviderError:
                pass
        for group in groups:
            group["buckets"].sort(key=lambda b: b["window"] != "weekly")

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
