"""Opt-in Pinterest search: parser, throttle/budgets, auto-pause, curator source and live board routes.

All HTTP is mocked at the social_fetch boundary; no test reaches the network.
"""
import json
from io import BytesIO
from urllib.parse import parse_qs, urlsplit

import pytest
from PIL import Image
from sqlmodel import select

from test_curation import client  # noqa: F401
from bt import curation
from bt.db import get_settings, save_setting, session
from bt.hub import moodboard_sources as sources, moodboards as mb, pinterest_guard as guard, pinterest_live
from bt.hub.models import MoodPin
from bt.models import AgentEvent


def pin(n, **changes):
    item = {'id': str(1000 + n), 'type': 'pin', 'grid_title': f'Reading nook {n}', 'description': 'window seat',
            'pinner': {'username': 'maker'},
            'images': {'orig': {'url': f'https://i.pinimg.com/originals/aa/bb/{n}.jpg', 'width': 1200, 'height': 1600},
                       '736x': {'url': f'https://i.pinimg.com/736x/aa/bb/{n}.jpg', 'width': 736, 'height': 980},
                       '236x': {'url': f'https://i.pinimg.com/236x/aa/bb/{n}.jpg', 'width': 236, 'height': 314}}}
    item.update(changes)
    return item


def payload(pins, bookmark='BM2', status='success'):
    return {'resource_response': {'status': status, 'data': {'results': pins}, 'bookmark': bookmark}}


@pytest.fixture
def net(client, monkeypatch):
    """Mock pinterest.com/i.pinimg.com behind social_fetch; spacing sleeps recorded, not slept."""
    state = {'calls': [], 'sleeps': [], 'reply': payload([pin(1), pin(2)])}
    out = BytesIO()
    Image.new('RGB', (3, 4)).save(out, format='PNG')
    png = out.getvalue()
    def fetch(url, **kw):
        state['calls'].append(url)
        if urlsplit(url).hostname == 'i.pinimg.com':
            return (png, url, {}) if kw.get('with_metadata') else png
        reply = state['reply']
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, bytes) else json.dumps(reply).encode()
    monkeypatch.setattr(guard.social_fetch, 'fetch', fetch)
    monkeypatch.setattr(mb.social_fetch, 'fetch', fetch)
    monkeypatch.setattr(guard.time, 'sleep', lambda s: state['sleeps'].append(s))
    monkeypatch.setattr(guard.random, 'uniform', lambda a, b: a)
    pinterest_live._results.clear(); pinterest_live._pages.clear(); pinterest_live._previews.clear()
    sources._pinterest_bookmarks.clear()
    return state


def enable(on=True):
    save_setting('moodboard_sources', {**get_settings()['moodboard_sources'], 'pinterest': on})


def clear_spacing():
    guard._update(lambda st: st.update(next_at=0))


def test_parser_skips_promoted_video_explicit_and_foreign_images():
    found = sources.parse_pinterest(payload([
        pin(1), pin(2, is_promoted=True), pin(3, videos={'v': 1}), pin(4, story_pin_data={'pages': [1]}),
        pin(5, grid_title='nude beach'), pin(6, images={'orig': {'url': 'https://evil.example/x.jpg'}}),
        pin(7, images={'736x': {'url': 'https://i.pinimg.com/736x/x/7.jpg', 'width': 736, 'height': 900}}),
        pin(1), pin(8, id='abc')]))
    assert [f['source_url'] for f in found] == ['https://www.pinterest.com/pin/1001/', 'https://www.pinterest.com/pin/1007/']
    first = found[0]
    assert first['image_url'] == 'https://i.pinimg.com/originals/aa/bb/1.jpg' and first['width'] == 1200
    assert first['preview_url'] == 'https://i.pinimg.com/236x/aa/bb/1.jpg'
    assert first['attribution'] == '@maker (Pinterest)' and first['provider'] == 'pinterest'
    assert first['license'] == sources.RIGHTS and first['title'] == 'Reading nook 1'
    assert found[1]['image_url'].endswith('/736x/x/7.jpg')


def test_parser_finds_pins_in_embedded_page_json():
    page = {'props': {'initialReduxState': {'pins': {'1001': pin(1)}}}}
    assert [f['source_url'] for f in sources.parse_pinterest(page)] == ['https://www.pinterest.com/pin/1001/']


def test_source_is_opt_in_and_skipped_while_blocked(net):
    cfg = {'web': False, 'pinterest': False}
    assert sources.enabled_sources(cfg) == []
    assert [n for n, _ in sources.enabled_sources({**cfg, 'pinterest': True})] == ['pinterest']
    guard.block('test block')
    assert sources.enabled_sources({**cfg, 'pinterest': True}) == []


def test_curator_search_request_shape_paging_and_honest_client(net):
    found = sources.pinterest_search('cozy reading nook', 30, 0)
    assert len(found) == 2 and 'preview_url' not in found[0]
    url = urlsplit(net['calls'][0])
    assert url.hostname == 'www.pinterest.com' and url.path == '/resource/BaseSearchResource/get/'
    data = json.loads(parse_qs(url.query)['data'][0])
    assert data['options'] == {'query': 'cozy reading nook', 'scope': 'pins', 'page_size': 25, 'bookmarks': []}
    # Later pages only follow a bookmark Pinterest handed out; unknown pages never crawl.
    clear_spacing()
    sources.pinterest_search('cozy reading nook', 30, 30)
    assert json.loads(parse_qs(urlsplit(net['calls'][1]).query)['data'][0])['options']['bookmarks'] == ['BM2']
    assert sources.pinterest_search('cozy reading nook', 30, 90) == [] and len(net['calls']) == 2


def test_spacing_and_per_run_budget_return_quietly_for_the_curator(net):
    assert sources.pinterest_search('nook', 30, 0)
    assert sources.pinterest_search('other', 30, 0) == []  # Still inside the 12-30 s spacing.
    clear_spacing()
    assert sources.pinterest_search('third', 30, 0)
    clear_spacing()
    assert sources.pinterest_search('fourth', 30, 0) == []  # Two curator requests per 10 minutes.
    assert len(net['calls']) == 2


def test_hour_and_day_budgets(net, monkeypatch):
    monkeypatch.setattr(guard, 'HOUR_BUDGET', 3)
    for _ in range(3):
        clear_spacing()
        guard.fetch('https://www.pinterest.com/resource/x', origin='live')
    clear_spacing()
    with pytest.raises(guard.Paused, match='hourly'):
        guard.fetch('https://www.pinterest.com/resource/x', origin='live')
    monkeypatch.setattr(guard, 'HOUR_BUDGET', 99)
    monkeypatch.setattr(guard, 'DAY_BUDGET', 3)
    with pytest.raises(guard.Paused, match='daily'):
        guard.fetch('https://www.pinterest.com/resource/x', origin='live')
    assert guard.status()['day_left'] == 0


def test_live_waits_briefly_for_its_slot(net, monkeypatch):
    guard.fetch('https://www.pinterest.com/resource/x', origin='live')
    guard._update(lambda st: st.update(next_at=guard.time.time() + 2))
    guard.fetch('https://www.pinterest.com/resource/x', origin='live')
    assert net['sleeps'] and 0 < net['sleeps'][-1] <= guard.LIVE_MAX_WAIT


@pytest.mark.parametrize('reply,reason', [
    (curation.FetchError(403), 'HTTP 403'),
    (curation.FetchError(429, '60'), 'HTTP 429'),
    (ValueError('Source returned unsupported content type text/html.'), 'login or captcha'),
    (b'<html>captcha</html>', 'something other'),
    (payload([], status='failure'), 'refused the search'),
])
def test_any_block_pauses_switches_off_and_reports(net, reply, reason):
    enable()
    net['reply'] = reply
    assert sources.pinterest_search('nook', 30, 0) == []  # Curator carries on quietly.
    st = guard.status()
    assert st['blocked'] and reason in st['reason'] and st['blocked_until'] - st['last_block_at'] >= 86_000
    assert get_settings()['moodboard_sources']['pinterest'] is False
    with session() as s:
        assert any(e.kind == 'pinterest' and e.level == 'warn' for e in s.exec(select(AgentEvent)).all())
    calls = len(net['calls'])
    clear_spacing()
    with pytest.raises(guard.Paused, match='paused itself'):
        guard.fetch('https://www.pinterest.com/resource/x', origin='live')
    assert len(net['calls']) == calls  # Nothing more is sent while paused.


def test_repeat_block_doubles_the_pause(net):
    guard.block('first')
    guard._update(lambda st: st.update(blocked_until=0))
    guard.block('second')
    st = guard.status()
    assert st['blocked_until'] - st['last_block_at'] >= guard.REPEAT_BLOCK_S - 5


def test_image_budget(net, monkeypatch):
    monkeypatch.setattr(guard, 'IMAGE_DAY_BUDGET', 1)
    mb.capture_image('https://i.pinimg.com/originals/aa/bb/1.jpg')
    with pytest.raises(guard.Paused, match='image budget'):
        mb.capture_image('https://i.pinimg.com/originals/aa/bb/2.jpg')


def board(client):
    return client.post('/api/hub/boards', json={'name': 'Nooks', 'interests': 'reading nooks'}).json()['board']


def test_live_search_flow_preview_add_and_on_board(net, client):
    b = board(client)
    base = f"/api/hub/boards/{b['id']}/pinterest"
    assert client.get(base + '/search', params={'q': 'nook'}).status_code == 409  # Opt-in.
    assert client.get('/api/hub/boards/pinterest/status').json()['enabled'] is False
    enable()
    res = client.get(base + '/search', params={'q': 'nook'})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body['has_more'] is True and len(body['results']) == 2
    first = body['results'][0]
    assert set(first) == {'token', 'title', 'pin_url', 'attribution', 'width', 'height', 'preview_url', 'on_board'}
    assert first['preview_url'] == f"{base}/preview/{first['token']}" and 'pinimg' not in json.dumps(body)
    assert first['on_board'] is False
    preview = client.get(first['preview_url'])
    assert preview.status_code == 200 and preview.headers['cache-control'] == 'private, no-store'
    assert net['calls'][-1] == 'https://i.pinimg.com/236x/aa/bb/1.jpg'
    assert client.get(base + '/preview/not-a-token').status_code == 404
    added = client.post(base + '/add', json={'token': first['token']})
    assert added.status_code == 200 and not added.json().get('existing')
    with session() as s:
        row = s.exec(select(MoodPin)).one()
        assert row.source_url == 'https://www.pinterest.com/pin/1001/' and row.image_url.endswith('/originals/aa/bb/1.jpg')
    assert client.post(base + '/add', json={'token': first['token']}).json()['existing'] is True
    clear_spacing()
    again = client.get(base + '/search', params={'q': 'nook'}).json()['results']
    assert again[0]['on_board'] is True and again[1]['on_board'] is False
    clear_spacing()
    more = client.get(base + '/search', params={'q': 'nook', 'more': 'true'})
    assert json.loads(parse_qs(urlsplit(net['calls'][-1]).query)['data'][0])['options']['bookmarks'] == ['BM2']
    assert more.status_code == 200


def test_live_search_reports_spacing_and_block(net, client):
    b = board(client)
    enable()
    base = f"/api/hub/boards/{b['id']}/pinterest"
    assert client.get(base + '/search', params={'q': 'nook'}).status_code == 200
    guard._update(lambda st: st.update(next_at=guard.time.time() + 20))
    spaced = client.get(base + '/search', params={'q': 'nook'})
    assert spaced.status_code == 429 and 'Try again in' in spaced.json()['detail'] and spaced.headers['retry-after']
    clear_spacing()
    net['reply'] = curation.FetchError(403)
    blocked = client.get(base + '/search', params={'q': 'nook'})
    assert blocked.status_code == 503 and 'paused' in blocked.json()['detail']
    assert client.get('/api/hub/boards/pinterest/status').json()['blocked'] is True


def test_live_routes_respect_profiles(net, client):
    b = board(client)
    enable()
    base = f"/api/hub/boards/{b['id']}/pinterest"
    token = client.get(base + '/search', params={'q': 'nook'}).json()['results'][0]['token']
    other = client.post('/api/profiles', json={'name': 'Other'}).json()
    headers = {'X-Profile-Id': str(other['id'])}
    assert client.get(base + '/search', params={'q': 'nook'}, headers=headers).status_code == 404
    assert client.get(f'{base}/preview/{token}', headers=headers).status_code == 404
    assert client.post(base + '/add', json={'token': token}, headers=headers).status_code == 404


def test_settings_accepts_only_a_pinterest_switch(client):
    assert get_settings()['moodboard_sources']['pinterest'] is False
    current = client.get('/api/settings').json()['moodboard_sources']
    ok = client.put('/api/settings/moodboard_sources', json={**current, 'pinterest': True})
    assert ok.status_code == 200, ok.text
    assert client.put('/api/settings/moodboard_sources', json={**current, 'pinterest': 'yes'}).status_code == 400
