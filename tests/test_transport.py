import requests
import pytest
import search_transport as transport


def test_rate_limit_cooldown_is_shared_and_never_rotates_on_429(monkeypatch):
    transport._cooldowns.clear()
    calls = []
    def request(*args, **kwargs):
        calls.append(kwargs)
        response = requests.Response()
        response.status_code = 429
        response.headers['Retry-After'] = '2'
        return response
    monkeypatch.setattr(requests.Session, 'request', request)
    session = transport.SearchSession()
    session.get('https://limit.example/search')
    with pytest.raises(requests.RequestException, match='cooldown'):
        session.get('https://limit.example/search')
    assert len(calls) == 1
    transport._cooldowns.clear()


def test_deadline_passed_to_each_request(monkeypatch):
    calls = []
    monkeypatch.setattr(requests.Session, 'request', lambda *a, **kw: calls.append(kw) or requests.Response())
    transport.with_deadline(lambda: transport.SearchSession().get('https://example.com', timeout=12), transport.time.monotonic() + .3)
    assert 0 < calls[0]['timeout'] <= .3


def test_retry_after_http_date_is_respected(monkeypatch):
    from datetime import datetime, timezone, timedelta
    from email.utils import format_datetime
    transport._cooldowns.clear()
    response = requests.Response()
    response.status_code = 503
    response.headers['Retry-After'] = format_datetime(datetime.now(timezone.utc) + timedelta(minutes=10), usegmt=True)
    monkeypatch.setattr(requests.Session, 'request', lambda *a, **kw: response)
    transport.SearchSession().get('https://date.example/search')
    assert transport._cooldowns['date.example'] - transport.time.monotonic() > 590
    transport._cooldowns.clear()
