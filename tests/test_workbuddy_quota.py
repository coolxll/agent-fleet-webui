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


def test_fetch_all_package_pages_and_ignore_total_dosage_as_capacity(monkeypatch):
    pages = []
    def handler(request):
        page = json.loads(request.content)['PageNumber']
        pages.append(page)
        rows = [{'AccountId': f'{page}-{i}', 'CapacitySize': 10,
                 'CapacityRemain': 4, 'CapacityUsed': 6} for i in range(100 if page == 1 else 5)]
        return httpx.Response(200, json={'code': 0, 'Response': {'Data': {
            'TotalCount': 105, 'TotalDosage': 9999, 'Accounts': rows,
        }}})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    account = asyncio.run(wb.fetch('test-token'))['quota']['accounts'][0]
    assert pages == [1, 2]
    bucket = account['groups'][0]['buckets'][0]
    assert bucket['total'] == 1050
    assert bucket['remaining'] == 420
    assert bucket['used'] == 630
    assert bucket['remaining_fraction'] == 0.4
    assert bucket['reset_kind'] == 'partial_expiry'


def test_expired_packages_and_empty_balances_do_not_set_next_expiry(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={'Response': {'Data': {'Accounts': [
            {'CapacitySize': 100, 'CapacityRemain': 100, 'CycleEndTime': '2020-01-01 00:00:00'},
            {'CapacitySize': 100, 'CapacityRemain': 0, 'CycleEndTime': '2027-01-01 00:00:00'},
            {'CapacitySize': 100, 'CapacityRemain': 80, 'CycleEndTime': '2027-02-01 00:00:00'},
        ]}}})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    bucket = asyncio.run(wb.fetch('test-token'))['quota']['accounts'][0]['groups'][0]['buckets'][0]
    assert bucket['total'] == 200
    assert bucket['remaining'] == 80
    assert bucket['reset_at'] == '2027-01-31T16:00:00Z'


def test_partial_pagination_failure_does_not_save_incomplete_quota(monkeypatch):
    def handler(request):
        page = json.loads(request.content)['PageNumber']
        if page == 2:
            return httpx.Response(503)
        return httpx.Response(200, json={'Response': {'Data': {
            'TotalCount': 2, 'Accounts': [{'CapacitySize': 100, 'CapacityRemain': 80}],
        }}})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    with pytest.raises(QuotaProviderError, match='503'):
        asyncio.run(wb.fetch('test-token'))


@pytest.mark.parametrize('payload', [{}, {'code': 0, 'data': {}}, {'code': 1}, {'Response': {'Data': {'Accounts': 'invalid'}}}])
def test_invalid_resource_is_not_shown_as_zero_balance(monkeypatch, payload):
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload)), **kw))
    with pytest.raises(QuotaProviderError):
        asyncio.run(wb.fetch('test-token'))


def test_cycle_amounts_keep_zero_and_fractional_credits():
    assert wb._package_remain_used({'CycleCapacitySize': 100, 'CycleCapacityRemain': 0, 'CycleCapacityUsed': 5}) == (0, 100, 100)
    assert wb._package_remain_used({'CapacitySize': 10.5, 'CapacityRemain': 2.25}) == (2.25, 8.25, 10.5)
