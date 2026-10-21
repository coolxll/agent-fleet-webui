import pytest

from agent_fleet.services.quota_providers import _codex_window


@pytest.mark.parametrize('used', [None, 'invalid', float('nan'), float('inf'), True])
def test_codex_invalid_usage_is_unknown_instead_of_full(used):
    assert _codex_window({'used_percent': used, 'limit_window_seconds': 18000}, 'primary') is None


@pytest.mark.parametrize('seconds,window', [(18000, '5h'), (604800, 'weekly'), (86400, 'daily'), (3600, 'quota'), (None, 'quota')])
def test_codex_window_uses_reported_period(seconds, window):
    group = _codex_window({'used_percent': 25, 'limit_window_seconds': seconds, 'reset_at': 1793084565}, 'primary')
    assert group['buckets'][0]['window'] == window
    assert group['buckets'][0]['remaining_fraction'] == 0.75
    assert group['buckets'][0]['reset_at'] == '2026-10-27T07:02:45Z'


@pytest.mark.parametrize('reset', [float('inf'), 1e100, 'invalid', True])
def test_codex_invalid_reset_does_not_break_valid_balance(reset):
    bucket = _codex_window({'used_percent': 100, 'limit_window_seconds': 604800, 'reset_at': reset}, 'secondary')['buckets'][0]
    assert bucket['remaining_fraction'] == 0
    assert 'reset_at' not in bucket


def test_codex_additional_feature_limits_are_not_dropped(monkeypatch):
    import asyncio
    import httpx
    from agent_fleet.services.quota_providers import _fetch_codex

    def window(used, seconds):
        return {'used_percent': used, 'limit_window_seconds': seconds, 'reset_at': 1793084565}
    original = httpx.AsyncClient
    payload = {
        'email': 'test@example.com',
        'rate_limit': {'primary_window': window(10, 18000), 'secondary_window': window(20, 604800)},
        'additional_rate_limits': [{'limit_name': 'Spark', 'metered_feature': 'spark',
                                    'rate_limit': {'primary_window': window(90, 18000)}}],
        'code_review_rate_limit': {'secondary_window': window(40, 604800)},
    }
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload)), **kw))
    account = asyncio.run(_fetch_codex({'access_token': 'test-token'}))['quota']['accounts'][0]
    assert [g['name'] for g in account['groups']] == ['5-Hour limit', 'Weekly limit', 'Code review', 'Spark']
    assert account['min_remaining_fraction'] == 0.1
