"""Shared feed fetches retain independent curator/profile decisions and recover leases."""
import hashlib
import os
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

os.environ.setdefault('BT_DATA_DIR', tempfile.mkdtemp(prefix='wc-shared-poll-tests-'))

import pytest
from sqlalchemy import text
from sqlmodel import SQLModel, select
from fastapi.testclient import TestClient

from bt.db import engine, init_db, session
from bt.hub import ai, engine as hub, polling, providers
from bt.hub.models import SourcePoll
from bt.main import app


@pytest.fixture
def client(monkeypatch):
    with engine.begin() as conn:
        conn.exec_driver_sql('DROP TABLE IF EXISTS content_fts')
    SQLModel.metadata.drop_all(engine)
    init_db()
    monkeypatch.setattr(ai, 'enabled', lambda: False)
    monkeypatch.setattr(hub, 'start', lambda cid: None)
    yield TestClient(app)
    SQLModel.metadata.drop_all(engine)


def test_two_curators_share_fetch_but_score_and_discover_separately(client, monkeypatch):
    calls = []
    def fetch(url, _q):
        calls.append(url)
        return [{'url': 'https://example.org/python', 'title': 'Python release', 'type': 'news'},
                {'url': 'https://example.org/rust', 'title': 'Rust release', 'type': 'news'}]
    monkeypatch.setattr(providers.PROVIDERS['rss'], 'fetch', fetch)
    first_source = client.post('/api/hub/sources', json={'provider': 'rss', 'url': 'https://example.org/feed'}).json()
    first = client.post('/api/hub/curators', json={'name': 'Python', 'source_ids': [first_source['id']], 'keywords': ['Python']}).json()
    other = client.post('/api/profiles', json={'name': 'Other'}).json()
    headers = {'X-Profile-Id': str(other['id'])}
    second_source = client.post('/api/hub/sources', headers=headers, json={'provider': 'rss', 'url': 'https://example.org/feed'}).json()
    second = client.post('/api/hub/curators', headers=headers, json={'name': 'Rust', 'source_ids': [second_source['id']], 'keywords': ['Rust']}).json()
    assert hub.run(first['id']).new_items == 1
    assert hub.run(second['id']).new_items == 1
    assert calls == ['https://example.org/feed']
    assert client.get('/api/hub/items').json()['items'][0]['title'] == 'Python release'
    assert client.get('/api/hub/items', headers=headers).json()['items'][0]['title'] == 'Rust release'
    check = client.get(f"/api/hub/sources/{second_source['id']}/checks", headers=headers).json()[0]
    assert check['trigger'] == 'shared'
    first_check = client.get(f"/api/hub/sources/{first_source['id']}/checks").json()[0]
    assert check['checked_at'] == first_check['checked_at']
    assert client.post(f"/api/hub/sources/{second_source['id']}/test", headers=headers).json()['ok']
    assert len(calls) == 2  # An explicit test always fetches again.


def test_concurrent_pollers_fetch_once_and_cache_survives_a_new_process(client):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def fetch(url, _q):
        calls.append(url); entered.set()
        assert release.wait(5)
        return [{'url': 'https://example.org/item', 'title': 'A public item'}]
    provider = providers.Provider('rss', 'RSS', 'feed', '', fetch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(polling.fetch, provider, 'https://example.org/feed', str)
        assert entered.wait(5)
        second = pool.submit(polling.fetch, provider, 'https://example.org/feed', str)
        release.set()
        a, b = first.result(timeout=10), second.result(timeout=10)
    assert len(calls) == 1 and sorted([a.shared, b.shared]) == [False, True]
    script = '''
from bt.hub import polling, providers
def fail(*args): raise AssertionError('Should have reused the persisted fetch')
p=providers.Provider('rss','RSS','feed','',fail)
r=polling.fetch(p,'https://example.org/feed',str)
assert r.shared and not r.error and r.items[0]['title']=='A public item'
'''
    subprocess.run([sys.executable, '-c', script],
                   env={'BT_DATA_DIR': str(Path(engine.url.database).parent), 'PATH': os.defpath},
                   check=True, timeout=20)


def test_failure_backoff_forced_retry_and_abandoned_lease_recovery(client):
    calls = []
    def fetch(url, _q):
        calls.append(url)
        if len(calls) == 1: raise ValueError('Temporarily unavailable')
        return [{'url': 'https://example.org/item', 'title': 'Recovered'}]
    provider = providers.Provider('rss', 'RSS', 'feed', '', fetch)
    first = polling.fetch(provider, 'https://example.org/feed', str)
    assert first.error and not first.shared
    cached = polling.fetch(provider, 'https://example.org/feed', str)
    assert cached.shared and cached.error and len(calls) == 1
    assert not polling.fetch(provider, 'https://example.org/feed', str, force=True).error
    assert len(calls) == 2
    key = hashlib.sha256(b'rss\0https://example.org/abandoned').hexdigest()
    with session() as s:
        s.add(SourcePoll(key=key, lease_token='dead-process', lease_until=time.time() - 1)); s.commit()
    assert not polling.fetch(provider, 'https://example.org/abandoned', str).error
    with session() as s:
        assert s.get(SourcePoll, key).lease_token == ''


def test_expired_success_is_refetched_and_capacity_is_bounded(client, monkeypatch):
    calls = []
    provider = providers.Provider('rss', 'RSS', 'feed', '', lambda url, _q: calls.append(url) or [])
    polling.fetch(provider, 'https://example.org/feed', str)
    with session() as s:
        row = s.exec(select(SourcePoll)).first()
        row.fetched_at -= polling.FRESH_SECONDS + 1; s.add(row); s.commit()
    assert not polling.fetch(provider, 'https://example.org/feed', str).shared
    monkeypatch.setattr(polling, 'MAX_ENTRIES', 2)
    for i in range(4): polling.fetch(provider, f'https://example.org/{i}', str)
    with session() as s:
        assert len(s.exec(select(SourcePoll)).all()) == 2


def test_slow_fetch_renews_lease_and_interruption_releases_it(client, monkeypatch):
    monkeypatch.setattr(polling, 'LEASE_SECONDS', 0.3)
    entered, release = threading.Event(), threading.Event()
    calls = []
    def slow(url, _q):
        calls.append(url); entered.set()
        assert release.wait(5)
        return []
    provider = providers.Provider('rss', 'RSS', 'feed', '', slow)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(polling.fetch, provider, 'https://example.org/slow', str)
        assert entered.wait(5)
        time.sleep(0.6)  # Longer than the original lease, while the fetch remains alive.
        with session() as s:
            row = s.exec(select(SourcePoll)).first()
            assert row.lease_until > time.time()
        second = pool.submit(polling.fetch, provider, 'https://example.org/slow', str)
        release.set()
        assert not first.result(timeout=10).shared
        assert second.result(timeout=10).shared
    assert len(calls) == 1
    class Interrupted(BaseException):
        pass
    def interrupted(*args):
        raise Interrupted()
    provider.fetch = interrupted
    with pytest.raises(Interrupted):
        polling.fetch(provider, 'https://example.org/interrupted', str)
    with session() as s:
        assert all(not row.lease_token for row in s.exec(select(SourcePoll)).all())


def test_oversize_result_is_not_stored_and_query_providers_are_excluded(client, monkeypatch):
    monkeypatch.setattr(polling, 'MAX_RESPONSE_BYTES', 20)
    provider = providers.Provider('rss', 'RSS', 'feed', '', lambda *_: [{'title': 'x' * 100}])
    result = polling.fetch(provider, 'https://example.org/large', str)
    assert result.error and result.items == []
    with session() as s:
        assert s.exec(select(SourcePoll)).first().items == []
    with pytest.raises(ValueError, match='feed providers'):
        polling.fetch(providers.PROVIDERS['github_search'], '', str)
