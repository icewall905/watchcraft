"""Whole-board Pinterest imports: guarded paging, spaced slices, backoff and resume. HTTP is mocked at social_fetch."""
import json
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlmodel import select

from test_curation import client  # noqa: F401
from test_moodboards import make
from bt import curation
from bt.db import session
from bt.hub import moodboard_imports as imp, moodboards, pinterest_guard as guard, social_fetch
from bt.hub.models import MoodAsset, MoodBoard, MoodImport, MoodPin

URL = 'https://www.pinterest.com/u/b/'


def pin(n):
    return {'id': str(1000 + n), 'type': 'pin', 'grid_title': f'Pin {n}',
            'images': {'orig': {'url': f'https://i.pinimg.com/originals/aa/{n}.jpg', 'width': 10, 'height': 20}}}


def page(pins, bookmark=''):
    return {'resource_response': {'status': 'success', 'data': pins, 'bookmark': bookmark or '-end-'}}


@pytest.mark.parametrize('url', ['http://www.pinterest.com/u/b/', 'https://pinterest.com@127.0.0.1/u/b/', 'https://www.pinterest.com:444/u/b/', 'https://www.pinterest.com/pin/123/', 'https://pin.it/123', 'https://www.pinterest.com/u/b/extra', 'https://www.pinterest.com/u/feed.rss', 'https://www.pinterest.com.evil/u/b/'])
def test_reject_invalid_links(url):
    with pytest.raises(ValueError): imp.board_url(url)


@pytest.fixture
def net(client, monkeypatch):
    """A 3-pin board over two feed pages. ``fail`` maps a call kind to an exception raised once."""
    state = {'calls': [], 'images': [], 'fail': {}, 'pages': {'': page([pin(1), pin(2)], 'P2'), 'P2': page([pin(3)])},
             'board': {'resource_response': {'status': 'success', 'data': {'id': '99', 'pin_count': 3}}}}

    def fetch(url, **kw):
        parts = urlsplit(url)
        kind = 'board' if 'BoardResource' in parts.path else 'feed'
        state['calls'].append(kind)
        if kind in state['fail']:
            raise state['fail'].pop(kind)
        if kind == 'board':
            return json.dumps(state['board']).encode()
        options = json.loads(parse_qs(parts.query)['data'][0])['options']
        assert options['board_id'] == '99' and options['board_url'] == '/u/b/'
        return json.dumps(state['pages'][(options['bookmarks'] or [''])[0]]).encode()

    def capture(url):
        if 'image' in state['fail']:
            raise state['fail'].pop('image')
        state['images'].append(url)
        return b'local', 'image/jpeg', 10, 20

    monkeypatch.setattr(social_fetch, 'fetch', fetch)
    monkeypatch.setattr(moodboards, 'capture_image', capture)
    monkeypatch.setattr(guard, 'SPACING', (0.0, 0.0))
    monkeypatch.setattr(imp, 'IMAGE_GAP', (0.0, 0.0))
    return state


def job(jid):
    with session() as s:
        return s.get(MoodImport, jid)


def due(jid):
    """Pretend the wait is over (the guard pause too) so the next tick runs now."""
    with session() as s:
        row = s.get(MoodImport, jid)
        row.next_at = 0
        s.add(row)
        s.commit()
    guard._update(lambda state: state.update(blocked_until=0, next_at=0, requests=[]))


def drain(jid, limit=30):
    for _ in range(limit):
        if job(jid).status in ('complete', 'error'):
            return job(jid)
        due(jid)
        imp.dispatch_due()
    raise AssertionError('import did not finish')


def start(client, b):
    res = client.post(f"/api/hub/boards/{b['id']}/pinterest", json={'url': 'https://pinterest.com/u/b/?x=1'})
    assert res.status_code == 200
    return res.json()['import']['id']


def test_whole_board_imports_slice_by_slice(client, net):
    b = make(client, enabled=False); path = f"/api/hub/boards/{b['id']}/pinterest"
    jid = start(client, b)
    assert client.post(path, json={'url': URL}).status_code == 409
    assert imp.dispatch_due() and net['calls'] == ['board'] and job(jid).total == 3
    imp.dispatch_due(); assert net['calls'] == ['board', 'feed'] and not net['images']  # one Pinterest request per tick
    imp.dispatch_due(); assert len(net['images']) == 1
    data = client.get(path).json()['import']
    assert data['status'] == 'waiting' and data['done'] == 1 and data['total'] == 3 and not data['note']
    assert 'lease' not in data and 'processed' not in data and 'cursor' not in data
    final = drain(jid)
    assert final.status == 'complete' and final.imported == 3 and final.total == 3 and net['calls'].count('feed') == 2
    pin_ = client.get(f"/api/hub/boards/{b['id']}").json()['pins'][0]
    assert pin_['saved'] and client.get(pin_['image_url']).content == b'local'
    other = client.post('/api/profiles', json={'name': 'Other'}).json(); h = {'X-Profile-Id': str(other['id'])}
    assert client.get(path, headers=h).status_code == 404
    assert client.post(path, headers=h, json={'url': URL}).status_code == 404
    # Importing again only counts what is already there; no image is fetched twice.
    jid2 = start(client, b); before = len(net['images'])
    again = drain(jid2)
    assert again.existing == 3 and again.imported == 0 and len(net['images']) == before
    client.delete(f"/api/hub/boards/{b['id']}")
    with session() as s:
        assert not s.exec(select(MoodImport)).all() and not s.exec(select(MoodAsset)).all()


def test_ban_mid_board_waits_and_resumes_where_it_stopped(client, net):
    b = make(client, enabled=False)
    jid = start(client, b)
    for _ in range(4):  # board, page 1, two images
        due(jid); imp.dispatch_due()
    assert job(jid).imported == 2 and job(jid).cursor == 'P2'
    net['fail']['feed'] = curation.FetchError(429)
    due(jid); imp.dispatch_due()
    row = job(jid)
    assert row.status == 'waiting' and row.next_at > time.time() + 3600 and 'Pinterest' in row.note
    assert row.imported == 2 and row.cursor == 'P2' and not row.error and guard.blocked()
    assert not imp.dispatch_due()  # nothing touches Pinterest while it waits
    assert 'Resumes' not in json.dumps(row.model_dump()) and row.lease == ''
    final = drain(jid)
    assert final.status == 'complete' and final.imported == 3
    assert net['calls'].count('board') == 1 and net['calls'].count('feed') == 3  # page 1 was not asked again


def test_captcha_page_and_image_block_back_off_without_losing_pins(client, net):
    b = make(client, enabled=False)
    jid = start(client, b)
    due(jid); imp.dispatch_due()
    net['fail']['feed'] = ValueError('Unsupported content type text/html')
    due(jid); imp.dispatch_due()
    assert job(jid).status == 'waiting' and guard.blocked() and job(jid).failures == 0
    due(jid); imp.dispatch_due()  # page 1 now
    net['fail']['image'] = curation.FetchError(403)
    due(jid); imp.dispatch_due()
    row = job(jid)
    assert row.status == 'waiting' and row.failures == 1 and row.processed == [] and row.next_at > time.time() + 600
    net['fail']['image'] = guard.Paused('Pinterest daily image budget used up.', 7200)
    due(jid); imp.dispatch_due()
    assert job(jid).failures == 1 and job(jid).processed == [] and 'budget' in job(jid).note
    final = drain(jid)
    assert final.status == 'complete' and final.imported == 3 and final.skipped == 0


def test_broken_image_is_skipped_but_import_goes_on(client, net):
    b = make(client, enabled=False)
    jid = start(client, b)
    for _ in range(2):
        due(jid); imp.dispatch_due()
    net['fail']['image'] = ValueError('Unsupported image')
    final = drain(jid)
    assert final.status == 'complete' and final.imported == 2 and final.skipped == 1


def test_private_board_errors_without_pausing_pinterest_and_can_be_retried(client, net):
    b = make(client, enabled=False)
    net['board'] = {'resource_response': {'status': 'failure', 'message': 'Board not found'}}
    jid = start(client, b)
    assert drain(jid).status == 'error' and 'public board' in job(jid).error and not guard.blocked()
    net['board'] = {'resource_response': {'status': 'success', 'data': {'id': '99', 'pin_count': 3}}}
    assert start(client, b) == jid  # same link continues the same job
    assert drain(jid).imported == 3


def test_partial_legacy_imports_resume_through_the_background_path(client, net):
    b = make(client, enabled=False)
    with session() as s:
        owner = s.get(MoodBoard, b['id']).profile_id
        s.add(MoodPin(board_id=b['id'], profile_id=owner,
                      source_url='https://www.pinterest.com/pin/1001/', image_url='https://i.pinimg.com/originals/aa/1.jpg', title='old'))
        s.add(MoodImport(board_id=b['id'], profile_id=owner, url=URL.rstrip('/'), status='complete', imported=1, skipped=23, total=24,
                         fetched=True, processed=['https://www.pinterest.com/pin/1001/'], candidates=[{'source_url': 'x'}]))
        s.add(MoodImport(board_id=b['id'], profile_id=owner, url='https://www.pinterest.com/u/gone', status='error', error='feed failed'))
        s.commit()
    assert imp.resume_partial() == 1  # one active import per board; the other link waits for a later sweep
    assert imp.resume_partial() == 0
    with session() as s:
        rows = sorted((r.url, r.status, r.full) for r in s.exec(select(MoodImport)).all())
        jid = next(r.id for r in s.exec(select(MoodImport).where(MoodImport.full == True)))  # noqa: E712
    assert rows == [('https://www.pinterest.com/u/b', 'queued', True), ('https://www.pinterest.com/u/gone', 'error', False)]
    final = drain(jid)
    assert final.status == 'complete' and final.existing == 1 and final.imported == 2
    assert 'https://i.pinimg.com/originals/aa/1.jpg' not in net['images']
    assert imp.resume_partial() == 1  # the second board link resumes once the first finished


def test_deleted_during_capture_does_not_resurrect_data(client, net, monkeypatch):
    b = make(client, enabled=False)
    jid = start(client, b)
    for _ in range(2):
        due(jid); imp.dispatch_due()

    def capture(url):
        client.delete(f"/api/hub/boards/{b['id']}")
        return b'local', 'image/jpeg', 20, 20
    monkeypatch.setattr(moodboards, 'capture_image', capture)
    due(jid); imp.dispatch_due()
    with session() as s:
        assert not s.exec(select(MoodPin)).all() and not s.exec(select(MoodImport)).all()
