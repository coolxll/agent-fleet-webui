"""Read-only Codex quota links. Magpie alone owns authentication and renewal.

Only an access token and account identity cross into the usage provider. No
source credential is persisted, and no magpie process or refresh endpoint is
invoked. One backend worker owns the collector and in-flight query registry.
"""
from __future__ import annotations

import asyncio
import json
import logging
import weakref
from datetime import datetime, timedelta, timezone

from agent_fleet import models
from agent_fleet.services import quota_providers, quota_store
from agent_fleet.settings import get_settings

log = logging.getLogger(__name__)
DEFAULT_COLLECTION = {"enabled": True, "interval_seconds": 600}
_inflight: dict[str, asyncio.Task] = {}
_query_limits: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


class SourceError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _time(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc) if value else None
    except (ValueError, TypeError, AttributeError):
        return None


def _logins() -> list[dict]:
    path = get_settings().magpie_config_dir / "logins.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SourceError("magpie login store was not found") from None
    except (OSError, ValueError):
        raise SourceError("magpie login store could not be read") from None
    if not isinstance(data, list):
        raise SourceError("magpie login store has an unsupported format")
    entries = []
    for row in data:
        if not isinstance(row, dict) or row.get("agent") != "codex":
            continue
        auth = row.get("auth")
        tokens = auth.get("tokens") if isinstance(auth, dict) else None
        if not isinstance(tokens, dict) or not isinstance(row.get("id"), str):
            continue
        # Deliberately do not forward id_token or refresh_token to providers.
        credential = {k: tokens[k] for k in ("access_token", "account_id") if isinstance(tokens.get(k), str)}
        identity = quota_providers._codex_identity(credential)
        if not identity.get("account_id"):
            continue
        label = row.get("user") if isinstance(row.get("user"), str) else row["id"]
        email = identity.get("email")
        if not email and "@" in label:
            email = label.split(" · ")[0]
        if email:
            credential["email"] = email
        entries.append({
            "id": row["id"], "email": email, "label": label,
            "plan": row.get("plan") if isinstance(row.get("plan"), str) else None,
            "account_id": identity["account_id"], "credential": credential,
        })
    return entries


def _source(source_id: str, account_id: str | None = None, email: str | None = None) -> dict:
    matches = [a for a in _logins() if a["id"] == source_id]
    if len(matches) != 1:
        raise SourceError("Linked account is missing or ambiguous in magpie")
    entry = matches[0]
    if account_id and entry["account_id"] != account_id:
        raise SourceError("The linked magpie account identity has changed; link it again")
    if email and entry["email"] != email:
        raise SourceError("The linked magpie user has changed; remove the link and link it again")
    return entry


def source_accounts() -> dict:
    try:
        entries = _logins()
    except SourceError as exc:
        return {"available": False, "error": str(exc), "accounts": []}
    accounts = quota_store.list_accounts()
    public = []
    for a in entries:
        linked = next((r for r in accounts if r["credential_source"] == "magpie" and r["source_account_id"] == a["id"]), None)
        public.append({k: a[k] for k in ("id", "email", "label", "plan")} | {
            "linked_account_id": linked["id"] if linked else None,
        })
    return {"available": True, "error": None, "accounts": public}


def collection_settings() -> dict:
    with quota_store._STORE_LOCK:
        data = quota_store._read()
        return DEFAULT_COLLECTION | data.get("quota_collection", {})


def update_collection(enabled: bool, interval_seconds: int) -> dict:
    settings = {"enabled": enabled, "interval_seconds": interval_seconds}
    with quota_store._STORE_LOCK:
        data = quota_store._read()
        data["quota_collection"] = settings
        for a in data["accounts"]:
            if a.get("credential_source") == "magpie":
                a["next_query_at"] = _iso((_time(a.get("last_checked_at")) or _now()) + timedelta(seconds=interval_seconds))
        quota_store._write(data)
    return settings


async def link_account(source_id: str) -> dict:
    entry = _source(source_id)
    with quota_store._STORE_LOCK:
        data = quota_store._read()
        matches = [a for a in data["accounts"] if a.get("provider") == "codex" and (
            a.get("source_account_id") == source_id
            # ChatGPT account_id identifies a workspace, shared by team members.
            # A workspace alone must never merge two different users.
            or ((a.get("account_id") or (a.get("credential") or {}).get("account_id")) == entry["account_id"]
                and entry["email"] and a.get("email") == entry["email"])
        )]
        if not matches:
            # Legacy imports sometimes have no stable ID. Only migrate a unique
            # email match with no conflicting account ID (teams can share email).
            matches = [a for a in data["accounts"] if a.get("provider") == "codex"
                       and entry["email"] and a.get("email") == entry["email"]
                       and not (a.get("account_id") or (a.get("credential") or {}).get("account_id"))]
        if len(matches) > 1:
            raise SourceError("Multiple quota accounts match; remove duplicate imports before linking")
        account = matches[0] if matches else {
            "id": models.new_id(), "provider": "codex", "created_at": _iso(_now()),
        }
        if not matches:
            data["accounts"].append(account)
        if account.get("source_account_id") not in (None, source_id):
            raise SourceError("This quota account is already linked to another magpie login")
        account.update({"credential_source": "magpie", "source_account_id": source_id,
                        "account_id": entry["account_id"], "email": entry["email"],
                        "plan": entry["plan"], "status": "unknown", "updated_at": _iso(_now())})
        account.pop("credential", None)
        quota_store._write(data)
        ident = account["id"]
    try:
        return await query_account(ident)
    except quota_providers.QuotaProviderError:
        # Keep the association and old snapshot even if this token is expired.
        return quota_store.public_account(quota_store.get_account(ident))


async def query_account(account_id: str) -> dict:
    task = _inflight.get(account_id)
    if task is None or task.done():
        task = asyncio.create_task(_limited_query(account_id))
        _inflight[account_id] = task
        def finished(done):
            if _inflight.get(account_id) is done:
                _inflight.pop(account_id, None)
            if not done.cancelled():
                done.exception()  # consume failures even if every waiter cancelled
        task.add_done_callback(finished)
    return await asyncio.shield(task)


async def _limited_query(account_id: str) -> dict:
    loop = asyncio.get_running_loop()
    if loop not in _query_limits:
        _query_limits[loop] = asyncio.Semaphore(3)
    async with _query_limits[loop]:
        return await _query_account(account_id)


async def _query_account(account_id: str) -> dict:
    account = quota_store.get_account(account_id)
    if account is None:
        raise KeyError(account_id)
    if account.get("credential_source") != "magpie":
        raise ValueError("Account is not linked to magpie")
    # Manual clicks and the timer share both in-flight work and a short cooldown.
    checked = _time(account.get("last_checked_at"))
    if checked and (_now() - checked).total_seconds() < 30:
        return quota_store.public_account(account)
    source_id = account["source_account_id"]
    error_code, message, result = None, None, None
    try:
        entry = _source(source_id, account.get("account_id"), account.get("email"))
        credential = entry["credential"]
        if not credential.get("access_token"):
            raise quota_providers.QuotaAuthError("No current access token")
        try:
            result = await quota_providers.fetch_account("codex", json.dumps(credential))
        except quota_providers.QuotaAuthError:
            # Magpie may have replaced its access token during the request. Read
            # it once more; retry only if it changed. Never renew it ourselves.
            latest = _source(source_id, account.get("account_id"), account.get("email"))["credential"]
            if latest == credential or not latest.get("access_token"):
                raise
            result = await quota_providers.fetch_account("codex", json.dumps(latest))
    except SourceError as exc:
        error_code, message = "source_unavailable", str(exc)
    except quota_providers.QuotaAuthError:
        error_code, message = "auth_required", "Update this account's login in magpie, then query its quota again."
    except Exception:
        # Never persist vendor response text or exception strings containing
        # source credentials. The last successful snapshot stays available.
        error_code, message = "query_failed", "Quota query failed; it will be retried with backoff."
    settings = collection_settings()
    with quota_store._STORE_LOCK:
        data = quota_store._read()
        current = next((a for a in data["accounts"] if a["id"] == account_id), None)
        if current is None:
            raise KeyError(account_id)
        if current.get("credential_source") != "magpie" or current.get("source_account_id") != source_id:
            return quota_store.public_account(current)
        if result is not None and error_code is None:
            quota_store._apply_result(current, result)
            current.pop("credential", None)
            current["failure_count"] = 0
        else:
            current.update({"status": "error", "last_error": message,
                            "updated_at": _iso(_now()), "failure_count": current.get("failure_count", 0) + 1})
        current["error_code"] = error_code
        current["last_checked_at"] = _iso(_now())
        delay = min(settings["interval_seconds"] * 2 ** min(max(current["failure_count"] - 1, 0), 6), max(3600, settings["interval_seconds"]))
        current["next_query_at"] = _iso(_now() + timedelta(seconds=delay))
        quota_store._write(data)
        public = quota_store.public_account(current)
    if error_code:
        raise quota_providers.QuotaProviderError(message)
    return public


class Collector:
    def __init__(self):
        self.task: asyncio.Task | None = None

    def start(self):
        self.task = asyncio.create_task(self._loop())

    async def stop(self):
        tasks = [t for t in [self.task, *_inflight.values()] if t is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def run_once(self):
        if not collection_settings()["enabled"]:
            return
        due = [a for a in quota_store.list_accounts()
               if a["credential_source"] == "magpie"
               and (_time(a.get("next_query_at")) or _now()) <= _now()]
        semaphore = asyncio.Semaphore(3)
        async def query(a):
            async with semaphore:
                try:
                    await query_account(a["id"])
                except (KeyError, ValueError, quota_providers.QuotaProviderError):
                    pass  # persisted account status supplies the user-facing error
        await asyncio.gather(*(query(a) for a in due))

    async def _loop(self):
        while True:
            try:
                await self.run_once()
            except Exception:
                log.warning("Quota collection failed; will retry on the next tick")
            await asyncio.sleep(15)
