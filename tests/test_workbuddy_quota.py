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


def test_parse_credential_empty():
    with pytest.raises(QuotaProviderError, match="empty"):
        wb.parse_credential("   ")


def test_fetch_workbuddy_cn_success(monkeypatch):
    def handler(request: httpx.Request):
        assert request.url.host == "www.codebuddy.cn"
        assert request.url.path == "/v2/billing/meter/get-user-resource"
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
