"""Local account store for imported quota credentials.

Credentials live in ``data/quota_accounts.json`` with mode 0600. They are
deliberately kept outside SQLite so the fleet database and its export never
contain a token. The API only ever returns the public account view.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from agent_fleet import models
from agent_fleet.services import quota_providers
from agent_fleet.settings import get_settings

STORE_VERSION = 1
STORE_NAME = "quota_accounts.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def store_path() -> Path:
    return get_settings().db_path.parent / STORE_NAME


def _read() -> dict:
    path = store_path()
    if not path.exists():
        return {"version": STORE_VERSION, "accounts": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"quota account store is unreadable: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("accounts"), list):
        raise ValueError("quota account store has an unexpected shape")
    return data


def _write(data: dict) -> None:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".quota-accounts-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def public_account(account: dict) -> dict:
    quota = account.get("quota")
    summary = None
    if isinstance(quota, dict):
        quota_accounts = quota.get("accounts")
        if isinstance(quota_accounts, list):
            fractions = [
                a.get("min_remaining_fraction")
                for a in quota_accounts
                if isinstance(a, dict)
                and isinstance(a.get("min_remaining_fraction"), (int, float))
            ]
            summary = {
                "source": quota.get("source"),
                "account_count": len(quota_accounts),
                "observed_at": quota.get("observed_at"),
                "min_remaining_fraction": min(fractions) if fractions else None,
            }
    return {
        "id": account.get("id"),
        "provider": account.get("provider"),
        "agent": quota_providers.PROVIDERS.get(account.get("provider"), {}).get("agent", account.get("provider")),
        "provider_name": quota_providers.provider_name(account.get("provider", "")),
        "label": account.get("label"),
        "email": account.get("email"),
        "name": account.get("name"),
        "plan": account.get("plan"),
        "status": account.get("status", "unknown"),
        "last_error": account.get("last_error"),
        "created_at": account.get("created_at"),
        "updated_at": account.get("updated_at"),
        "last_refreshed_at": account.get("last_refreshed_at"),
        "quota": quota,
        "quota_summary": summary,
    }


def list_accounts() -> list[dict]:
    return [public_account(a) for a in _read()["accounts"]]


def get_account(account_id: str) -> dict | None:
    return next(
        (a for a in _read()["accounts"] if a.get("id") == account_id), None
    )


def _apply_result(account: dict, result: dict) -> None:
    identity = result.get("identity") or {}
    account["credential"] = result.get("credential") or account.get("credential")
    account["email"] = identity.get("email") or account.get("email")
    account["name"] = identity.get("name") or account.get("name")
    account["plan"] = identity.get("plan")
    account["quota"] = result.get("quota")
    account["status"] = "ok"
    account["last_error"] = None
    account["last_refreshed_at"] = _now()
    account["updated_at"] = account["last_refreshed_at"]


async def add_account(provider: str, label: str | None, raw_credential: str) -> dict:
    if provider not in quota_providers.PROVIDERS:
        raise ValueError(f"unsupported provider: {provider}")
    result = await quota_providers.fetch_account(provider, raw_credential)
    data = _read()
    if provider == "antigravity":
        existing = next((a for a in data["accounts"] if a.get("provider") == provider
                         and a.get("email") == result["identity"]["email"]), None)
        if existing:
            _apply_result(existing, result)
            _write(data)
            return public_account(existing)
    account = {
        "id": models.new_id(),
        "provider": provider,
        "label": (label or "").strip()[:120] or None,
        "status": "ok",
        "created_at": _now(),
        "updated_at": _now(),
    }
    _apply_result(account, result)
    data["accounts"].append(account)
    _write(data)
    return public_account(account)


async def refresh_account(account_id: str) -> dict:
    data = _read()
    account = next((a for a in data["accounts"] if a.get("id") == account_id), None)
    if account is None:
        raise KeyError(account_id)
    credential = account.get("credential")
    if not isinstance(credential, dict):
        raise ValueError("stored credential is missing")
    raw = json.dumps(credential)
    try:
        result = await quota_providers.fetch_account(account.get("provider", ""), raw)
    except Exception as exc:
        if isinstance(getattr(exc, "credential", None), dict):
            account["credential"] = exc.credential
        account["status"] = "error"
        account["last_error"] = str(exc)[:300]
        account["updated_at"] = _now()
        _write(data)
        raise
    _apply_result(account, result)
    _write(data)
    return public_account(account)


def delete_account(account_id: str) -> bool:
    data = _read()
    before = len(data["accounts"])
    data["accounts"] = [a for a in data["accounts"] if a.get("id") != account_id]
    if len(data["accounts"]) == before:
        return False
    _write(data)
    return True
