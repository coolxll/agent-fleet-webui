import asyncio
import json

import pytest

from agent_fleet.services import quota_store


def _account(account_id: str, token: str = "old") -> dict:
    return {
        "id": account_id,
        "provider": "test",
        "credential": {"access_token": token},
        "status": "ok",
        "created_at": "then",
    }


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "quota_accounts.json"
    monkeypatch.setattr(quota_store, "store_path", lambda: path)
    quota_store._write({"version": 1, "accounts": [_account("a")]})
    return path


def _result(token: str, email: str = "a@example.test"):
    return {
        "credential": {"access_token": token},
        "identity": {"email": email},
        "quota": {"source": "test", "accounts": []},
    }


def test_refresh_preserves_account_added_while_fetching(store, monkeypatch):
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()

        async def fetch(provider, raw):
            started.set()
            await release.wait()
            return _result("rotated")

        monkeypatch.setattr(quota_store.quota_providers, "fetch_account", fetch)
        task = asyncio.create_task(quota_store.refresh_account("a"))
        await started.wait()
        with quota_store._STORE_LOCK:
            data = quota_store._read()
            data["accounts"].append(_account("new"))
            quota_store._write(data)
        release.set()
        await task

    asyncio.run(scenario())
    assert {a["id"] for a in quota_store._read()["accounts"]} == {"a", "new"}


def test_refresh_does_not_resurrect_deleted_account(store, monkeypatch):
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()

        async def fetch(provider, raw):
            started.set()
            await release.wait()
            return _result("rotated")

        monkeypatch.setattr(quota_store.quota_providers, "fetch_account", fetch)
        task = asyncio.create_task(quota_store.refresh_account("a"))
        await started.wait()
        assert quota_store.delete_account("a")
        release.set()
        with pytest.raises(KeyError):
            await task

    asyncio.run(scenario())
    assert quota_store._read()["accounts"] == []


def test_different_accounts_refresh_concurrently_and_merge(store, monkeypatch):
    async def scenario():
        both_started, release = asyncio.Event(), asyncio.Event()
        count = 0

        async def fetch(provider, raw):
            nonlocal count
            count += 1
            if count == 2:
                both_started.set()
            await release.wait()
            old = json.loads(raw)["access_token"]
            return _result(f"{old}-new", f"{old}@example.test")

        with quota_store._STORE_LOCK:
            quota_store._write({"version": 1, "accounts": [_account("a"), _account("b", "other")]})
        monkeypatch.setattr(quota_store.quota_providers, "fetch_account", fetch)
        tasks = [asyncio.create_task(quota_store.refresh_account(i)) for i in ("a", "b")]
        await asyncio.wait_for(both_started.wait(), timeout=1)
        release.set()
        await asyncio.gather(*tasks)

    asyncio.run(scenario())
    accounts = {a["id"]: a for a in quota_store._read()["accounts"]}
    assert accounts["a"]["credential"]["access_token"] == "old-new"
    assert accounts["b"]["credential"]["access_token"] == "other-new"


def test_error_refresh_preserves_concurrent_account_change(store, monkeypatch):
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()

        async def fetch(provider, raw):
            started.set()
            await release.wait()
            error = RuntimeError("provider failed")
            error.credential = {"access_token": "stale-error-token"}
            raise error

        monkeypatch.setattr(quota_store.quota_providers, "fetch_account", fetch)
        task = asyncio.create_task(quota_store.refresh_account("a"))
        await started.wait()
        with quota_store._STORE_LOCK:
            data = quota_store._read()
            data["accounts"][0]["credential"] = {"access_token": "newer-token"}
            data["accounts"].append(_account("added"))
            quota_store._write(data)
        release.set()
        with pytest.raises(RuntimeError):
            await task

    asyncio.run(scenario())
    accounts = {a["id"]: a for a in quota_store._read()["accounts"]}
    assert accounts["a"]["credential"]["access_token"] == "newer-token"
    assert accounts["a"]["status"] == "ok"
    assert "added" in accounts


def test_concurrent_same_account_refresh_keeps_newer_rotated_credential(store, monkeypatch):
    async def scenario():
        started = [asyncio.Event(), asyncio.Event()]
        releases = [asyncio.Event(), asyncio.Event()]
        calls = 0

        async def fetch(provider, raw):
            nonlocal calls
            index = calls
            calls += 1
            started[index].set()
            await releases[index].wait()
            return _result("first-rotated" if index == 0 else "second-stale")

        monkeypatch.setattr(quota_store.quota_providers, "fetch_account", fetch)
        first = asyncio.create_task(quota_store.refresh_account("a"))
        await started[0].wait()
        second = asyncio.create_task(quota_store.refresh_account("a"))
        await started[1].wait()
        releases[0].set()
        await first
        releases[1].set()
        await second

    asyncio.run(scenario())
    account = quota_store._read()["accounts"][0]
    assert account["credential"]["access_token"] == "first-rotated"


def test_stable_identity_deduplicates_by_account_id_and_upgrades_legacy(store, monkeypatch):
    async def scenario():
        monkeypatch.setitem(quota_store.quota_providers.PROVIDERS, "test", {"agent": "test"})
        with quota_store._STORE_LOCK:
            quota_store._write({
                "version": 1,
                "accounts": [{
                    **_account("legacy"),
                    "credential": {"access_token": "token", "uid": "same-uid", "realm": "cn"},
                }],
            })

        async def fetch(provider, raw):
            return {
                "credential": {"access_token": "token", "uid": "same-uid", "realm": "cn"},
                "identity": {
                    "account_id": "workbuddy:cn:uid:same-uid",
                    "email": "same-uid@example.test",
                },
                "quota": {"source": "test", "accounts": []},
            }

        monkeypatch.setattr(quota_store.quota_providers, "fetch_account", fetch)
        await quota_store.add_account("test", None, "input")
        await quota_store.add_account("test", None, "input")

    asyncio.run(scenario())
    accounts = quota_store._read()["accounts"]
    assert len(accounts) == 1
    assert accounts[0]["id"] == "legacy"
    assert accounts[0]["account_id"] == "workbuddy:cn:uid:same-uid"
