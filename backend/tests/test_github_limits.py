"""GitHub's anonymous quotas are paced and explained instead of surfacing a bare 403."""
import pytest

from bt import curation
from bt.hub import providers


class Response:
    def __init__(self, headers):
        self.headers = headers

    def getheader(self, key, default=''):
        return self.headers.get(key, default)


def test_retry_hint_reads_githubs_rate_limit_reset(monkeypatch):
    monkeypatch.setattr(curation.time, 'time', lambda: 1000)
    assert curation._retry_hint(Response({'x-ratelimit-remaining': '0', 'x-ratelimit-reset': '1042'})) == '42'
    assert curation._retry_hint(Response({'Retry-After': '120'})) == '120'
    # A 403 that is not a quota answer carries no hint, so the caller keeps the plain error.
    assert curation._retry_hint(Response({'x-ratelimit-remaining': '5'})) == ''
    assert curation._retry_hint(Response({})) == ''


def test_github_search_spaces_requests_to_stay_under_the_anonymous_limit(monkeypatch):
    clock = {'t': 1000.0}
    slept = []

    def sleep(seconds):
        slept.append(round(seconds, 2))
        clock['t'] += seconds

    monkeypatch.setattr(providers.time, 'monotonic', lambda: clock['t'])
    monkeypatch.setattr(providers.time, 'sleep', sleep)
    monkeypatch.setattr(providers, '_github_last', 0.0)
    monkeypatch.setattr(curation, 'fetch', lambda url, **kwargs: b'{"items": []}')

    providers.github_search('', 'first')
    assert slept == []  # nothing to wait for on the first call
    providers.github_search('', 'second')
    assert slept == [providers.GITHUB_SEARCH_GAP_S]


def test_github_search_explains_a_rate_limit(monkeypatch):
    monkeypatch.setattr(providers, '_github_pace', lambda: None)

    def fetch(url, **kwargs):
        raise curation.FetchError(403, '42')

    monkeypatch.setattr(curation, 'fetch', fetch)
    with pytest.raises(ValueError, match=r'GitHub rate limit reached: 10 searches a minute.*resets in 42s'):
        providers.github_search('', 'q')


def test_github_releases_explains_a_rate_limit(monkeypatch):
    def fetch(url, **kwargs):
        raise curation.FetchError(403, '900')

    monkeypatch.setattr(curation, 'fetch', fetch)
    with pytest.raises(ValueError, match=r'GitHub rate limit reached: 60 requests an hour.*resets in 900s'):
        providers.github_releases('https://github.com/owner/repository')


def test_a_403_without_a_reset_keeps_its_plain_error(monkeypatch):
    monkeypatch.setattr(providers, '_github_pace', lambda: None)

    def fetch(url, **kwargs):
        raise curation.FetchError(403, '')

    monkeypatch.setattr(curation, 'fetch', fetch)
    with pytest.raises(curation.FetchError, match='HTTP 403'):
        providers.github_search('', 'q')
