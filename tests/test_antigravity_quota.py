import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from agent_fleet.services import antigravity_quota as agy, quota_store
from agent_fleet.services.quota_providers import QuotaProviderError


ASYNC_CLIENT = httpx.AsyncClient


def install_transport(monkeypatch, handler):
    original = ASYNC_CLIENT
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))


def credential(**kwargs):
    return json.dumps({'access_token': 'old-access', 'refresh_token': 'saved-refresh',
                       'expiry': '2020-01-01T00:00:00Z', **kwargs})


def handler(request):
    if request.url.host == 'oauth2.googleapis.com':
        assert b'refresh_token=saved-refresh' in request.content
        return httpx.Response(200, json={'access_token': 'new-access', 'refresh_token': 'rotated-refresh', 'expires_in': 3600})
    assert request.headers['Authorization'] == 'Bearer new-access'
    if request.url.path.endswith('userinfo'):
        return httpx.Response(200, json={'email': 'test@example.com', 'verified_email': True, 'name': 'Test'})
    if request.url.path.endswith(':loadCodeAssist'):
        return httpx.Response(200, json={'cloudaicompanionProject': 'project', 'paidTier': {'id': 'g1-pro-tier'}})
    if request.url.path.endswith(':retrieveUserQuotaSummary'):
        return httpx.Response(200, json={'groups': [{'buckets': [
            {'bucketId': '3p-weekly', 'window': 'weekly', 'remainingFraction': 0.25, 'resetTime': '2027-01-01T00:00:00Z'},
            {'bucketId': '3p-5h', 'window': '5h', 'remainingFraction': 0.9, 'resetTime': '2027-01-01T00:00:00Z', 'disabled': True}]}]})
    return httpx.Response(200, json={'models': {'gemini-pro': {'quotaInfo': {'resetTime': '2027-01-01T00:00:00Z'}}}})


def test_refresh_and_independent_quota(monkeypatch):
    install_transport(monkeypatch, handler)
    result = asyncio.run(agy.fetch(credential()))
    assert result['credential']['refresh_token'] == 'rotated-refresh'
    assert result['identity']['email'] == 'test@example.com'
    assert result['identity']['plan'] == 'Google AI Pro'
    account = result['quota']['accounts'][0]
    assert account['min_remaining_fraction'] == 0
    assert [g['id'] for g in account['groups']] == ['gemini', 'third_party']
    assert len(account['groups'][1]['buckets']) == 1
    assert 'project' not in json.dumps(result['quota'])


def test_reject_unverified_identity(monkeypatch):
    def response(request):
        if request.url.path.endswith('userinfo'):
            return httpx.Response(200, json={'email': 'fake@example.com', 'verified_email': False})
        return handler(request)
    install_transport(monkeypatch, response)
    with pytest.raises(QuotaProviderError, match='verified'):
        asyncio.run(agy.fetch(credential()))


def test_import_deduplicates_and_hides_credentials(monkeypatch, fleet_db):
    install_transport(monkeypatch, handler)
    first = asyncio.run(quota_store.add_account('antigravity', None, credential()))
    second = asyncio.run(quota_store.add_account('antigravity', None, credential()))
    assert first['id'] == second['id']
    assert len(quota_store.list_accounts()) == 1
    assert first['agent'] == 'agy'
    public = json.dumps(first)
    assert 'refresh_token' not in public and 'access_token' not in public
    assert all(k not in first for k in ('host', 'host_id', 'runtime_id', 'machine'))
    assert quota_store.store_path().stat().st_mode & 0o777 == 0o600


def test_failed_quota_preserves_rotated_token(monkeypatch, fleet_db):
    install_transport(monkeypatch, handler)
    account = asyncio.run(quota_store.add_account('antigravity', None, credential()))
    data = quota_store._read()
    data['accounts'][0]['credential'] = json.loads(credential())
    quota_store._write(data)
    def failed(request):
        if request.url.path.endswith(':loadCodeAssist'):
            return httpx.Response(503, text='private response must not be returned')
        return handler(request)
    install_transport(monkeypatch, failed)
    with pytest.raises(QuotaProviderError, match='HTTP 503'):
        asyncio.run(quota_store.refresh_account(account['id']))
    stored = quota_store.get_account(account['id'])
    assert stored['credential']['refresh_token'] == 'rotated-refresh'
    assert stored['status'] == 'error'
    assert 'private response' not in stored['last_error']


def test_access_token_401_refreshes_once(monkeypatch):
    calls = []
    def expired(request):
        calls.append(str(request.url))
        if request.headers.get('Authorization') == 'Bearer old-access':
            return httpx.Response(401)
        return handler(request)
    install_transport(monkeypatch, expired)
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    assert asyncio.run(agy.fetch(credential(expiry=future)))['identity']['email'] == 'test@example.com'
    assert sum('oauth2.googleapis.com/token' in c for c in calls) == 1


def test_quota_groups_parses_gemini_weekly_and_gemini_4_argon():
    summary_payload = {
        'response': {
            'groups': [
                {
                    'displayName': 'Gemini Models',
                    'buckets': [
                        {'bucketId': 'gemini-weekly', 'displayName': 'Weekly Limit Remaining', 'window': 'weekly', 'remainingFraction': 0.75, 'resetTime': '2027-01-08T00:00:00Z'},
                        {'bucketId': 'gemini-5h', 'displayName': 'Five Hour Limit Remaining', 'window': '5h', 'remainingFraction': 0.95, 'resetTime': '2027-01-01T04:00:00Z'}
                    ]
                },
                {
                    'name': 'Gemini 4 Argon',
                    'description': 'Separate quota for Gemini 4 generation',
                    'buckets': [
                        {'id': 'gemini-argon-wtu-weekly', 'name': 'Gemini 4 Argon Weekly Limit', 'remaining': {'remainingFraction': 0.42}, 'reset_time': '2027-01-08T12:00:00Z'}
                    ]
                },
                {
                    'displayName': 'Claude and GPT models',
                    'buckets': [
                        {'bucketId': '3p-weekly', 'window': 'weekly', 'remainingFraction': 0.88, 'resetTime': '2027-01-08T00:00:00Z'}
                    ]
                }
            ]
        }
    }
    groups = agy.quota_groups(summary_payload)
    group_map = {g['id']: g for g in groups}
    assert 'gemini' in group_map
    assert 'gemini_4' in group_map
    assert 'third_party' in group_map

    gemini_buckets = {b['window']: b for b in group_map['gemini']['buckets']}
    assert 'weekly' in gemini_buckets
    assert '5h' in gemini_buckets
    assert gemini_buckets['weekly']['remaining_fraction'] == 0.75
    assert gemini_buckets['5h']['remaining_fraction'] == 0.95

    assert group_map['gemini_4']['name'] == 'Gemini 4 Argon'
    g4_buckets = {b['window']: b for b in group_map['gemini_4']['buckets']}
    assert 'weekly' in g4_buckets
    assert g4_buckets['weekly']['remaining_fraction'] == 0.42


def test_gemini_groups_from_models_handles_weekly_and_gemini_4():
    now = datetime.now(timezone.utc)
    models = {
        'models': {
            'gemini-2.5-flash': {
                'quotaInfo': {
                    'remainingFraction': 0.9,
                    'resetTime': (now + timedelta(hours=3)).isoformat()
                }
            },
            'gemini-2.5-pro': {
                'quotaInfo': {
                    'remainingFraction': 0.65,
                    'resetTime': (now + timedelta(days=6)).isoformat()
                }
            },
            'gemini-4-argon': {
                'displayName': 'Gemini 4 Argon',
                'quotaInfo': {
                    'remainingFraction': 0.35,
                    'resetTime': (now + timedelta(days=5)).isoformat()
                }
            }
        }
    }
    groups = agy.gemini_groups_from_models(models)
    group_map = {g['id']: g for g in groups}
    assert 'gemini' in group_map
    assert 'gemini_4' in group_map

    gemini_windows = {b['window'] for b in group_map['gemini']['buckets']}
    assert '5h' in gemini_windows
    assert 'weekly' in gemini_windows
    assert group_map['gemini_4']['name'] == 'Gemini 4 Argon'
    assert group_map['gemini_4']['buckets'][0]['window'] == 'weekly'
    assert group_map['gemini_4']['buckets'][0]['remaining_fraction'] == 0.35

