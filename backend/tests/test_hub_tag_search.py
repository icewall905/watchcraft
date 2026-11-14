"""Free-text item search also matches source tags and the profile's own tags."""
from test_curation import client, make_curator  # noqa: F401  (isolated DB/API fixture)

from bt.db import session
from bt.hub import engine
from bt.hub.models import ContentItem, CuratorDiscovery, ItemState


def _discover(curator, rows):
    """Insert items with their FTS entry and a discovery row for profile 1."""
    ids = {}
    with session() as s:
        for key, item in rows:
            s.add(item)
            s.flush()
            engine.index(s, item)
            s.add(CuratorDiscovery(curator_id=curator['id'], item_id=item.id, profile_id=1, query='q', score=50))
            ids[key] = item.id
        s.commit()
    return ids


def test_search_matches_source_tags_without_the_word_in_the_text(client):
    curator = make_curator(client, [{'provider': 'rss', 'url': 'https://example.org/feed'}])
    ids = _discover(curator, [
        ('tagged', ContentItem(url='https://example.org/a', type='news', title='Local servers',
                               summary='A guide', meta={'tags': ['selfhosted', 'linux']})),
        ('plain', ContentItem(url='https://example.org/b', type='news', title='Rust release', summary='')),
    ])
    found = client.get('/api/hub/items', params={'section': 'discover', 'q': 'selfhosted'}).json()
    assert [i['id'] for i in found['items']] == [ids['tagged']]
    # A prefix of a tag matches too, so typing narrows as the user types.
    assert [i['id'] for i in client.get('/api/hub/items', params={'q': 'selfho'}).json()['items']] == [ids['tagged']]
    # Text search still works and is not replaced by tag matching.
    assert [i['id'] for i in client.get('/api/hub/items', params={'q': 'rust'}).json()['items']] == [ids['plain']]


def test_search_matches_the_profiles_own_tags(client):
    curator = make_curator(client, [{'provider': 'rss', 'url': 'https://example.org/feed'}])
    ids = _discover(curator, [
        ('tagged', ContentItem(url='https://example.org/a', type='news', title='Local servers',
                               summary='', meta={'tags': ['selfhosted']})),
        ('mine', ContentItem(url='https://example.org/c', type='news', title='Unrelated story', summary='')),
    ])
    with session() as s:
        s.add(ItemState(profile_id=1, item_id=ids['mine'], tags=['selfhosted']))
        s.commit()
    found = client.get('/api/hub/items', params={'section': 'discover', 'q': 'selfhosted'}).json()
    assert sorted(i['id'] for i in found['items']) == sorted([ids['tagged'], ids['mine']])


def test_negated_terms_do_not_match_tags(client):
    curator = make_curator(client, [{'provider': 'rss', 'url': 'https://example.org/feed'}])
    ids = _discover(curator, [
        ('tagged', ContentItem(url='https://example.org/a', type='news', title='Local servers',
                               summary='', meta={'tags': ['selfhosted']})),
        ('other', ContentItem(url='https://example.org/b', type='news', title='Local servers elsewhere', summary='')),
    ])
    found = client.get('/api/hub/items', params={'section': 'discover', 'q': 'local -selfhosted'}).json()
    assert [i['id'] for i in found['items']] == [ids['other']]


def test_tag_terms_ignores_phrases_and_negations():
    from bt.hub.api import tag_terms
    assert tag_terms('"machine learning" rust -java') == ['rust']
    assert tag_terms('"machine learning" rust -java', negated=True) == ['java']
    assert tag_terms('') == []
    assert tag_terms('aa bb cc dd ee ff gg hh') == ['aa', 'bb', 'cc', 'dd', 'ee', 'ff']


def test_feedback_learns_the_reason_the_person_typed(client):
    """A meme whose title has nothing distinctive still teaches the curator when the person says why."""
    curator = make_curator(client, [{'provider': 'rss', 'url': 'https://example.org/feed'}])
    ids = _discover(curator, [
        ('blank', ContentItem(url='https://example.org/m', type='meme', title='😂😂😂', summary='')),
    ])
    path = f"/api/hub/items/{ids['blank']}/curator-feedback"
    # Nothing in the title to learn from, and no reason given.
    assert client.post(path, json={'curator_id': curator['id'], 'verdict': 'less'}).status_code == 422
    # The person supplies the reason instead.
    r = client.post(path, json={'curator_id': curator['id'], 'verdict': 'less', 'terms': ['  Spongebob  ']})
    assert r.status_code == 200
    assert r.json()['added'] == ['spongebob']
    assert r.json()['tuned_down'] == ['spongebob']


def test_feedback_terms_are_bounded_and_deduplicated(client):
    from bt.hub.api import _clean_terms
    assert _clean_terms(['  Spongebob ', 'spongebob', 'a', 'x' * 80]) == ['spongebob', 'x' * 40]
    assert _clean_terms(None) == []


def test_hiding_a_language_keeps_it_out_of_the_meme_feed(client):
    """A thumbs-down reason of "German" must actually stop German memes, not just rank them lower."""
    from bt.hub.models import MemeExample
    curator = make_curator(client, [{'provider': 'rss', 'url': 'https://example.org/feed'}])
    ids = _discover(curator, [
        ('de', ContentItem(url='https://example.org/de', type='meme', title='Ein Witz', summary='', meta={'tags': ['memes']})),
        ('en', ContentItem(url='https://example.org/en', type='meme', title='A joke', summary='', meta={'tags': ['memes']})),
    ])
    with session() as s:
        s.add(MemeExample(item_id=ids['de'], language='de', has_media=True))
        s.add(MemeExample(item_id=ids['en'], language='en', has_media=True))
        s.commit()

    def feed():
        return [i['id'] for i in client.get('/api/hub/memes/items?view=examples').json()['items']]

    assert sorted(feed()) == sorted([ids['de'], ids['en']])
    assert client.post('/api/hub/memes/languages/de/hide', json={'hidden': True}).json()['hidden_languages'] == ['de']
    assert feed() == [ids['en']]
    # Undo puts it back.
    assert client.post('/api/hub/memes/languages/de/hide', json={'hidden': False}).json()['hidden_languages'] == []
    assert sorted(feed()) == sorted([ids['de'], ids['en']])


def test_hiding_a_language_rejects_nonsense_codes(client):
    assert client.post('/api/hub/memes/languages/not a code/hide', json={'hidden': True}).status_code == 422


def test_language_detection_fills_in_what_the_source_left_blank():
    from bt.hub.memes import detect_language
    assert detect_language('Ein Witz über Katzen') == 'de'
    assert detect_language('the cat is not happy') == 'en'
    assert detect_language('me timaron con el doblaje') == 'es'
    # Nothing to go on: stay unknown rather than guess and hide the wrong memes.
    assert detect_language('😂😂😂') == ''
    assert detect_language('ok') == ''
    assert detect_language('') == ''


def test_language_detection_needs_more_than_one_word():
    # B-040: one German function word in an English caption is not German.
    from bt.hub.memes import detect_language
    assert detect_language('das meme') == ''
    assert detect_language('when das meme hits') == ''
    assert detect_language('das ist nicht gut') == 'de'
    # Accented names and loanwords in English captions are not evidence either.
    for caption in ('Motörhead ist back', 'Häagen-Dazs mit sprinkles', 'me as a façade', 'el niño is coming',
                    'nada to see here, Señor', 'we got the van', 'we need a bigger van', 'op we need more',
                    'des moines plus'):
        assert detect_language(caption) in ('', 'en'), caption
    assert detect_language('hva gjør du') == 'no'
    assert detect_language('jag är trött') == 'sv'


def test_language_backfill_walks_past_undetectable_rows(client):
    # B-041: captions that stay unknown must not pin the slice to the first rows forever.
    from bt.db import session
    from bt.hub import memes
    from sqlmodel import select
    from bt.hub.models import ContentItem, MemeExample
    with session() as s:
        ids = []
        for i in range(5):
            item = ContentItem(url=f'https://m.example/{i}', type='meme', title='😂' if i < 3 else 'das ist nicht gut')
            s.add(item); s.flush()
            s.add(MemeExample(item_id=item.id, language='de' if i == 0 else ''))
            ids.append(item.id)
        s.commit()
        # The first slice only sees undetectable rows; it also clears the old loose guess.
        assert memes.backfill_languages(s, limit=3) == 1
        assert memes.backfill_languages(s, limit=3) == 2
        assert memes.backfill_languages(s, limit=3) == 0
        langs = {ex.item_id: ex.language for ex in s.exec(select(MemeExample)).all()}
    assert [langs[i] for i in ids] == ['', '', '', 'de', 'de']


def test_facets_report_the_hidden_languages(client):
    assert client.get('/api/hub/memes/facets').json()['hidden_languages'] == []
    client.post('/api/hub/memes/languages/de/hide', json={'hidden': True})
    assert client.get('/api/hub/memes/facets').json()['hidden_languages'] == ['de']
