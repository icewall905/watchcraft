"""Kid-safe lock: child profiles use the Hub, the server shows them only kid-checked items."""
import os
import tempfile

os.environ.setdefault('BT_DATA_DIR', tempfile.mkdtemp(prefix='wc-kidsafe-'))

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, select

from bt import db
from bt.db import init_db, session
from bt.hub import ai, kidsafe, providers
from bt.hub import engine as hub
from bt.hub.models import (ContentItem, Curator, CuratorDiscovery, CuratorRun, CuratorSource, ItemState, KidSafeVerdict,
                           Source)
from bt.main import app
from bt.models import Profile


@pytest.fixture
def client():
    with db.engine.begin() as conn:
        conn.exec_driver_sql('DROP TABLE IF EXISTS content_fts')
    SQLModel.metadata.drop_all(db.engine)
    init_db()
    yield TestClient(app, raise_server_exceptions=True)
    SQLModel.metadata.drop_all(db.engine)
    init_db()


def h(p):
    return {'X-Profile-Id': str(p['id'])}


@pytest.fixture
def family(client):
    admin = client.get('/api/profiles').json()[0]
    assert client.patch(f"/api/profiles/{admin['id']}", json={'pin': '4826'}).status_code == 200
    child = client.post('/api/profiles', json={'name': 'Kid', 'role': 'child'}).json()
    return admin, child


def find(pid: int, url: str, title: str = 'A find', **extra) -> int:
    """An item found by one of this profile's curators."""
    with session() as s:
        c = s.exec(select(Curator).where(Curator.profile_id == pid)).first()
        if not c:
            c = Curator(profile_id=pid, name='Finder')
            s.add(c); s.flush()
        item = hub.upsert(s, {'url': url, 'title': title, 'summary': 'A summary about it.', **extra}, 'web_search')
        s.add(CuratorDiscovery(curator_id=c.id, item_id=item.id, profile_id=pid))
        s.commit()
        return item.id


def stamp(iid: int, safe: bool, image: bool = False):
    with session() as s:
        s.add(KidSafeVerdict(item_id=iid, safe=safe, image_checked=image, version=kidsafe.VERSION))
        s.commit()


# ---------- read chokepoint (fail closed)

def test_child_sees_only_items_that_passed_the_kid_safe_check(client, family):
    _, child = family
    unjudged = find(child['id'], 'https://example.org/unjudged', 'Unjudged')
    unsafe = find(child['id'], 'https://example.org/unsafe', 'Unsafe')
    safe = find(child['id'], 'https://example.org/safe', 'Safe')
    stamp(unsafe, False)
    stamp(safe, True)
    for query in ('items', 'items?curation=all', 'items?section=library', 'news?curation=all', 'search?q=example'):
        r = client.get('/api/hub/' + query, headers=h(child))
        assert r.status_code == 200, query
        body = r.json()
        ids = {i['id'] for i in body['items']} if 'items' in body else {i['id'] for g in body['groups'] for i in g['items']}
        assert unjudged not in ids and unsafe not in ids, query
    assert {i['id'] for i in client.get('/api/hub/items', headers=h(child)).json()['items']} == {safe}
    assert client.get(f'/api/hub/items/{unjudged}', headers=h(child)).status_code == 404
    assert client.get(f'/api/hub/items/{unsafe}', headers=h(child)).status_code == 404
    assert client.get(f'/api/hub/items/{safe}', headers=h(child)).status_code == 200
    # Saving an item cannot be used to reach one that failed.
    assert client.post(f'/api/hub/items/{unsafe}/state', json={'saved': True}, headers=h(child)).status_code == 404


def test_stale_verdict_version_hides_items_again(client, family, monkeypatch):
    _, child = family
    iid = find(child['id'], 'https://example.org/old')
    stamp(iid, True)
    monkeypatch.setattr(kidsafe, 'VERSION', kidsafe.VERSION + 1)
    assert client.get(f'/api/hub/items/{iid}', headers=h(child)).status_code == 404


def test_child_never_sees_other_profiles_finds(client, family):
    admin, child = family
    iid = find(admin['id'], 'https://example.org/grown-up', 'Grown-up find')
    stamp(iid, True)  # even a kid-safe item stays with the profile whose curator found it
    assert client.get(f'/api/hub/items/{iid}', headers=h(child)).status_code == 404
    assert client.get('/api/hub/search?q=grown', headers=h(child)).json()['items'] == []
    assert client.post(f'/api/hub/items/{iid}/state', json={'saved': True}, headers=h(child)).status_code == 404


def test_unchecked_pictures_are_withheld_from_children(client, family):
    _, child = family
    iid = find(child['id'], 'https://example.org/pic', image='https://example.org/pic.jpg')
    stamp(iid, True, image=False)
    item = client.get('/api/hub/items', headers=h(child)).json()['items'][0]
    assert item['image'] == '' and item['kid_safe'] is True
    assert client.get(f'/api/hub/items/{iid}/image', headers=h(child)).status_code == 404


def test_set_aside_and_counts_respect_the_lock(client, family):
    _, child = family
    find(child['id'], 'https://example.org/c1')
    assert client.get('/api/hub/counts', headers=h(child)).json()['discover_unread'] == 0
    assert client.get('/api/hub/today/set-aside', headers=h(child)).status_code == 403


# ---------- route gate (default deny)

@pytest.mark.parametrize('method,path,body', [
    ('post', 'items/import', {'url': 'https://example.org/'}),
    ('post', 'search/web', {'q': 'anything'}),
    ('post', 'memes/refresh', {}),
    ('post', 'news/refresh', {}),
    ('get', 'today/set-aside', None),
    ('post', 'items/1/overrule', {'action': 'promote'}),
    ('post', 'items/1/share', {}),
    ('get', 'items/1/share', None),
    ('post', 'scout/runs', {'query': 'x'}),
    ('get', 'curator/preferences', None),
    ('post', 'sources', {'provider': 'rss', 'url': 'https://example.org/feed.xml'}),
    ('post', 'sources', {'provider': 'web_search', 'url': 'https://example.org/'}),
    ('put', 'capture/settings', {}),
])
def test_adult_only_hub_routes_refuse_children(client, family, method, path, body):
    _, child = family
    r = client.request(method, '/api/hub/' + path, json=body, headers=h(child))
    assert r.status_code == 403, r.text


def test_music_stays_grown_up_only(client, family):
    _, child = family
    assert client.get('/api/music/home', headers=h(child)).status_code == 403


def test_every_child_route_is_a_real_route():
    """The allowlist names routes that exist, so a typo cannot silently lock children out."""
    from bt.access import CHILD_HUB_ROUTES
    from bt.hub import api, master_api, moodboards
    real = {(m, r.path[len('/hub'):]) for mod in (api, master_api, moodboards) for r in mod.router.routes for m in r.methods}
    assert CHILD_HUB_ROUTES <= real, CHILD_HUB_ROUTES - real


def test_child_curators_use_kid_providers_only(client, family):
    _, child = family
    wiki = client.post('/api/hub/sources', json={'provider': 'wikipedia'}, headers=h(child))
    assert wiki.status_code == 200
    ok = client.post('/api/hub/curators', json={'name': 'Volcanoes', 'source_ids': [wiki.json()['id']]}, headers=h(child))
    assert ok.status_code == 200 and ok.json()['subject_type'] in ('', 'topic')
    with session() as s:
        hn = Source(profile_id=child['id'], provider='hackernews', url='')
        s.add(hn); s.commit(); s.refresh(hn)
    assert client.post('/api/hub/curators', json={'name': 'HN', 'source_ids': [hn.id]}, headers=h(child)).status_code == 403
    assert client.post('/api/hub/curators', json={'name': 'Person', 'subject_type': 'domain'}, headers=h(child)).status_code == 403
    cid = ok.json()['id']
    assert client.post(f'/api/hub/curators/{cid}/bogus', headers=h(child)).status_code in (403, 404)
    assert client.get(f'/api/hub/curators/{cid}/export', headers=h(child)).status_code == 403


# ---------- providers

def test_web_search_sends_strict_safe_search(monkeypatch):
    seen = {}

    class Response:
        content = b'{}'
        def raise_for_status(self): pass
        def json(self): return {'results': []}

    monkeypatch.setattr(providers, 'get_settings', lambda: {'search': {'provider': 'searxng', 'searxng_url': 'http://searx', 'brave_api_key': ''}})
    monkeypatch.setattr(providers.httpx, 'get', lambda url, params=None, **kw: seen.update(params) or Response())
    providers.PROVIDERS['web_search'].run('', 'cats', {}, safe=True)
    assert seen['safesearch'] == 2
    seen.clear()
    providers.PROVIDERS['web_search'].run('', 'cats', {})
    assert 'safesearch' not in seen

    urls = []
    monkeypatch.setattr(providers, 'get_settings', lambda: {'search': {'provider': 'brave', 'searxng_url': '', 'brave_api_key': 'k'}})
    monkeypatch.setattr(providers.curation, 'fetch', lambda url, **kw: urls.append(url) or (b'{}' if 'brave' in url else b'<rss><channel></channel></rss>'))
    providers.PROVIDERS['web_search'].run('', 'cats', {}, safe=True)
    assert 'safesearch=strict' in urls[0]
    providers.PROVIDERS['bing_news'].run('', 'cats', {}, safe=True)
    assert 'adlt=strict' in urls[1]


def test_safe_mode_refuses_non_kid_providers():
    for key in ('hackernews', 'rss', 'rdap', 'subreddit', 'page_watch', 'github_search'):
        with pytest.raises(ValueError):
            providers.PROVIDERS[key].run('https://example.org/', 'q', {}, safe=True)


def test_child_curator_run_skips_unsafe_sources_and_uses_safe_search(client, family, monkeypatch):
    _, child = family
    calls = []
    with session() as s:
        ws = Source(profile_id=child['id'], provider='web_search', url='')
        feed = Source(profile_id=child['id'], provider='rss', url='https://example.org/feed.xml')
        c = Curator(profile_id=child['id'], name='Cats', queries=['cats'], next_run=0)
        s.add(ws); s.add(feed); s.add(c); s.flush()
        s.add(CuratorSource(curator_id=c.id, source_id=ws.id)); s.add(CuratorSource(curator_id=c.id, source_id=feed.id))
        run = CuratorRun(curator_id=c.id, profile_id=child['id'])
        s.add(run); s.commit(); s.refresh(c); s.refresh(run)
    monkeypatch.setattr(providers.Provider, 'availability', lambda self: (True, ''))
    monkeypatch.setattr(providers.Provider, 'run', lambda self, url, q='', config=None, safe=False: calls.append((self.key, safe)) or [])
    monkeypatch.setattr(hub.polling, 'fetch', lambda *a, **k: pytest.fail('a feed was fetched for a child'))
    hub._execute(c, run)
    assert calls == [('web_search', True)]


# ---------- judge (fail closed)

def _judged(iid):
    with session() as s:
        return s.get(KidSafeVerdict, (iid, ''))


def test_judge_stores_only_explicit_true_as_safe(client, family, monkeypatch):
    _, child = family
    a = find(child['id'], 'https://example.org/a', 'Rainbows')
    b = find(child['id'], 'https://example.org/b', 'Something')
    c = find(child['id'], 'https://example.org/c', 'Unanswered')
    monkeypatch.setattr(ai, 'enabled', lambda: True)
    monkeypatch.setattr(kidsafe, '_vision', lambda: False)
    order = {}
    def call(system, user, schema, **kw):
        assert 'KID-SAFE' in system
        rows = []
        for n, iid in enumerate((x for x in order['ids']), 1):
            if iid == a: rows.append({'n': n, 'kid_safe': True, 'reason': 'fine'})
            if iid == b: rows.append({'n': n, 'kid_safe': 'yes', 'reason': 'string is not true'})
        return {'verdicts': rows}
    with session() as s:
        order['ids'] = [i.id for i in kidsafe.pending(s)]
    monkeypatch.setattr(ai, '_call', call)
    with session() as s:
        out = kidsafe.judge_pending(s)
    assert _judged(a).safe is True
    assert _judged(b).safe is False
    assert _judged(c) is None  # no answer, no verdict: still hidden, judged again next pass
    assert out['skipped'] == 1


def test_judge_model_error_and_no_model_leave_items_hidden(client, family, monkeypatch):
    _, child = family
    iid = find(child['id'], 'https://example.org/err')
    monkeypatch.setattr(ai, 'enabled', lambda: False)
    with session() as s:
        assert kidsafe.judge_pending(s)['stopped'] == 'no model'
    monkeypatch.setattr(ai, 'enabled', lambda: True)
    monkeypatch.setattr(kidsafe, '_vision', lambda: False)
    monkeypatch.setattr(ai, '_call', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('down')))
    with session() as s:
        assert kidsafe.judge_pending(s)['stopped'] == 'model error'
    assert _judged(iid) is None
    assert client.get(f'/api/hub/items/{iid}', headers=h(child)).status_code == 404


def test_memes_need_a_vision_checked_picture(client, family, monkeypatch):
    _, child = family
    iid = find(child['id'], 'https://example.org/meme', 'Funny', type='meme')
    monkeypatch.setattr(ai, 'enabled', lambda: True)
    monkeypatch.setattr(kidsafe, '_vision', lambda: False)
    monkeypatch.setattr(ai, '_call', lambda *a, **k: pytest.fail('a meme was judged without its picture'))
    with session() as s:
        kidsafe.judge_pending(s)
        assert kidsafe.pending(s, vision=False) == []  # waits for vision instead of crowding out text items
        assert [i.id for i in kidsafe.pending(s, vision=True)] == [iid]
    assert _judged(iid) is None


def test_sensitive_items_fail_without_a_model_call(client, family, monkeypatch):
    _, child = family
    iid = find(child['id'], 'https://example.org/cw', meta={'sensitive': True})
    monkeypatch.setattr(ai, 'enabled', lambda: True)
    monkeypatch.setattr(kidsafe, '_vision', lambda: False)
    monkeypatch.setattr(ai, '_call', lambda *a, **k: pytest.fail('no call needed'))
    with session() as s:
        kidsafe.judge_pending(s)
    assert _judged(iid).safe is False


def test_judge_only_reads_what_children_can_reach(client, family):
    admin, child = family
    find(admin['id'], 'https://example.org/adult-only')
    mine = find(child['id'], 'https://example.org/kid')
    with session() as s:
        assert [i.id for i in kidsafe.pending(s)] == [mine]


def test_master_rules_reject_failed_items_for_children(client, family):
    from bt.hub.master import rules
    _, child = family
    iid = find(child['id'], 'https://example.org/r')
    stamp(iid, False)
    with session() as s:
        r = rules.load(s, child['id'])
        assert rules.check(s.get(ContentItem, iid), None, r).reject is True


def test_rank_prompt_carries_the_kid_rule_for_children(client, family, monkeypatch):
    _, child = family
    with session() as s:
        c = Curator(profile_id=child['id'], name='Cats', instructions='cats')
        s.add(c); s.commit(); s.refresh(c)
    systems = []
    monkeypatch.setattr(ai, '_call', lambda system, *a, **k: systems.append(system) or {'scores': []})
    ai.rank(c, [{'type': 'web', 'title': 't', 'url': 'https://example.org/'}])
    assert 'KID-SAFE' in systems[0]


# ---------- review follow-ups

def test_changed_content_drops_the_verdict(client, family):
    from bt.hub.models import CapturedAsset
    _, child = family
    iid = find(child['id'], 'https://example.org/grows')
    stamp(iid, True)
    with session() as s:
        it = s.get(ContentItem, iid)
        it.body = 'A much longer captured article that nobody judged.'
        s.add(it); s.commit()
    assert _judged(iid) is None
    stamp(iid, True, image=True)
    with session() as s:
        s.add(CapturedAsset(item_id=iid, kind='image', mime='image/png', data=b'new'))
        s.commit()
    assert _judged(iid) is None


def test_children_get_only_checked_picture_bytes_and_no_documents(client, family):
    from bt.hub.models import CapturedAsset
    _, child = family
    iid = find(child['id'], 'https://example.org/doc', image='https://example.org/remote.jpg')
    with session() as s:
        s.add(CapturedAsset(item_id=iid, kind='image', mime='image/png', data=b'checked'))
        s.add(CapturedAsset(item_id=iid, kind='document', mime='application/pdf', data=b'%PDF'))
        s.commit()
    stamp(iid, True, image=True)
    assert client.get(f'/api/hub/items/{iid}/assets/document', headers=h(child)).status_code == 404
    r = client.get(f'/api/hub/items/{iid}/image', headers=h(child))
    assert r.status_code == 200 and r.content == b'checked'


def test_team_report_and_curator_list_hide_unchecked_text(client, family, monkeypatch):
    _, child = family
    iid = find(child['id'], 'https://example.org/secret', 'Unchecked headline')
    with session() as s:
        c = s.exec(select(Curator).where(Curator.profile_id == child['id'])).first()
        c.report, c.report_refs = 'Prose about [1].', [iid]
        s.add(c); s.commit()
    team = client.get('/api/hub/team', headers=h(child)).text
    assert 'Unchecked headline' not in team
    assert all(not c['report'] for c in client.get('/api/hub/curators', headers=h(child)).json())
    issue = client.post('/api/hub/scout-report/generate', json={'period': 'daily'}, headers=h(child)).json()
    assert 'Unchecked headline' not in str(issue)


def test_child_cannot_restart_a_paused_person_curator(client, family):
    _, child = family
    with session() as s:
        c = Curator(profile_id=child['id'], name='Someone', subject_type='person', enabled=False)
        s.add(c); s.commit(); s.refresh(c)
    assert client.patch(f'/api/hub/curators/{c.id}', json={'enabled': True}, headers=h(child)).status_code == 403
    assert client.post(f'/api/hub/curators/{c.id}/resume', headers=h(child)).status_code == 403
    assert client.post(f'/api/hub/curators/{c.id}/run', headers=h(child)).status_code == 403


# ---------- per-child safety rails

def test_safety_rails_only_by_grown_ups_and_only_for_children(client, family):
    admin, child = family
    url = f"/api/profiles/{child['id']}/safety-rails"
    assert client.put(url, json={'rails': 'No scary stuff.'}, headers=h(child)).status_code == 403
    r = client.put(url, json={'rails': '  She is 8. No scary stuff.  '}, headers=h(admin))
    assert r.status_code == 200 and r.json()['safety_rails'] == 'She is 8. No scary stuff.'
    assert client.put(f"/api/profiles/{admin['id']}/safety-rails", json={'rails': 'x'}, headers=h(admin)).status_code == 400
    assert client.put(url, json={'rails': 'x' * (kidsafe.RAILS_MAX + 1)}, headers=h(admin)).status_code == 400
    # A child's own profile edit cannot touch the rails (they are not in prefs).
    client.patch(f"/api/profiles/{child['id']}", json={'prefs': {'safety_rails': ''}, 'safety_rails': ''}, headers=h(child))
    with session() as s:
        assert s.get(Profile, child['id']).safety_rails == 'She is 8. No scary stuff.'


def test_each_child_is_gated_by_verdicts_for_its_own_rails(client, family, monkeypatch):
    admin, older = family
    younger = client.post('/api/profiles', json={'name': 'Small', 'role': 'child'}).json()
    client.put(f"/api/profiles/{younger['id']}/safety-rails", json={'rails': 'He is 8. No battles.'}, headers=h(admin))
    battle = find(older['id'], 'https://example.org/battle', 'A famous battle')
    assert find(younger['id'], 'https://example.org/battle', 'A famous battle') == battle
    monkeypatch.setattr(ai, 'enabled', lambda: True)
    monkeypatch.setattr(kidsafe, '_vision', lambda: False)
    systems = []
    def call(system, user, schema, **kw):
        systems.append(system)
        return {'verdicts': [{'n': 1, 'kid_safe': 'No battles' not in system, 'reason': ''}]}
    monkeypatch.setattr(ai, '_call', call)
    with session() as s:
        kidsafe.judge_pending(s)
    assert len(systems) == 2 and sum('He is 8. No battles.' in x for x in systems) == 1
    ids = lambda p: [i['id'] for i in client.get('/api/hub/items?curation=all', headers=h(p)).json()['items']]
    assert battle in ids(older)
    assert battle not in ids(younger)
    with session() as s:
        assert kidsafe.is_safe(s, battle, older['id']) and not kidsafe.is_safe(s, battle, younger['id'])
    # Changing the rails hides the item for that child until it is judged again; the old verdicts go.
    client.put(f"/api/profiles/{younger['id']}/safety-rails", json={'rails': 'He is 8.'}, headers=h(admin))
    with session() as s:
        assert not kidsafe.is_safe(s, battle, younger['id'])
        kidsafe.judge_pending(s)
        assert kidsafe.is_safe(s, battle, younger['id'])
        assert len(s.exec(select(KidSafeVerdict)).all()) == 2


def test_child_rule_and_video_judge_carry_the_rails(client, family):
    admin, child = family
    client.put(f"/api/profiles/{child['id']}/safety-rails", json={'rails': 'Nothing about sharks.'}, headers=h(admin))
    from bt.agent import judge as video_judge, prompts
    from bt.models import Category
    with session() as s:
        assert 'Nothing about sharks.' in kidsafe.child_rule(s, child['id'])
        assert 'Nothing about sharks.' not in kidsafe.child_rule(s, admin['id'])
        assert 'Nothing about sharks.' in kidsafe.household_rails(s)
    cat = Category(profile_id=child['id'], name='Fun')
    assert 'Nothing about sharks.' in prompts.judge_system(cat)
    assert video_judge.decide(cat, {'kid_safe': False, 'score': 99, 'red_flags': [], 'summary': ''}) == (False, 'not kid-safe')
    grown = Category(profile_id=admin['id'], name='Fun')
    assert 'RAILS' not in prompts.judge_system(grown)
    assert video_judge.decide(grown, {'kid_safe': False, 'score': 99, 'red_flags': [], 'summary': 'ok'})[0] is True


def test_verdict_table_migration_keeps_existing_verdicts(client):
    from bt.hub import migrate
    with db.engine.begin() as conn:
        conn.exec_driver_sql('DROP TABLE kidsafeverdict')
        conn.exec_driver_sql('CREATE TABLE kidsafeverdict (item_id INTEGER PRIMARY KEY, safe BOOLEAN, image_checked BOOLEAN, '
                             'reason VARCHAR, version INTEGER, judged_at FLOAT)')
        conn.exec_driver_sql(f"INSERT INTO kidsafeverdict VALUES (7, 1, 1, 'ok', {kidsafe.VERSION}, 1.0)")
        migrate._kidsafe_rules(conn)
        migrate._kidsafe_rules(conn)  # idempotent
    with session() as s:
        v = s.get(KidSafeVerdict, (7, ''))
        assert v and v.safe and v.image_checked
