import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from agent_fleet.services import antigravity_quota as agy, quota_store
from agent_fleet.services.quota_providers import QuotaProviderError


ASYNC_CLIENT = httpx.AsyncClient


@pytest.fixture(autouse=True)
def antigravity_client_secret(monkeypatch):
    # Client secrets belong in local config, not the source tree.
    monkeypatch.setenv(
        agy.CLIENT_SECRETS_ENV,
        json.dumps({agy.DEFAULT_CLIENT: 'test-client-secret'}),
    )


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
    assert account['min_remaining_fraction'] == 0.25
    assert [g['id'] for g in account['groups']] == ['third_party']
    assert len(account['groups'][0]['buckets']) == 1
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
    g4_windows = {b['window'] for b in group_map['gemini_4']['buckets']}
    assert 'weekly' in g4_windows
    assert any(b['window'] == 'weekly' and b['remaining_fraction'] == 0.35 for b in group_map['gemini_4']['buckets'])


def test_gemini_groups_from_models_does_not_invent_missing_window():
    reset = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    groups = agy.gemini_groups_from_models({
        'models': {'gemini-2.5-pro': {'quotaInfo': {
            'remainingFraction': 0.7,
            'resetTime': reset,
        }}}
    }, reference_weekly_reset=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat())

    assert len(groups) == 1
    assert [bucket['window'] for bucket in groups[0]['buckets']] == ['weekly']
    assert groups[0]['buckets'][0]['remaining_fraction'] == 0.7


def test_fetch_blends_worst_case_weekly_quota(monkeypatch):
    now = datetime.now(timezone.utc)
    weekly_reset = (now + timedelta(days=3, hours=7)).isoformat()
    five_h_reset = (now + timedelta(hours=4)).isoformat()

    def custom_handler(request):
        if request.url.host == 'oauth2.googleapis.com':
            return httpx.Response(200, json={'access_token': 'new-access', 'refresh_token': 'rotated-refresh', 'expires_in': 3600})
        if request.url.path.endswith('userinfo'):
            return httpx.Response(200, json={'email': 'coolxll.qq@gmail.com', 'verified_email': True, 'name': 'CoolXll'})
        if request.url.path.endswith(':loadCodeAssist'):
            return httpx.Response(200, json={'cloudaicompanionProject': 'project', 'paidTier': {'id': 'g1-pro-tier'}})
        if request.url.path.endswith(':retrieveUserQuotaSummary'):
            return httpx.Response(200, json={'groups': [
                {
                    'displayName': 'Gemini Models',
                    'buckets': [
                        {'bucketId': 'gemini-weekly', 'displayName': 'Weekly Limit Remaining', 'window': 'weekly', 'remainingFraction': 0.92567885, 'resetTime': '2026-10-22T08:38:59Z'},
                        {'bucketId': 'gemini-5h', 'displayName': 'Five Hour Limit Remaining', 'window': '5h', 'remainingFraction': 1.0, 'resetTime': five_h_reset}
                    ]
                },
                {
                    'displayName': 'Claude and GPT models',
                    'buckets': [
                        {'bucketId': '3p-weekly', 'displayName': 'Weekly Limit Remaining', 'window': 'weekly', 'remainingFraction': 1.0, 'resetTime': weekly_reset},
                        {'bucketId': '3p-5h', 'displayName': 'Five Hour Limit Remaining', 'window': '5h', 'remainingFraction': 1.0, 'resetTime': five_h_reset}
                    ]
                }
            ]})
        if request.url.path.endswith(':fetchAvailableModels'):
            return httpx.Response(200, json={
                'models': {
                    'gemini-2.5-flash': {
                        'quotaInfo': {
                            'remainingFraction': 0.013393676,
                            'resetTime': weekly_reset
                        }
                    }
                }
            })
        return httpx.Response(404)

    install_transport(monkeypatch, custom_handler)
    result = asyncio.run(agy.fetch(credential()))
    account = result['quota']['accounts'][0]
    gemini_group = next(g for g in account['groups'] if g['id'] == 'gemini')
    buckets = {b['window']: b for b in gemini_group['buckets']}

    # Weekly should be updated to the true 1.3% from live models, not stale 92.6%
    assert abs(buckets['weekly']['remaining_fraction'] - 0.013393676) < 1e-6
    assert buckets['weekly']['reset_at'] == weekly_reset
    # 5h should be preserved as 100%
    assert buckets['5h']['remaining_fraction'] == 1.0


def test_fetch_keeps_real_summary_5h_when_models_only_report_weekly(monkeypatch):
    weekly_reset = (datetime.now(timezone.utc) + timedelta(days=4)).isoformat()
    five_h_reset = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()

    def custom_handler(request):
        if request.url.host == 'oauth2.googleapis.com':
            return httpx.Response(200, json={'access_token': 'new-access', 'refresh_token': 'rotated-refresh', 'expires_in': 3600})
        if request.url.path.endswith('userinfo'):
            return httpx.Response(200, json={'email': 'test@example.com', 'verified_email': True})
        if request.url.path.endswith(':loadCodeAssist'):
            return httpx.Response(200, json={'cloudaicompanionProject': 'project'})
        if request.url.path.endswith(':retrieveUserQuotaSummary'):
            return httpx.Response(200, json={'groups': [{
                'displayName': 'Gemini Models',
                'buckets': [{
                    'bucketId': 'gemini-5h', 'window': '5h',
                    'remainingFraction': 0.2, 'resetTime': five_h_reset,
                }],
            }]})
        if request.url.path.endswith(':fetchAvailableModels'):
            return httpx.Response(200, json={'models': {
                'gemini-2.5-pro': {'quotaInfo': {
                    'remainingFraction': 0.8, 'resetTime': weekly_reset,
                }}
            }})
        return httpx.Response(404)

    install_transport(monkeypatch, custom_handler)
    result = asyncio.run(agy.fetch(credential()))
    groups = result['quota']['accounts'][0]['groups']
    gemini = next(group for group in groups if group['id'] == 'gemini')
    buckets = {bucket['window']: bucket for bucket in gemini['buckets']}

    assert buckets['5h']['remaining_fraction'] == 0.2
    assert buckets['5h']['reset_at'] == five_h_reset
    assert buckets['weekly']['remaining_fraction'] == 0.8
    assert set(buckets) == {'5h', 'weekly'}


def test_fetch_does_not_add_window_missing_from_summary_only(monkeypatch):
    five_h_reset = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()

    def custom_handler(request):
        if request.url.host == 'oauth2.googleapis.com':
            return httpx.Response(200, json={'access_token': 'new-access', 'refresh_token': 'rotated-refresh', 'expires_in': 3600})
        if request.url.path.endswith('userinfo'):
            return httpx.Response(200, json={'email': 'test@example.com', 'verified_email': True})
        if request.url.path.endswith(':loadCodeAssist'):
            return httpx.Response(200, json={'cloudaicompanionProject': 'project'})
        if request.url.path.endswith(':retrieveUserQuotaSummary'):
            return httpx.Response(200, json={'groups': [{
                'displayName': 'Gemini Models',
                'buckets': [{
                    'bucketId': 'gemini-5h', 'window': '5h',
                    'remainingFraction': 0.6, 'resetTime': five_h_reset,
                }],
            }]})
        if request.url.path.endswith(':fetchAvailableModels'):
            return httpx.Response(200, json={'models': {}})
        return httpx.Response(404)

    install_transport(monkeypatch, custom_handler)
    result = asyncio.run(agy.fetch(credential()))
    groups = result['quota']['accounts'][0]['groups']
    gemini = next(group for group in groups if group['id'] == 'gemini')

    assert [bucket['window'] for bucket in gemini['buckets']] == ['5h']


def test_fetch_cross_host_worst_case_wins_weekly_quota(monkeypatch):
    daily_weekly_reset = "2026-10-26T02:12:34Z"
    daily_5h_reset = "2026-10-20T09:43:58Z"
    prod_weekly_reset = "2026-10-27T08:54:17Z"
    prod_5h_reset = "2026-10-20T13:54:17Z"

    seen_user_agents = []

    def custom_handler(request):
        seen_user_agents.append(request.headers.get("User-Agent"))
        if request.url.host == 'oauth2.googleapis.com':
            return httpx.Response(200, json={'access_token': 'new-access', 'refresh_token': 'rotated-refresh', 'expires_in': 3600})
        if request.url.path.endswith('userinfo'):
            return httpx.Response(200, json={'email': 'wendyyuan360@gmail.com', 'verified_email': True})
        if request.url.path.endswith(':loadCodeAssist'):
            return httpx.Response(200, json={'cloudaicompanionProject': 'project', 'paidTier': {'id': 'g1-pro-tier'}})
        if request.url.path.endswith(':retrieveUserQuotaSummary'):
            if "daily" in request.url.host:
                return httpx.Response(200, json={'groups': [{
                    'displayName': 'Gemini Models',
                    'buckets': [
                        {'bucketId': 'gemini-weekly', 'window': 'weekly', 'remainingFraction': 0.2148, 'resetTime': daily_weekly_reset},
                        {'bucketId': 'gemini-5h', 'window': '5h', 'remainingFraction': 0.1949, 'resetTime': daily_5h_reset},
                    ],
                }, {
                    'displayName': 'Claude and GPT models',
                    'buckets': [
                        {'bucketId': '3p-weekly', 'window': 'weekly', 'remainingFraction': 0.0, 'resetTime': '2026-10-22T10:14:32Z'},
                    ],
                }]})
            else:
                # PROD returns stale 100% (1.0)
                return httpx.Response(200, json={'groups': [{
                    'displayName': 'Gemini Models',
                    'buckets': [
                        {'bucketId': 'gemini-weekly', 'window': 'weekly', 'remainingFraction': 1.0, 'resetTime': prod_weekly_reset},
                        {'bucketId': 'gemini-5h', 'window': '5h', 'remainingFraction': 1.0, 'resetTime': prod_5h_reset},
                    ],
                }]})
        if request.url.path.endswith(':fetchAvailableModels'):
            return httpx.Response(200, json={'models': {}})
        return httpx.Response(404)

    install_transport(monkeypatch, custom_handler)
    result = asyncio.run(agy.fetch(credential()))
    groups = result['quota']['accounts'][0]['groups']
    gemini = next(group for group in groups if group['id'] == 'gemini')
    buckets = {bucket['window']: bucket for bucket in gemini['buckets']}

    assert any("antigravity/2.24.0" in ua for ua in seen_user_agents if ua)
    # Worst-case wins should preserve daily live consumption instead of stale 1.0 from PROD
    assert buckets['weekly']['remaining_fraction'] == 0.2148
    assert buckets['weekly']['reset_at'] == daily_weekly_reset
    assert buckets['5h']['remaining_fraction'] == 0.1949
    assert buckets['5h']['reset_at'] == daily_5h_reset


