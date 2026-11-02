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
