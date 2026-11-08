"""Master curator P5: the edition, set-aside review, overrules, stories, explanations and curated feeds."""
import time

from sqlmodel import select

from test_curation import client  # noqa: F401  (isolated DB/API fixture)

from bt.db import session
from bt.hub.master import edition
from bt.hub.models import ContentItem, CuratorDiscovery, Edition, ItemState, ItemVerdict, MemeExample, Story, StoryItem, Topic

_n = iter(range(1, 10_000))


def add(type='news', tier='feature', final=70.0, pid=1, story=None, title=None, publisher='', verdict=True):
    with session() as s:
        n = next(_n)
        it = ContentItem(url=f'https://example.org/{n}', type=type, title=title or f'Item {n}', publisher=publisher or f'pub{n}')
        s.add(it); s.flush()
        s.add(CuratorDiscovery(curator_id=1, item_id=it.id, profile_id=pid, score=60))
        if type == 'meme':
            s.add(MemeExample(item_id=it.id))
        if story:
            s.add(StoryItem(item_id=it.id, story_id=story))
        if verdict:
            s.add(ItemVerdict(profile_id=pid, item_id=it.id, tier=tier, final=final, reasons=['has a summary']))
        s.commit()
        return it.id


def new_story():
    with session() as s:
        st = Story(title='x'); s.add(st); s.commit()
        return st.id


def other_profile(client):
    p = client.post('/api/profiles', json={'name': 'Other'}).json()
    return p['id'], {'X-Profile-Id': str(p['id'])}


def entries(today, key):
    return [e['item']['id'] for sec in today['sections'] if sec['key'] == key for e in sec['entries']]


# ---------- today

def test_today_renders_the_edition_and_remembers_what_was_shown(client):
    st = new_story()
    lead = add(story=st, final=90)
    meme = add(type='meme')
    add(tier='set_aside', final=30)
    today = client.get('/api/hub/today').json()
    assert entries(today, 'lead') == [lead] and entries(today, 'memes') == [meme]
    assert today['set_aside'] == 1 and today['curated'] is True
    lead_entry = today['sections'][0]['entries'][0]
    assert lead_entry['item']['verdict']['tier'] == 'feature' and lead_entry['sources'] == 1
    with session() as s:
        assert set(s.exec(select(Edition)).one().shown_item_ids) == {lead, meme}
    # A better story arriving later does not push out what the reader already saw.
    better = add(story=new_story(), final=99)
    with session() as s:
        edition.patch(s, 1)
    again = client.get('/api/hub/today').json()
    assert entries(again, 'lead') == [lead] and entries(again, 'stories') == [better]


def test_citations_resolve_only_to_items_the_profile_can_see(client):
    mine = add()
    pid, _ = other_profile(client)
    theirs = add(pid=pid)
    with session() as s:
        ed = edition.patch(s, 1)
        ed.intro, ed.intro_refs = 'Quiet day [1]. Elsewhere [2].', [mine, theirs]
        s.add(ed); s.commit()
    refs = client.get('/api/hub/today').json()['intro_refs']
    # Every GET now reconciles the edition, dropping prose whose sources no longer belong.
    assert refs == []
    from bt.hub.master_api import _refs
    assert _refs({mine: {'title': 'Mine', 'url': 'https://example.org/mine'}}, [mine, theirs])[1] == {'n': 2, 'item_id': None}


# ---------- set aside and overrules

def test_set_aside_lists_what_was_left_out_with_reasons(client):
    weak = add(tier='set_aside', final=30)
    meme = add(type='meme', tier='set_aside', final=20)
    add(tier='reject', final=0)
    got = client.get('/api/hub/today/set-aside').json()
    assert got['total'] == 2 and [i['id'] for i in got['items']] == [meme, weak]
    assert got['items'][1]['verdict']['reasons'] == ['has a summary']
    assert [i['id'] for i in client.get('/api/hub/today/set-aside', params={'type': 'meme'}).json()['items']] == [meme]


def test_overrules_move_items_on_and_off_the_front_page(client):
    weak = add(type='meme', tier='set_aside', final=30)
    shown = add(type='meme', final=80)
    r = client.post(f'/api/hub/items/{weak}/overrule', json={'action': 'promote'}).json()
    assert r['tier'] == 'feature' and r['overruled'] == 'promoted'
    assert weak in entries(client.get('/api/hub/today').json(), 'memes')
    client.post(f'/api/hub/items/{shown}/overrule', json={'action': 'demote'})
    assert entries(client.get('/api/hub/today').json(), 'memes') == [weak]
    cleared = client.post(f'/api/hub/items/{shown}/overrule', json={'action': 'clear'}).json()
    assert cleared['overruled'] == '' and client.post(f'/api/hub/items/{shown}/overrule', json={'action': 'nope'}).status_code == 422


def test_other_profiles_cannot_see_or_overrule_my_items(client):
    mine = add()
    _, other = other_profile(client)
    assert client.post(f'/api/hub/items/{mine}/overrule', json={'action': 'promote'}, headers=other).status_code == 404
    assert client.get(f'/api/hub/items/{mine}/why', headers=other).status_code == 404
    why = client.get(f'/api/hub/items/{mine}/why').json()
    assert why['verdict']['reasons'] == ['has a summary'] and why['story'] is None


def test_a_story_shows_only_this_profiles_reports(client):
    st = new_story()
    a = add(story=st, final=90)
    b = add(story=st, final=60, tier='keep')
    add(story=st, tier='reject')
    pid, other = other_profile(client)
    add(story=st, pid=pid)
    got = client.get(f'/api/hub/stories/{st}').json()
    assert [i['id'] for i in got['items']] == [a, b]
    assert client.get(f'/api/hub/items/{a}/why').json()['story'] == {'id': st, 'sources': 2}
    assert client.get(f'/api/hub/stories/{new_story()}').status_code == 404


def test_story_view_matches_curated_news_visibility_and_muted_topics(client):
    st = new_story()
    kept = add(story=st, title='Community report remains visible')
    unjudged = add(story=st, title='New unjudged report', verdict=False)
    aside = add(story=st, title='Set aside report', tier='set_aside')
    dismissed = add(story=st, title='Dismissed report')
    client.post(f'/api/hub/items/{dismissed}/state', json={'dismissed': True})
    add(story=st, title='MutedMarker report')
    wrong_type = add(type='web', story=st, title='A non-news report')
    with session() as s:
        touched = ContentItem(url='https://example.org/touched-only', type='news', title='Touched-only report')
        s.add(touched); s.flush()
        s.add(StoryItem(item_id=touched.id, story_id=st))
        s.add(ItemState(profile_id=1, item_id=touched.id, saved=True))
        s.commit()
    with session() as s:
        topic = Topic(profile_id=1, name='Muted', kind='news', muted=True, keywords=['mutedmarker'])
        s.add(topic); s.commit()
    other_profile_id, other_headers = other_profile(client)
    private = add(story=st, pid=other_profile_id, title='Private profile source')
    got = client.get(f'/api/hub/stories/{st}')
    assert got.status_code == 200
    assert {i['id'] for i in got.json()['items']} == {kept, unjudged}
    assert {i['id'] for g in client.get('/api/hub/news?days=30').json()['groups'] for i in g['items']} == {kept, unjudged}
    assert not {private, aside, dismissed, wrong_type, touched.id} & {i['id'] for i in got.json()['items']}
    assert client.get(f'/api/hub/stories/{st}', headers=other_headers).json()['items'][0]['id'] == private


# ---------- curated feeds

def test_feeds_show_what_the_curator_kept_and_all_on_request(client):
    kept = add(tier='keep', title='Kept story about bridges')
    feat = add(tier='feature', title='Featured story about bridges')
    aside = add(tier='set_aside', title='Weak story about bridges')
    ids = lambda **p: {i['id'] for i in client.get('/api/hub/items', params=p).json()['items']}  # noqa: E731
    assert ids() == {kept, feat}
    assert ids(curation='all') == {kept, feat, aside}
    news = client.get('/api/hub/news').json()['groups']
    assert {i['id'] for g in news for i in g['items']} == {kept, feat}
    # Saved items are the reader's own and are never filtered.
    client.post(f'/api/hub/items/{aside}/state', json={'saved': True})
    assert aside in ids(section='library')


def test_news_prefers_stored_story_links_and_keeps_sources_profile_scoped(client):
    st = new_story()
    first = add(story=st, title='Completely different headline alpha', publisher='One')
    second = add(story=st, title='Another unrelated wording beta', publisher='Two')
    pid, headers = other_profile(client)
    private = add(story=st, pid=pid, title='Private report gamma', publisher='Private')
    groups = client.get('/api/hub/news?days=30').json()['groups']
    group = next(g for g in groups if g.get('story_id') == st)
    assert {i['id'] for i in group['items']} == {first, second}
    assert all(i['publisher'] != 'Private' for i in group['items'])
    other_groups = client.get('/api/hub/news?days=30', headers=headers).json()['groups']
    assert {i['id'] for g in other_groups for i in g['items']} == {private}


def test_feeds_are_unfiltered_before_the_first_pass(client):
    a, b = add(verdict=False), add(verdict=False)
    assert {i['id'] for i in client.get('/api/hub/items').json()['items']} == {a, b}


def test_meme_feed_is_curated(client):
    good = add(type='meme', tier='feature')
    add(type='meme', tier='set_aside')
    assert [i['id'] for i in client.get('/api/hub/memes/items').json()['items']] == [good]
    assert len(client.get('/api/hub/memes/items', params={'curation': 'all'}).json()['items']) == 2


def test_badges_count_only_featured_unread_items(client):
    add(tier='feature')
    add(tier='keep')
    add(tier='set_aside')
    counts = client.get('/api/hub/counts').json()
    assert counts['discover_unread'] == 1 and counts['news_today'] == 1


# ---------- preferences

def test_preferences_can_be_listed_added_edited_and_removed(client):
    assert client.get('/api/hub/curator/preferences').json() == {'summary': '', 'built_at': None, 'lessons': []}
    l = client.post('/api/hub/curator/preferences', json={'text': 'Fewer reaction memes', 'scope': 'meme', 'polarity': -1}).json()
    assert l['user_edited'] and l['polarity'] == -1
    assert client.post('/api/hub/curator/preferences', json={'text': 'x'}).status_code == 422
    edited = client.patch(f"/api/hub/curator/preferences/{l['id']}", json={'active': False}).json()
    assert edited['active'] is False
    _, other = other_profile(client)
    assert client.delete(f"/api/hub/curator/preferences/{l['id']}", headers=other).status_code == 404
    assert client.delete(f"/api/hub/curator/preferences/{l['id']}").json() == {'ok': True}
    assert client.get('/api/hub/curator/preferences').json()['lessons'] == []


# ---------- review regressions

def test_unjudged_items_stay_in_the_feed_and_clear_needs_no_verdict(client):
    a, b, c = add(verdict=False), add(verdict=False), add(verdict=False)
    assert client.post(f'/api/hub/items/{a}/overrule', json={'action': 'clear'}).json() is None
    add(tier='set_aside')
    assert {i['id'] for i in client.get('/api/hub/items').json()['items']} == {a, b, c}


def test_preference_polarity_must_be_a_direction(client):
    assert client.post('/api/hub/curator/preferences', json={'text': 'More long reads', 'polarity': 0}).status_code == 422
    l = client.post('/api/hub/curator/preferences', json={'text': 'More long reads'}).json()
    assert client.patch(f"/api/hub/curator/preferences/{l['id']}", json={'polarity': 0}).status_code == 422
    assert client.patch(f"/api/hub/curator/preferences/{l['id']}", json={'polarity': -1}).json()['polarity'] == -1


def test_verdicts_for_items_no_longer_visible_are_ignored(client):
    gone = add(tier='set_aside')
    featured_gone = add(tier='feature')
    with session() as s:
        for d in s.exec(select(CuratorDiscovery)).all():
            s.delete(d)
        s.commit()
    assert client.get('/api/hub/today/set-aside').json() == {'items': [], 'total': 0}
    assert client.get('/api/hub/counts').json()['discover_unread'] == 0
    today = client.get('/api/hub/today').json()
    assert today['set_aside'] == 0 and not any(sec.get('entries') for sec in today['sections'])
    assert gone and featured_gone


def test_a_racing_first_request_of_the_day_does_not_fail(client, monkeypatch):
    from sqlalchemy.exc import IntegrityError
    from bt.hub import master_api
    add()
    real = edition.patch
    def racing(s, pid, now=None, run_id=''):
        with session() as other:
            real(other, pid, now)  # someone else builds it first
        raise IntegrityError('insert', {}, Exception('UNIQUE constraint failed'))
    monkeypatch.setattr(master_api.edition, 'patch', racing)
    assert client.get('/api/hub/today').status_code == 200


def test_manual_rerun_is_profile_scoped_durable_and_idempotent(client):
    from bt.hub.models import MasterQueue
    first = client.post('/api/hub/today/rerun')
    assert first.status_code == 200
    state = first.json()
    assert state['requested_at'] > state['completed_at']
    assert client.post('/api/hub/today/rerun').json() == state
    with session() as s:
        queue = s.get(MasterQueue, 1)
        assert queue and queue.due_at <= time.time()
        assert queue.gen == 1
    assert client.get('/api/hub/today').json()['rerun'] == state
    _, headers = other_profile(client)
    assert client.get('/api/hub/today', headers=headers).json()['rerun'] == {}


def test_manual_rerun_respects_disabled_master(client):
    from bt.db import get_settings, save_setting
    settings = get_settings()['hub_master']
    save_setting('hub_master', {**settings, 'enabled': False})
    assert client.post('/api/hub/today/rerun').status_code == 409


def test_news_week_supports_large_curated_collection_without_leaking_hidden_items(client):
    ids = {add(story=new_story(), tier='keep', title=f'Report on subject {n}') for n in range(135)}
    older = next(iter(ids))
    with session() as s:
        item = s.get(ContentItem, older)
        item.published_at = time.time() - 4 * 86400
        s.add(item); s.commit()
    hidden = add(tier='reject')
    aside = add(tier='set_aside')
    other = add(pid=999)
    response = client.get('/api/hub/news').json()
    shown = {i['id'] for g in response['groups'] for i in g['items']}
    assert shown == ids
    assert not shown & {hidden, aside, other}
    assert response['article_count'] == 135
    assert response['story_count'] == 135
    assert response['target_articles'] == 100
    assert older not in {i['id'] for g in client.get('/api/hub/news?days=1').json()['groups'] for i in g['items']}


def test_today_expands_existing_edition_from_kept_articles_without_losing_delivered_lead(client):
    lead = add(story=new_story(), final=90)
    first = client.get('/api/hub/today').json()
    assert entries(first, 'lead') == [lead]
    kept = {add(story=new_story(), tier='keep', title=f'Kept report {n}') for n in range(90)}
    expanded = client.get('/api/hub/today').json()
    assert entries(expanded, 'lead') == [lead]
    assert len(entries(expanded, 'stories')) == 79
    assert set(entries(expanded, 'stories')).issubset(kept)
    again = client.get('/api/hub/today').json()
    assert entries(again, 'stories') == entries(expanded, 'stories')


def test_today_preview_does_not_consume_hidden_entries_until_expanded(client):
    for n in range(90):
        add(story=new_story(), tier='keep', title=f'Preview story {n}')
    result = client.get('/api/hub/today?preview=true').json()
    assert len(entries(result, 'stories')) == 79
    initial = set(entries(result, 'lead') + entries(result, 'stories')[:8])
    with session() as s:
        assert set(s.exec(select(Edition)).one().shown_item_ids) == initial
    expanded = client.get('/api/hub/today?preview=true&stories=36').json()
    with session() as s:
        assert set(s.exec(select(Edition)).one().shown_item_ids) == set(entries(expanded, 'lead') + entries(expanded, 'stories')[:36])
