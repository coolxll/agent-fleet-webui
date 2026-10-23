import asyncio
import json
from datetime import timedelta

import pytest

from agent_fleet.services import magpie_quota as magpie, quota_providers, quota_store


@pytest.fixture
def source(fleet_db, tmp_path, monkeypatch):
    directory = tmp_path / 'magpie'
    directory.mkdir()
    monkeypatch.setattr(fleet_db, 'magpie_config_dir', directory)
    path = directory / 'logins.json'
    path.write_text(json.dumps([{
        'id': 'login-a', 'agent': 'codex', 'user': 'a@example.test · Team', 'plan': 'team',
        'auth': {'tokens': {'access_token': 'current-access', 'refresh_token': 'NEVER-REFRESH',
                            'id_token': 'NEVER-FORWARD', 'account_id': 'team-a'}},
    }]))
    return path


def result():
    return {
        'credential': {'access_token': 'current-access'},
        'identity': {'account_id': 'team-a', 'email': 'a@example.test', 'plan': 'team'},
        'quota': {'source': 'codex', 'observed_at': '2026-10-23T00:00:00Z', 'accounts': []},
    }


def age_account(ident):
    data = quota_store._read()
    account = next(a for a in data['accounts'] if a['id'] == ident)
    account['last_checked_at'] = magpie._iso(magpie._now() - timedelta(hours=2))
    account['next_query_at'] = account['last_checked_at']
    quota_store._write(data)


def test_link_migrates_existing_without_storing_tokens_or_writing_source(source, monkeypatch):
    quota_store._write({'version': 1, 'accounts': [{
        'id': 'existing', 'provider': 'codex', 'email': 'a@example.test', 'label': 'Work',
        'credential': {'account_id': 'team-a', 'access_token': 'old', 'refresh_token': 'old-refresh'},
    }]})
    before = source.read_bytes()
    async def fetch(provider, raw):
        credential = json.loads(raw)
        assert provider == 'codex'
        assert credential == {'access_token': 'current-access', 'account_id': 'team-a', 'email': 'a@example.test'}
        return result()
    monkeypatch.setattr(quota_providers, 'fetch_account', fetch)
    linked = asyncio.run(magpie.link_account('login-a'))
    assert linked['id'] == 'existing'
    assert linked['label'] == 'Work'
    assert linked['credential_source'] == 'magpie'
    assert linked['status'] == 'ok'
    assert len(quota_store.list_accounts()) == 1
    assert 'credential' not in quota_store.get_account('existing')
    stored = quota_store.store_path().read_text()
    assert 'current-access' not in stored and 'NEVER-' not in stored and 'old-refresh' not in stored
    assert source.read_bytes() == before
    assert quota_store.store_path().stat().st_mode & 0o777 == 0o600


def test_query_rereads_rotated_access_and_never_renews(source, monkeypatch):
    calls = []
    async def fetch(provider, raw):
        credential = json.loads(raw)
        assert 'refresh_token' not in credential and 'id_token' not in credential
        calls.append(credential['access_token'])
        if len(calls) == 2:
            rows = json.loads(source.read_text())
            rows[0]['auth']['tokens']['access_token'] = 'magpie-rotated'
            source.write_text(json.dumps(rows))
            raise quota_providers.QuotaAuthError('expired')
        return result()
    monkeypatch.setattr(quota_providers, 'fetch_account', fetch)
    async def scenario():
        linked = await magpie.link_account('login-a')
        age_account(linked['id'])
        await quota_store.refresh_account(linked['id'])
    asyncio.run(scenario())
    assert calls == ['current-access', 'current-access', 'magpie-rotated']


def test_expired_token_keeps_snapshot_and_reports_auth_required(source, monkeypatch):
    calls = 0
    async def fetch(provider, raw):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise quota_providers.QuotaAuthError('secret vendor response')
        return result()
    monkeypatch.setattr(quota_providers, 'fetch_account', fetch)
    before = source.read_bytes()
    async def scenario():
        linked = await magpie.link_account('login-a')
        age_account(linked['id'])
        with pytest.raises(quota_providers.QuotaProviderError):
            await magpie.query_account(linked['id'])
        return linked
    linked = asyncio.run(scenario())
    account = quota_store.get_account(linked['id'])
    assert account['error_code'] == 'auth_required'
    assert account['quota'] == result()['quota']
    assert account['last_refreshed_at'] == linked['last_refreshed_at']
    assert 'magpie' in account['last_error']
    assert 'secret vendor' not in account['last_error']
    assert calls == 2  # no retry of an unchanged token, no refresh flow
    assert source.read_bytes() == before


def test_singleflight_and_cooldown(source, monkeypatch):
    calls = 0
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        async def fetch(provider, raw):
            nonlocal calls
            calls += 1
            if calls > 1:
                started.set()
                await release.wait()
            return result()
        monkeypatch.setattr(quota_providers, 'fetch_account', fetch)
        linked = await magpie.link_account('login-a')
        age_account(linked['id'])
        first = asyncio.create_task(quota_store.refresh_account(linked['id']))
        await started.wait()
        second = asyncio.create_task(quota_store.refresh_account(linked['id']))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first, second)
        await magpie.query_account(linked['id'])
    asyncio.run(scenario())
    assert calls == 2


def test_collector_only_queries_due_links_and_backs_off(source, monkeypatch):
    calls = 0
    async def fetch(provider, raw):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError('network failure')
        return result()
    monkeypatch.setattr(quota_providers, 'fetch_account', fetch)
    async def scenario():
        linked = await magpie.link_account('login-a')
        data = quota_store._read()
        data['accounts'].append({'id': 'manual-agy', 'provider': 'antigravity', 'credential': {'refresh_token': 'do-not-use'}})
        quota_store._write(data)
        collector = magpie.Collector()
        await collector.run_once()  # recent link isn't due
        assert calls == 1
        for failure in (1, 2):
            age_account(linked['id'])
            await collector.run_once()
            a = quota_store.get_account(linked['id'])
            delay = (magpie._time(a['next_query_at']) - magpie._time(a['last_checked_at'])).total_seconds()
            assert 600 * failure <= delay < 600 * failure + 2
        await collector.run_once()  # backoff prevents another request
        magpie.update_collection(False, 600)
        age_account(linked['id'])
        await collector.run_once()
    asyncio.run(scenario())
    assert calls == 3
    assert quota_store.get_account('manual-agy')['credential']['refresh_token'] == 'do-not-use'


def test_deleted_link_during_query_is_not_resurrected(source, monkeypatch):
    async def fetch(provider, raw):
        return result()
    monkeypatch.setattr(quota_providers, 'fetch_account', fetch)
    async def scenario():
        linked = await magpie.link_account('login-a')
        age_account(linked['id'])
        async def deleted(provider, raw):
            quota_store.delete_account(linked['id'])
            return result()
        monkeypatch.setattr(quota_providers, 'fetch_account', deleted)
        with pytest.raises(KeyError):
            await magpie.query_account(linked['id'])
    asyncio.run(scenario())
    assert not quota_store.list_accounts()


def test_missing_source_preserves_snapshot(source, monkeypatch):
    async def fetch(provider, raw):
        return result()
    monkeypatch.setattr(quota_providers, 'fetch_account', fetch)
    async def scenario():
        linked = await magpie.link_account('login-a')
        age_account(linked['id'])
        source.unlink()
        with pytest.raises(quota_providers.QuotaProviderError):
            await magpie.query_account(linked['id'])
        return linked['id']
    ident = asyncio.run(scenario())
    assert quota_store.get_account(ident)['error_code'] == 'source_unavailable'
    assert quota_store.get_account(ident)['quota'] == result()['quota']


def test_link_rejects_identity_replacement(source, monkeypatch):
    async def fetch(provider, raw):
        return result()
    monkeypatch.setattr(quota_providers, 'fetch_account', fetch)
    async def scenario():
        linked = await magpie.link_account('login-a')
        age_account(linked['id'])
        rows = json.loads(source.read_text())
        rows[0]['auth']['tokens']['account_id'] = 'different-team'
        source.write_text(json.dumps(rows))
        with pytest.raises(quota_providers.QuotaProviderError):
            await magpie.query_account(linked['id'])
        assert quota_store.get_account(linked['id'])['account_id'] == 'team-a'
    asyncio.run(scenario())


def test_api_sources_redacted_link_and_collection_settings(source, client, monkeypatch):
    async def fetch(provider, raw):
        return result()
    monkeypatch.setattr(quota_providers, 'fetch_account', fetch)
    sources = client.get('/api/quota-sources/magpie')
    assert sources.status_code == 200
    assert sources.json()['accounts'][0]['id'] == 'login-a'
    assert 'access_token' not in sources.text and 'NEVER-' not in sources.text
    response = client.post('/api/quota-sources/magpie/link', json={'source_account_id': 'login-a'})
    assert response.status_code == 200
    ident = response.json()['id']
    again = client.post('/api/quota-sources/magpie/link', json={'source_account_id': 'login-a'})
    assert again.json()['id'] == ident
    assert 'credential' not in quota_store.get_account(ident)
    assert client.get('/api/quota-sources/magpie').json()['accounts'][0]['linked_account_id'] == ident
    response = client.put('/api/quota-collection', json={'enabled': False, 'interval_seconds': 300})
    assert response.json() == {'enabled': False, 'interval_seconds': 300}
    assert client.get('/api/quota-collection').json() == response.json()
    assert client.put('/api/quota-collection', json={'enabled': True, 'interval_seconds': 0}).status_code == 422
    assert client.delete('/api/quota-accounts/' + ident).status_code == 200
    assert client.get('/api/quota-sources/magpie').json()['accounts'][0]['linked_account_id'] is None


def test_linked_query_only_calls_usage_endpoint(source, monkeypatch):
    import httpx
    original = httpx.AsyncClient
    requests = []
    def respond(request):
        requests.append((request.method, str(request.url)))
        assert request.headers['Authorization'] == 'Bearer current-access'
        return httpx.Response(401, json={'error': 'expired'})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    linked = asyncio.run(magpie.link_account('login-a'))
    assert linked['error_code'] == 'auth_required'
    assert requests == [('GET', 'https://chatgpt.com/backend-api/wham/usage')]


def test_manual_query_in_flight_cannot_restore_credentials_after_link(source, monkeypatch):
    quota_store._write({'version': 1, 'accounts': [{
        'id': 'existing', 'provider': 'codex', 'email': 'a@example.test',
        'credential': {'account_id': 'team-a', 'access_token': 'old'},
    }]})
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        async def fetch(provider, raw):
            if json.loads(raw)['access_token'] == 'old':
                started.set()
                await release.wait()
            return result()
        monkeypatch.setattr(quota_providers, 'fetch_account', fetch)
        manual = asyncio.create_task(quota_store.refresh_account('existing'))
        await started.wait()
        await magpie.link_account('login-a')
        release.set()
        await manual
    asyncio.run(scenario())
    assert 'credential' not in quota_store.get_account('existing')
    assert quota_store.get_account('existing')['credential_source'] == 'magpie'


def test_different_queries_share_concurrency_limit(source, monkeypatch):
    rows = json.loads(source.read_text())
    seed = rows[0]
    rows = []
    for i in range(5):
        row = json.loads(json.dumps(seed))
        row['id'] = f'login-{i}'
        row['auth']['tokens']['account_id'] = f'team-{i}'
        rows.append(row)
    source.write_text(json.dumps(rows))
    active, peak = 0, 0
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        async def fetch(provider, raw):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            if active == 3:
                started.set()
            await release.wait()
            active -= 1
            output = result()
            output['identity']['account_id'] = json.loads(raw)['account_id']
            return output
        monkeypatch.setattr(quota_providers, 'fetch_account', fetch)
        tasks = [asyncio.create_task(magpie.link_account(r['id'])) for r in rows]
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.sleep(0)
        assert peak == 3
        release.set()
        await asyncio.gather(*tasks)
    asyncio.run(scenario())
    assert peak == 3


def test_team_members_sharing_workspace_remain_distinct(source, monkeypatch):
    rows = json.loads(source.read_text())
    other = json.loads(json.dumps(rows[0]))
    other.update({'id': 'login-b', 'user': 'b@example.test · Team'})
    rows.append(other)
    source.write_text(json.dumps(rows))
    quota_store._write({'version': 1, 'accounts': [
        {'id': email, 'provider': 'codex', 'email': email,
         'credential': {'access_token': 'old', 'account_id': 'team-a'}}
        for email in ('a@example.test', 'b@example.test')
    ]})
    async def fetch(provider, raw):
        output = result()
        output['identity']['email'] = json.loads(raw)['email']
        return output
    monkeypatch.setattr(quota_providers, 'fetch_account', fetch)
    async def scenario():
        for row in rows:
            account = await magpie.link_account(row['id'])
            assert account['id'] == account['email']
    asyncio.run(scenario())
    accounts = quota_store.list_accounts()
    assert len(accounts) == 2
    assert {a['email'] for a in accounts} == {'a@example.test', 'b@example.test'}
    assert {a['source_account_id'] for a in accounts} == {'login-a', 'login-b'}
