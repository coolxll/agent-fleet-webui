import asyncio
import json

import httpx

from agent_fleet.services import quota_store


def _install_workbuddy_api(monkeypatch):
    requests = []

    def handler(request: httpx.Request):
        requests.append((request.url.host, request.headers.get("Authorization"), request.headers.get("X-User-Id")))
        return httpx.Response(200, json={"code": 0, "Response": {"Data": {"Accounts": []}}})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    return requests


def _use_temp_store(tmp_path, monkeypatch):
    path = tmp_path / "quota_accounts.json"
    monkeypatch.setattr(quota_store, "store_path", lambda: path)
    return path


def test_workbuddy_token_accounts_are_distinct_idempotent_and_private(tmp_path, monkeypatch):
    path = _use_temp_store(tmp_path, monkeypatch)
    requests = _install_workbuddy_api(monkeypatch)

    async def scenario():
        first = await quota_store.add_account("workbuddy", None, "plain-token-one")
        second = await quota_store.add_account("workbuddy", None, "Bearer plain-token-two")
        repeated = await quota_store.add_account("workbuddy", None, "plain-token-one")
        return first, second, repeated

    first, second, repeated = asyncio.run(scenario())
    stored = json.loads(path.read_text())["accounts"]
    assert len(stored) == 2
    assert first["id"] == repeated["id"]
    assert first["id"] != second["id"]
    assert {item["credential"]["access_token"] for item in stored} == {
        "plain-token-one", "plain-token-two"
    }
    assert first["email"] is None and second["email"] is None
    public_json = json.dumps(quota_store.list_accounts())
    assert "account_id" not in public_json
    assert "plain-token" not in public_json
    assert "token:" not in public_json
    assert all(request[1] in {"Bearer plain-token-one", "Bearer plain-token-two"} for request in requests)


def test_workbuddy_same_uid_realms_refresh_independently(tmp_path, monkeypatch):
    path = _use_temp_store(tmp_path, monkeypatch)
    requests = _install_workbuddy_api(monkeypatch)
    cn_credential = json.dumps({
        "accessToken": "cn-secret-token",
        "domain": "www.codebuddy.cn",
        "uid": "same-user",
        "nickname": "CN account",
    })
    global_credential = json.dumps({
        "accessToken": "global-secret-token",
        "domain": "www.workbuddy.ai",
        "realm": "global",
        "uid": "same-user",
        "nickname": "Global account",
    })

    async def scenario():
        cn = await quota_store.add_account("workbuddy", None, cn_credential)
        global_account = await quota_store.add_account("workbuddy", None, global_credential)
        await quota_store.refresh_account(cn["id"])
        await quota_store.refresh_account(global_account["id"])
        return cn, global_account

    cn, global_account = asyncio.run(scenario())
    accounts = json.loads(path.read_text())["accounts"]
    assert len(accounts) == 2
    assert cn["id"] != global_account["id"]
    assert {account["account_id"] for account in accounts} == {
        "workbuddy:cn:uid:same-user", "workbuddy:global:uid:same-user"
    }
    assert all(account["credential"]["uid"] == "same-user" for account in accounts)
    assert {account["credential"]["realm"] for account in accounts} == {"cn", "global"}
    assert {account["credential"]["access_token"] for account in accounts} == {
        "cn-secret-token", "global-secret-token"
    }

    # The real provider must route both imports and both refreshes through the
    # corresponding realm, preserving the source uid in X-User-Id.
    assert [host for host, _, _ in requests] == [
        "www.codebuddy.cn", "www.workbuddy.ai", "www.codebuddy.cn", "www.workbuddy.ai"
    ]
    assert all(uid == "same-user" for _, _, uid in requests)
    public_json = json.dumps(quota_store.list_accounts())
    assert "account_id" not in public_json
    assert "workbuddy:cn:uid" not in public_json
    assert "workbuddy:global:uid" not in public_json
    assert "cn-secret-token" not in public_json
    assert "global-secret-token" not in public_json
