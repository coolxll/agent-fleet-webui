import asyncio
import json

import httpx
import pytest

from agent_fleet.services import workbuddy_quota as wb
from agent_fleet.services.quota_providers import QuotaProviderError, fetch_account


def test_parse_credential_plain():
    res = wb.parse_credential("my-token-123")
    assert res["access_token"] == "my-token-123"
    assert res["domain"] == ""
    assert res["realm"] == ""


def test_parse_credential_bearer():
    res = wb.parse_credential("Bearer my-token-abc")
    assert res["access_token"] == "my-token-abc"


def test_parse_credential_flat_json():
    data = {
        "accessToken": "flat-token",
        "domain": "www.codebuddy.cn",
        "uid": "user-101",
        "nickname": "TestUser",
    }
    res = wb.parse_credential(json.dumps(data))
    assert res["access_token"] == "flat-token"
    assert res["domain"] == "www.codebuddy.cn"
    assert res["uid"] == "user-101"
    assert res["nickname"] == "TestUser"


def test_parse_credential_nested_json():
    data = {
        "auth": {
            "accessToken": "nested-token",
            "refreshToken": "refresh-123",
            "domain": "www.workbuddy.ai",
            "realm": "global",
        },
        "account": {
            "uid": "global-user-1",
            "nickname": "GlobalTester",
        },
    }
    res = wb.parse_credential(json.dumps(data))
    assert res["access_token"] == "nested-token"
    assert res["refresh_token"] == "refresh-123"
    assert res["domain"] == "www.workbuddy.ai"
    assert res["realm"] == "global"
    assert res["uid"] == "global-user-1"
    assert res["nickname"] == "GlobalTester"


def test_parse_credential_empty(monkeypatch):
    monkeypatch.setattr(wb, "find_local_workbuddy_auth", lambda: None)
    with pytest.raises(QuotaProviderError, match="empty"):
        wb.parse_credential("   ")


def test_parse_credential_auto_detected(monkeypatch):
    dummy_auth = json.dumps({"auth": {"accessToken": "auto-found-token"}, "account": {"uid": "auto-uid"}})
    monkeypatch.setattr(wb, "find_local_workbuddy_auth", lambda: dummy_auth)
    res = wb.parse_credential("auto")
    assert res["access_token"] == "auto-found-token"
    assert res["uid"] == "auto-uid"


def test_codebuddy_provider_alias(monkeypatch):
    dummy_auth = json.dumps({"auth": {"accessToken": "alias-token"}})
    def handler(request: httpx.Request):
        assert request.headers.get("User-Agent") == "WorkBuddy/5.5.6"
        return httpx.Response(200, json={"code": 0, "Response": {"Data": {"Accounts": []}}})

    orig_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: orig_client(transport=httpx.MockTransport(handler), **kw),
    )
    res = asyncio.run(fetch_account("codebuddy", dummy_auth))
    assert res["quota"]["source"] == "workbuddy"


def test_fetch_workbuddy_cn_success(monkeypatch):
    def handler(request: httpx.Request):
        assert request.url.host == "www.codebuddy.cn"
        assert request.url.path in ("/billing/meter/get-user-resource", "/v2/billing/meter/get-user-resource")
        assert request.headers["Authorization"] == "Bearer cn-token"
        assert request.headers["X-User-Id"] == "cn-user-99"

        body = json.loads(request.content)
        assert body["ProductCode"] == "p_tcaca"

        return httpx.Response(
            200,
            json={
                "code": 0,
                "Response": {
                    "Data": {
                        "TotalDosage": 1000,
                        "Accounts": [
                            {
                                "PackageName": "每日签到赠送积分",
                                "CycleEndTime": "2027-10-21 00:00:00",
                                "CapacitySize": 100,
                                "CapacityRemain": 75,
                                "CapacityUsed": 25,
                                "CycleCapacitySize": 0,
                                "CycleCapacityRemain": 0,
                                "CycleCapacityUsed": 0,
                            },
                            {
                                "PackageName": "月度专业版套餐",
                                "CycleEndTime": "2027-11-01 00:00:00",
                                "CapacitySize": 0,
                                "CapacityRemain": 0,
                                "CapacityUsed": 0,
                                "CycleCapacitySize": 900,
                                "CycleCapacityRemain": 700,
                                "CycleCapacityUsed": 200,
                            },
                        ],
                    }
                },
            },
        )

    orig_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: orig_client(transport=httpx.MockTransport(handler), **kw),
    )

    cred = {
        "accessToken": "cn-token",
        "domain": "www.codebuddy.cn",
        "uid": "cn-user-99",
        "nickname": "BuddyCN",
    }
    result = asyncio.run(fetch_account("workbuddy", json.dumps(cred)))

    assert result["identity"]["name"] == "BuddyCN"
    assert result["identity"]["plan"] == "WorkBuddy CN"
    account = result["quota"]["accounts"][0]
    # Total remain = 75 + 700 = 775, total size = 1000
    assert account["min_remaining_fraction"] == 0.775
    group = account["groups"][0]
    bucket = group["buckets"][0]
    assert bucket["remaining"] == 775
    assert bucket["used"] == 225
    assert bucket["total"] == 1000
    assert len(group["packages"]) == 2
    assert len(group["daily_consumption"]) >= 1


def test_fetch_workbuddy_global_fallback(monkeypatch):
    calls = []

    def handler(request: httpx.Request):
        assert request.url.host == "www.workbuddy.ai"
        calls.append(request.url.path)
        if request.url.path == "/billing/meter/get-user-resource":
            return httpx.Response(404, text="Not Found")
        if request.url.path == "/v2/billing/meter/get-user-resource":
            return httpx.Response(
                200,
                json={
                    "Code": 0,
                    "Response": {
                        "Data": {
                            "TotalDosage": 500,
                            "Accounts": [
                                {
                                    "PackageName": "Global Welcome Pack",
                                    "CycleEndTime": "2027-12-31 23:59:59",
                                    "CapacitySize": 500,
                                    "CapacityRemain": 400,
                                    "CapacityUsed": 100,
                                }
                            ],
                        }
                    },
                },
            )
        return httpx.Response(500)

    orig_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: orig_client(transport=httpx.MockTransport(handler), **kw),
    )

    cred = {
        "auth": {
            "accessToken": "global-token",
            "domain": "www.workbuddy.ai",
            "realm": "global",
        },
        "account": {"uid": "glob-1", "nickname": "GlobalTester"},
    }
    result = asyncio.run(wb.fetch(json.dumps(cred)))

    assert calls == [
        "/billing/meter/get-user-resource",
        "/v2/billing/meter/get-user-resource",
    ]
    assert result["identity"]["plan"] == "WorkBuddy Global"
    account = result["quota"]["accounts"][0]
    assert account["min_remaining_fraction"] == 0.8
    assert account["groups"][0]["buckets"][0]["remaining"] == 400


def test_workbuddy_identity_is_stable_and_realm_scoped(monkeypatch):
    def handler(request: httpx.Request):
        return httpx.Response(200, json={"code": 0, "Response": {"Data": {"Accounts": []}}})

    orig_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: orig_client(transport=httpx.MockTransport(handler), **kw),
    )

    cn_one = asyncio.run(wb.fetch("cn-token-one"))
    cn_one_again = asyncio.run(wb.fetch("Bearer cn-token-one"))
    cn_two = asyncio.run(wb.fetch("cn-token-two"))
    global_one = asyncio.run(wb.fetch(json.dumps({
        "accessToken": "global-token-one",
        "realm": "global",
        "uid": "same-user",
    })))
    cn_uid = asyncio.run(wb.fetch(json.dumps({
        "accessToken": "cn-user-token",
        "domain": "www.codebuddy.cn",
        "uid": "same-user",
    })))

    first_id = cn_one["identity"]["account_id"]
    assert first_id == cn_one_again["identity"]["account_id"]
    assert first_id != cn_two["identity"]["account_id"]
    assert cn_one["identity"]["email"] is None
    assert "cn-token-one" not in first_id
    assert global_one["identity"]["account_id"] != cn_uid["identity"]["account_id"]
    assert global_one["identity"]["email"] == cn_uid["identity"]["email"] == "same-user@workbuddy"
    assert cn_uid["credential"]["uid"] == "same-user"


def test_package_aggregation(monkeypatch):
    def handler(request: httpx.Request):
        accounts = [
            {
                "PackageName": "运营裂变包",
                "CapacitySize": 100,
                "CapacityRemain": 80,
                "CapacityUsed": 20,
            }
            for _ in range(5)
        ]
        accounts.append({
            "PackageName": "体验包",
            "CapacitySize": 500,
            "CapacityRemain": 500,
            "CapacityUsed": 0,
        })
        return httpx.Response(200, json={"code": 0, "Response": {"Data": {"Accounts": accounts}}})

    orig_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: orig_client(transport=httpx.MockTransport(handler), **kw),
    )

    res = asyncio.run(wb.fetch("test-token"))
    packages = res["quota"]["accounts"][0]["packages"]
    assert len(packages) == 2
    fission_pkg = next(p for p in packages if "运营裂变包" in p["name"])
    assert fission_pkg["name"] == "运营裂变包 (5个包汇总)"
    assert fission_pkg["total"] == 500
    assert fission_pkg["used"] == 100
    assert fission_pkg["remaining"] == 400
    assert fission_pkg["count"] == 5

    trial_pkg = next(p for p in packages if "体验包" in p["name"])
    assert trial_pkg["name"] == "体验包"
    assert trial_pkg["total"] == 500
    assert trial_pkg["used"] == 0
    assert trial_pkg["remaining"] == 500
    assert trial_pkg["count"] == 1


def test_get_local_codebuddy_daily_usage(tmp_path, monkeypatch):
    # Setup mock .codebuddy projects
    cb_dir = tmp_path / ".codebuddy/projects/proj1"
    cb_dir.mkdir(parents=True)
    jsonl_file = cb_dir / "session.jsonl"
    # Write sample records for 2026-10-20 (timestamp in ms, e.g. 1792454400000)
    lines = [
        json.dumps({
            "timestamp": "2026-10-20T10:00:00+08:00",
            "providerData": {"rawUsage": {"credit": 1.5, "total_tokens": 1000}},
        }),
        json.dumps({
            "timestamp": "2026-10-20T11:00:00+08:00",
            "providerData": {"rawUsage": {"credit": 2.25, "total_tokens": 1500}},
        }),
        json.dumps({
            "timestamp": "2026-10-19T15:00:00+08:00",
            "providerData": {"rawUsage": {"credit": 3.0, "total_tokens": 2000}},
        }),
    ]
    jsonl_file.write_text("\n".join(lines), encoding="utf-8")

    # Setup mock .workbuddy sqlite
    wb_dir = tmp_path / ".workbuddy"
    wb_dir.mkdir(parents=True)
    db_path = wb_dir / "workbuddy.db"
    import sqlite3
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("CREATE TABLE session_usage (session_id TEXT PRIMARY KEY, used INTEGER, size INTEGER, updated_at INTEGER, credit_json TEXT)")
    # 2026-10-20 12:00:00 CST is approx 1792468800000 ms
    from datetime import datetime, timezone, timedelta
    cst = timezone(timedelta(hours=8))
    dt_today = datetime(2026, 10, 20, 12, 0, 0, tzinfo=cst)
    ts_today = int(dt_today.timestamp() * 1000)
    cur.execute(
        "INSERT INTO session_usage VALUES (?, ?, ?, ?, ?)",
        ("sess1", 500, 300000, ts_today, json.dumps({"req1": 0.5, "req2": 0.75})),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(wb.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    daily = wb.get_local_codebuddy_daily_usage(days_limit=10)
    assert len(daily) == 2
    assert daily[0]["name"] == "2026-10-20"
    # 1.5 + 2.25 + 0.5 + 0.75 = 5.0
    assert daily[0]["used"] == 5.0
    assert daily[0]["unit"] == "积分"

    assert daily[1]["name"] == "2026-10-19"
    assert daily[1]["used"] == 3.0

    # Verify UID isolation: if sessions table exists and account_uid is given, exclude other users
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, user_id TEXT)")
    cur.execute("INSERT INTO sessions VALUES ('sess1', 'user-alpha')")
    cur.execute("INSERT INTO sessions VALUES ('sess2', 'user-beta')")
    cur.execute(
        "INSERT INTO session_usage VALUES (?, ?, ?, ?, ?)",
        ("sess2", 9999, 300000, ts_today, json.dumps({"foreign_req": 999.0})),
    )
    conn.commit()
    conn.close()

    # Query as user-alpha: should only see sess1 (1.25) + jsonl (3.75) = 5.0, NOT sess2 (999.0)
    isolated_daily = wb.get_local_codebuddy_daily_usage(account_uid="user-alpha", days_limit=10)
    assert isolated_daily[0]["used"] == 5.0

    # Query as unknown user: should only see jsonl (3.75), 0 from workbuddy.db
    unknown_daily = wb.get_local_codebuddy_daily_usage(account_uid="user-unknown", days_limit=10)
    assert unknown_daily[0]["used"] == 3.75
