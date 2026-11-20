import pytest
from sqlmodel import select

from test_curation import client  # noqa: F401
from bt.db import session
from bt.hub import board_curator as bc, board_recipes as br, moodboard_sources, moodboards as mb
from bt.hub.models import MoodBoard, MoodBoardSource, MoodPin, MoodSite, MoodSourceHunt
from bt.hub.watchers import RuleError

GALLERY = b'''<html><head><title>Fern Archive</title>
<link rel="alternate" type="application/rss+xml" href="/feed.xml"></head><body>
<form action="/search" method="get"><input type="hidden" name="t" value="photos"><input type="search" name="q"></form>
<img src="/static/logo.png">
''' + b''.join(b'<div class="tile"><a href="/photo/%d"><img data-src="/media/fern%d.jpg" alt="Fern %d"></a></div>' % (i, i, i)
               for i in range(8)) + b'<a href="/gallery?page=2">next</a></body></html>'


def verdicts(schema):
    n = schema['properties']['verdicts']['maxItems']
    return {'verdicts': [{'index': i, 'seen': 'fern', 'fits': True, 'why': 'fern'} for i in range(n)]}


@pytest.fixture(autouse=True)
def stubs(client, monkeypatch):
    monkeypatch.setattr(mb.ai, 'enabled', lambda: True)
    monkeypatch.setattr(mb, 'get_settings', lambda: {'llm': {'vision': True}})
    monkeypatch.setattr(mb, 'vision_image', lambda data: 'data:image/jpeg;base64,cGl4ZWxz')
    monkeypatch.setattr(mb, 'capture_image', lambda url: (b'bytes', 'image/png', 800, 600))

    def call(system, user, schema, **kw):
        props = schema['properties']
        if 'verdicts' in props:
            return verdicts(schema)
        if 'queries' in props:
            return {'queries': ['fern photo archive']}
        return {'pick': []}
    monkeypatch.setattr(mb.ai, '_call', call)


def make(client, **kw):
    r = client.post('/api/hub/boards', json={'name': 'Ferns', 'interests': 'ferns', **kw})
    assert r.status_code == 200, r.text
    return r.json()['board']


def test_validate_rejects_unsafe_recipes():
    with pytest.raises(ValueError):
        br.validate('gallery', {'url': 'http://example.org/'})
    with pytest.raises(RuleError):
        br.validate('search_template', {'url_template': 'https://example.org/search'})  # no {q}
    with pytest.raises(RuleError):
        br.validate('gallery', {'url': 'https://example.org/', 'item': 'div:nth-child(2)'})
    with pytest.raises(RuleError):
        br.validate('subreddit', {'name': '../etc'})
    with pytest.raises(RuleError):
        br.validate('script', {})
    assert br.validate('subreddit', {'name': 'r/Ferns'}) == {'name': 'Ferns'}


def test_propose_and_parse_gallery_page():
    options = br.propose(GALLERY, 'https://ferns.example.org/gallery', 'ferns')
    kinds = [k for k, _ in options]
    assert kinds[0] == 'search_template' and 'gallery' in kinds and 'feed' in kinds
    search = options[0][1]
    assert search['url_template'] == 'https://ferns.example.org/search?t=photos&q={q}'
    assert search['item'] == 'div.tile' and search['image_attr'] == 'data-src' and search['page_param'] == 'page'
    assert br.page_url('search_template', search, 'royal fern', 2).endswith('q=royal+fern&page=2')
    items = br.parse_page(GALLERY, 'https://ferns.example.org/gallery', search)
    assert len(items) == 8
    assert items[0]['image_url'] == 'https://ferns.example.org/media/fern0.jpg'
    assert items[0]['source_url'] == 'https://ferns.example.org/photo/0'
    assert items[0]['title'] == 'Fern 0'
    assert br.known_network('https://www.reddit.com/r/ferns/comments/x') == ('subreddit', {'name': 'ferns'})


def test_create_queues_hunt_and_find_sources_conflicts(client):
    b = make(client)
    with session() as s:
        assert s.exec(select(MoodSourceHunt).where(MoodSourceHunt.board_id == b['id'])).one().status == 'queued'
    assert client.post(f"/api/hub/boards/{b['id']}/find-sources").status_code == 409
    data = client.get(f"/api/hub/boards/{b['id']}/sources").json()
    assert data['sources'] == [] and data['hunt']['status'] == 'queued'


def hunt_env(monkeypatch, hits):
    monkeypatch.setattr(bc, 'moodboard_sources_web_ok', lambda: (True, ''))
    monkeypatch.setattr(bc, 'providers_web_search', lambda q, safe=False: hits)
    monkeypatch.setattr(br.curation, 'fetch', lambda url, **kw: (GALLERY, url.split('?')[0].replace('/search', '/gallery'),
                                                                   {'content_type': 'text/html'}))


def test_hunt_saves_recipe_to_shared_library_and_links_board(client, monkeypatch):
    hunt_env(monkeypatch, [{'url': 'https://ferns.example.org/gallery', 'title': 'Fern Archive', 'summary': 'photos'},
                           {'url': 'https://www.pinterest.com/x', 'title': 'Pins', 'summary': ''}])
    b = make(client)
    assert bc.dispatch_due()
    data = client.get(f"/api/hub/boards/{b['id']}/sources").json()
    assert data['hunt']['status'] == 'complete' and data['hunt']['found'] == 1, data['hunt']
    [src] = data['sources']
    assert src['kind'] == 'search_template' and src['host'] == 'ferns.example.org' and src['fit'] == 5
    with session() as s:
        assert s.exec(select(MoodSite)).one().recipe['url_template'].endswith('q={q}')
    # A second board reuses the shared recipe through the library step.
    monkeypatch.setattr(mb.ai, '_call', lambda system, user, schema, **kw:
                        verdicts(schema) if 'verdicts' in schema['properties'] else
                        {'pick': [data_site_id()]} if 'pick' in schema['properties'] else {'queries': []})
    b2 = make(client, name='More ferns')
    assert bc.dispatch_due()
    again = client.get(f"/api/hub/boards/{b2['id']}/sources").json()
    assert [x['host'] for x in again['sources']] == ['ferns.example.org']
    with session() as s:
        assert len(s.exec(select(MoodSite)).all()) == 1


def data_site_id():
    with session() as s:
        return s.exec(select(MoodSite.id)).first()


def test_hunt_rejects_sites_that_do_not_fit(client, monkeypatch):
    hunt_env(monkeypatch, [{'url': 'https://ferns.example.org/gallery', 'title': 'Fern Archive', 'summary': ''}])
    monkeypatch.setattr(mb.ai, '_call', lambda system, user, schema, **kw:
                        {'verdicts': [{'index': i, 'seen': 'car', 'fits': False, 'why': 'off topic'}
                                      for i in range(schema['properties']['verdicts']['maxItems'])]}
                        if 'verdicts' in schema['properties'] else {'queries': ['x']} if 'queries' in schema['properties'] else {'pick': []})
    b = make(client)
    assert bc.dispatch_due()
    data = client.get(f"/api/hub/boards/{b['id']}/sources").json()
    assert data['hunt']['status'] == 'complete' and data['sources'] == []
    assert any('fit the board' in e['message'] for e in data['hunt']['log'])


def test_child_board_never_gets_social_sources(client, monkeypatch):
    hunt_env(monkeypatch, [{'url': 'https://www.reddit.com/r/ferns/', 'title': 'r/ferns', 'summary': ''}])
    monkeypatch.setattr(moodboard_sources, 'reddit_listing', lambda *a, **k: pytest.fail('child board fetched reddit'))
    b = make(client, child_friendly=True)
    assert bc.dispatch_due()
    assert client.get(f"/api/hub/boards/{b['id']}/sources").json()['sources'] == []


def link_site(bid, kind='gallery'):
    with session() as s:
        site = MoodSite(host='ferns.example.org', kind=kind, name='Fern Archive',
                        recipe=br.validate('gallery', {'url': 'https://ferns.example.org/gallery', 'item': 'div.tile',
                                                       'image_attr': 'data-src'}))
        s.add(site)
        s.flush()
        link = MoodBoardSource(board_id=bid, site_id=site.id)
        s.add(link)
        s.commit()
        return site.id, link.id


def test_board_run_pins_from_linked_sources_and_tracks_use(client, monkeypatch):
    b = make(client)
    site_id, link_id = link_site(b['id'])
    monkeypatch.setattr(br.curation, 'fetch', lambda url, **kw: (GALLERY, url, {'content_type': 'text/html'}))
    monkeypatch.setattr(moodboard_sources, 'search', lambda q, **kw: [])
    assert mb.dispatch_due()
    with session() as s:
        pins = s.exec(select(MoodPin).where(MoodPin.board_id == b['id'])).all()
        assert len(pins) == 8 and all(p.source_url.startswith('https://ferns.example.org/photo/') for p in pins)
        link = s.get(MoodBoardSource, link_id)
        assert (link.hits, link.kept) == (8, 8)
        assert s.get(MoodSite, site_id).last_ok > 0


def test_failing_recipe_is_marked_broken_and_skipped(client, monkeypatch):
    b = make(client)
    site_id, link_id = link_site(b['id'])
    for _ in range(bc.BROKEN_AFTER):
        bc.record_fetch(site_id, False)
    with session() as s:
        assert s.get(MoodSite, site_id).status == 'broken'
        assert bc.board_sources(s, b['id'], False) == []


def test_sources_toggle_remove_and_board_delete(client):
    b = make(client)
    _site_id, link_id = link_site(b['id'])
    r = client.patch(f"/api/hub/boards/{b['id']}/sources/{link_id}", json={'enabled': False})
    assert r.status_code == 200 and r.json()['sources'][0]['enabled'] is False
    other = make(client, name='Other')
    assert client.delete(f"/api/hub/boards/{other['id']}/sources/{link_id}").status_code == 404
    assert client.delete(f"/api/hub/boards/{b['id']}").status_code == 200
    with session() as s:
        assert not s.exec(select(MoodBoardSource)).all()
        assert not s.exec(select(MoodSourceHunt).where(MoodSourceHunt.board_id == b['id'])).all()
        assert s.exec(select(MoodSite)).all()  # shared library survives


def test_platform_sources_the_model_names_are_tried_without_web_search(client, monkeypatch):
    from bt.db import save_setting, get_settings
    save_setting('moodboard_sources', {**get_settings()['moodboard_sources'], 'giphy_api_key': 'k'})
    monkeypatch.setattr(bc, 'moodboard_sources_web_ok', lambda: (False, 'No web search provider is configured.'))
    gifs = [dict(title=f'Funny {i}', source_url=f'https://giphy.com/gifs/x-{i}', image_url=f'https://media.giphy.com/media/{i}/giphy.gif',
                 attribution='Giphy', license='Giphy terms') for i in range(10)]
    seen = []
    monkeypatch.setattr(moodboard_sources, 'giphy_search', lambda q, l, o, key, rating='pg-13': seen.append((q, rating)) or gifs)
    monkeypatch.setattr(moodboard_sources, 'reddit_listing', lambda url, limit=30: [])
    def call(system, user, schema, **kw):
        props = schema['properties']
        if 'verdicts' in props:
            return verdicts(schema)
        if 'subreddits' in props:
            return {'subreddits': ['gifs'], 'commons_categories': [], 'platform_queries': ['funny animal reaction']}
        return {'pick': [], 'queries': ['x']}
    monkeypatch.setattr(mb.ai, '_call', call)
    b = make(client, name='Funny GIFs', interests='funny gifs')
    assert bc.dispatch_due()
    data = client.get(f"/api/hub/boards/{b['id']}/sources").json()
    assert [s['kind'] for s in data['sources']] == ['network_search'] and data['sources'][0]['host'] == 'giphy.com'
    assert seen[0] == ('funny animal reaction', 'pg-13')
    assert any('r/gifs: only 0 usable images' in e['message'] for e in data['hunt']['log'])
    assert data['hunt']['status'] == 'complete'


def test_blocked_site_is_opened_in_the_isolated_browser(client, monkeypatch):
    from bt.hub import browser
    hunt_env(monkeypatch, [{'url': 'https://ferns.example.org/gallery', 'title': 'Fern Archive', 'summary': ''}])
    def plain(url, **kw):
        raise ValueError('Source returned 403')
    monkeypatch.setattr(br.curation, 'fetch', plain)
    monkeypatch.setenv('BT_BROWSER_URL', 'http://browser:9300')
    rendered = []
    monkeypatch.setattr(browser, 'render', lambda url, **kw: rendered.append(url) or
                        {'html': GALLERY.decode(), 'final_url': url.split('?')[0].replace('/search', '/gallery'), 'status': 200})
    b = make(client)
    assert bc.dispatch_due()
    data = client.get(f"/api/hub/boards/{b['id']}/sources").json()
    assert data['hunt']['found'] == 1, data['hunt']['log']
    with session() as s:
        assert s.exec(select(MoodSite)).one().recipe['via'] == 'browser'
    assert any('opening it in the browser' in e['message'] for e in data['hunt']['log'])


def test_later_rounds_learn_from_what_failed(client, monkeypatch):
    hits = iter([[{'url': 'https://ferns.example.org/gallery', 'title': 'Fern Archive', 'summary': ''}], [], []])
    hunt_env(monkeypatch, [])
    monkeypatch.setattr(bc, 'providers_web_search', lambda q, safe=False: next(hits, []))
    calls = []
    real = bc._queries
    monkeypatch.setattr(bc, '_queries', lambda *a, **kw: calls.append(a[4] if len(a) > 4 else None) or ['q'])
    monkeypatch.setattr(mb.ai, '_call', lambda system, user, schema, **kw:
                        {'verdicts': [{'index': i, 'seen': 'car', 'fits': False, 'why': 'no'}
                                      for i in range(schema['properties']['verdicts']['maxItems'])]}
                        if 'verdicts' in schema['properties'] else {'pick': [], 'subreddits': [], 'commons_categories': [], 'platform_queries': []})
    b = make(client)
    assert bc.dispatch_due()
    assert len(calls) == bc.MAX_ROUNDS and calls[0] is None
    assert any('off-topic' in t for t in calls[1])
    assert real  # the real planner stays importable


def test_browser_client_refuses_private_addresses_and_needs_config(monkeypatch):
    from bt.hub import browser
    monkeypatch.delenv('BT_BROWSER_URL', raising=False)
    with pytest.raises(browser.BrowserUnavailable):
        browser.render('https://example.org/')
    monkeypatch.setenv('BT_BROWSER_URL', 'http://browser:9300')
    for url in ('http://10.0.10.73:8006/', 'https://localhost/', 'https://192.168.1.1/'):
        with pytest.raises(ValueError):
            browser.render(url)


def test_network_recipes_validate_and_respect_child_boards(client):
    assert br.validate('network_search', {'network': 'giphy', 'query': 'cats'}) == {'network': 'giphy', 'query': 'cats'}
    with pytest.raises(RuleError):
        br.validate('network_search', {'network': 'myspace'})
    assert br.validate('commons_category', {'name': 'Category:Animated_GIF_files'}) == {'name': 'Animated GIF files'}
    assert not br.network_available('bluesky', child=True)


def test_giphy_and_tenor_results_are_normalized(monkeypatch):
    giphy = {'data': [{'url': 'https://giphy.com/gifs/cat-abc', 'title': 'Cat GIF', 'username': 'catlover',
                       'images': {'downsized_large': {'url': 'https://media.giphy.com/media/abc/giphy.gif', 'width': '480', 'height': '270'}}},
                      {'url': 'https://evil.example/x', 'images': {'original': {'url': 'https://evil.example/x.gif'}}}]}
    tenor = {'results': [{'itemurl': 'https://tenor.com/view/dog-123', 'content_description': 'Dog dance',
                          'media_formats': {'gif': {'url': 'https://media.tenor.com/x/dog.gif', 'dims': [320, 240]}}}]}
    seen = []
    monkeypatch.setattr(moodboard_sources, '_json', lambda url: seen.append(url) or (giphy if 'giphy' in url else tenor))
    [g] = moodboard_sources.giphy_search('cats', 30, 0, 'key', rating='g')
    assert g['source_url'] == 'https://giphy.com/gifs/cat-abc' and g['media_type'] == 'gif' and g['width'] == 480
    assert 'rating=g' in seen[0]
    [t] = moodboard_sources.tenor_search('dogs', 30, 0, 'key', contentfilter='high')
    assert t['title'] == 'Dog dance' and t['image_url'] == 'https://media.tenor.com/x/dog.gif'
    assert 'contentfilter=high' in seen[1]
    assert moodboard_sources.giphy_search('cats', 30, 0, '') == []
