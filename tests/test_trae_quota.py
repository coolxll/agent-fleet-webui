import asyncio
import json

import httpx
import pytest

from agent_fleet.services import quota_store, trae_quota as trae
from agent_fleet.services.quota_providers import QuotaProviderError, fetch_account

ASYNC_CLIENT = httpx.AsyncClient


def install_transport(monkeypatch, handler):
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: ASYNC_CLIENT(transport=httpx.MockTransport(handler), **kw))


def pack(total, used=0, **kwargs):
    return {'entitlement_base_info': {'quota': {'credits_limit': total}, 'user_id': 'user-1',
                                      'end_time': 1795017600},
            'usage': {} if used is None else {'credits_amount': used}, **kwargs}


@pytest.mark.parametrize('raw', [
    'saved-token', 'Cloud-IDE-JWT saved-token', 'Bearer saved-token',
    'Authorization: Cloud-IDE-JWT saved-token', '"Cloud-IDE-JWT saved-token"',
    '{"token":"saved-token"}', '{"access_token":"saved-token"}',
    '{"authorization":"Cloud-IDE-JWT saved-token"}',
])
def test_parse_plain_token_and_copied_headers(raw):
    assert trae.parse_credential(raw) == {'access_token': 'saved-token', 'device_id': ''}


@pytest.mark.parametrize('raw', ['', '{}', '{invalid', '{"token":true}', '{"token":"abc\\r\\ninjected"}',
                                 '{"token":"abc","device_id":"device\\ninjected"}'])
def test_rejects_empty_malformed_and_multiline_headers(raw):
    with pytest.raises(QuotaProviderError):
        trae.parse_credential(raw)


def test_authoritative_credit_summary_wins_over_incomplete_pack_list():
    result = trae.parse_usage({
        'usage_summary': {'total_amount': 4050, 'consumed_amount': 3751.3},
        'user_entitlement_pack_list': [pack(150, None), pack(500, 500)],
    })
    assert result['total'] == 4050
    assert result['used'] == 3751.3
    assert result['remaining'] == pytest.approx(298.7)
    assert len(result['packages']) == 2


def test_pack_fallback_handles_feature_flags_untouched_and_overdrawn_packs():
    result = trae.parse_usage({'user_entitlement_pack_list': [
        pack(100, 120), pack('100', '20'), pack(0, 0), pack(150, None),
        {'entitlement_base_info': {'quota': {'enable_solo_agent': True}}, 'usage': {}},
    ]})
    assert result['total'] == 350
    assert result['used'] == 140
    assert result['remaining'] == 210
    assert result['user_id'] == 'user-1'


def test_summary_zero_and_exhaustion_are_preserved():
    for total, used in [(0, 0), (100, 100), (100, 125)]:
        result = trae.parse_usage({'usage_summary': {'total_amount': total, 'consumed_amount': used}})
        assert result['remaining'] == 0
        assert result['used'] == used


@pytest.mark.parametrize('payload', [
    {}, {'user_entitlement_pack_list': []}, {'user_entitlement_pack_list': [pack(100, 10), {}]},
    {'user_entitlement_pack_list': [pack(100, -1)]},
    {'user_entitlement_pack_list': [pack(100, True)]},
    {'user_entitlement_pack_list': [pack(100, 0, usage={'credits_amount': None})]},
    {'user_entitlement_pack_list': [pack(None, None)]},
    {'user_entitlement_pack_list': [pack('NaN', 1)]},
    {'user_entitlement_pack_list': [pack(0, 5)]},
    {'usage_summary': {'total_amount': 100}},
    {'usage_summary': {'total_amount': True, 'consumed_amount': 0}},
    {'code': 1001, 'usage_summary': {'total_amount': 100, 'consumed_amount': 1}},
    {'ResponseMetadata': {'Error': {'Message': 'private response'}}},
])
def test_invalid_or_partial_data_is_not_published_as_valid_balance(payload):
    with pytest.raises(QuotaProviderError):
        trae.parse_usage(payload)


def test_wrapped_camel_case_payload():
    result = trae.parse_usage({'data': {'userEntitlementPackList': [{
        'entitlementBaseInfo': {'quota': {'creditsLimit': 100}, 'userId': 'user-2'},
        'usage': {'creditsAmount': 10},
    }]}})
    assert result['remaining'] == 90
    assert result['user_id'] == 'user-2'


def test_fetch_uses_cn_read_only_endpoint_and_does_not_invent_reset(monkeypatch):
    calls = []
    def response(request):
        calls.append(request)
        assert str(request.url) == trae.API + trae.PATHS[0]
        assert request.method == 'POST'
        assert request.headers['Authorization'] == 'Cloud-IDE-JWT saved-token'
        assert request.headers['X-User-Region'] == 'CN'
        assert request.headers['X-Device-Id'] == '123456'
        assert json.loads(request.content) == {}
        return httpx.Response(200, json={
            'usage_summary': {'total_amount': 1000, 'consumed_amount': 250},
            'user_entitlement_pack_list': [pack(1000, 250)],
        })
    install_transport(monkeypatch, response)
    result = asyncio.run(fetch_account('trae', json.dumps({'access_token': 'saved-token', 'device_id': '123456'})))
    bucket = result['quota']['accounts'][0]['groups'][0]['buckets'][0]
    assert bucket['remaining'] == 750
    assert bucket['total'] == 1000
    assert bucket['remaining_fraction'] == 0.75
    assert bucket['reset_at'] is None
    assert result['identity']['account_id'] == 'trae:cn:uid:user-1'
    assert result['identity']['plan'] is None
    assert len(calls) == 1
    assert 'saved-token' not in json.dumps(result['quota'])
    assert '123456' not in json.dumps(result['quota'])


def test_missing_web_endpoint_falls_back_to_ide_endpoint(monkeypatch):
    paths = []
    def response(request):
        paths.append(request.url.path)
        if len(paths) == 1:
            return httpx.Response(404)
        return httpx.Response(200, json={'user_entitlement_pack_list': [pack(500, 125)]})
    install_transport(monkeypatch, response)
    result = asyncio.run(trae.fetch('token'))
    assert paths == list(trae.PATHS)
    assert result['quota']['accounts'][0]['min_remaining_fraction'] == 0.75


@pytest.mark.parametrize('status', [401, 403, 429, 500, 302])
def test_http_errors_are_sanitized_and_not_saved(monkeypatch, status):
    calls = []
    def response(request):
        calls.append(request)
        return httpx.Response(status, text='private response containing credentials')
    install_transport(monkeypatch, response)
    with pytest.raises(QuotaProviderError) as error:
        asyncio.run(trae.fetch('saved-token'))
    assert 'saved-token' not in str(error.value)
    assert 'private response' not in str(error.value)
    assert len(calls) == 1


def test_account_store_deduplicates_same_user_and_hides_credentials(monkeypatch, fleet_db):
    def response(request):
        return httpx.Response(200, json={'user_entitlement_pack_list': [pack(100, 25)]})
    install_transport(monkeypatch, response)
    first = asyncio.run(quota_store.add_account('trae', 'Trae account', 'first-token'))
    second = asyncio.run(quota_store.add_account('trae', None, 'renewed-token'))
    assert first['id'] == second['id']
    assert len(quota_store.list_accounts()) == 1
    assert first['agent'] == 'trae'
    assert 'first-token' not in json.dumps(first)
    assert 'renewed-token' not in json.dumps(second)
    assert quota_store.get_account(first['id'])['credential']['access_token'] == 'renewed-token'


def test_tokens_without_user_identity_do_not_merge_accounts(monkeypatch):
    install_transport(monkeypatch, lambda r: httpx.Response(200, json={'usage_summary': {'total_amount': 100, 'consumed_amount': 0}}))
    first = asyncio.run(trae.fetch('first-token'))
    repeat = asyncio.run(trae.fetch('first-token'))
    second = asyncio.run(trae.fetch('second-token'))
    assert first['identity']['account_id'] == repeat['identity']['account_id']
    assert first['identity']['account_id'] != second['identity']['account_id']
    assert 'first-token' not in first['identity']['account_id']
    assert first['identity']['email'] is None


def test_conflicting_pack_identities_are_rejected():
    other = pack(100, 0)
    other['entitlement_base_info']['user_id'] = 'another-user'
    with pytest.raises(QuotaProviderError, match='conflicting'):
        trae.parse_usage({'user_entitlement_pack_list': [pack(100, 0), other]})


def test_trae_provider_import_and_refresh_through_api(client, monkeypatch):
    install_transport(monkeypatch, lambda r: httpx.Response(200, json={
        'usage_summary': {'total_amount': 2000, 'consumed_amount': 375.5},
        'user_entitlement_pack_list': [pack(2000, 375.5)],
    }))
    assert any(p['id'] == 'trae' for p in client.get('/api/quota-providers').json()['providers'])
    imported = client.post('/api/quota-accounts', json={
        'provider': 'trae', 'credential': 'Cloud-IDE-JWT private-token', 'label': 'My Trae CN',
    })
    assert imported.status_code == 200
    account = imported.json()
    assert account['provider_name'] == 'Trae CN'
    assert account['label'] == 'My Trae CN'
    assert account['agent'] == 'trae'
    refreshed = client.post(f"/api/quota-accounts/{account['id']}/refresh")
    assert refreshed.status_code == 200
    bucket = refreshed.json()['quota']['accounts'][0]['groups'][0]['buckets'][0]
    assert bucket['remaining'] == 1624.5
    assert 'private-token' not in refreshed.text
    assert 'private-token' not in client.get('/api/quota-accounts').text
