from io import BytesIO

import pytest
from PIL import Image
from sqlmodel import select

from test_curation import client  # noqa: F401
from bt.db import session
from bt.hub import board_quality as bq, moodboards as mb, moodboard_sources
from bt.hub.models import MoodFeedback, MoodHash, MoodPin, MoodPinNote, MoodVerdict


def png(seed=0, size=(64, 48)):
    """A seeded test picture; other sizes are resizes of the same picture (a near copy)."""
    image = Image.new('RGB', (64, 48))
    image.putdata([((x * 7 + seed * 50) % 256, (y * 5) % 256, ((x + y) * 3 + seed * 90) % 256)
                   for y in range(48) for x in range(64)])
    out = BytesIO()
    image.resize(size).save(out, format='PNG')
    return out.getvalue()


@pytest.fixture(autouse=True)
def model_stub(client, monkeypatch):
    monkeypatch.setattr(mb.ai, 'enabled', lambda: True)
    monkeypatch.setattr(mb, 'get_settings', lambda: {'llm': {'vision': True}})
    monkeypatch.setattr(mb, 'vision_image', lambda data: 'data:image/jpeg;base64,cGl4ZWxz')


def make(client, **kw):
    response = client.post('/api/hub/boards', json={'name': 'Style', 'interests': 'velvet flares', **kw})
    assert response.status_code == 200, response.text
    return response.json()['board']


def cand(n, host='commons.wikimedia.org'):
    return dict(title=f'Pic {n}', source_url=f'https://{host}/wiki/File:Pic{n}.jpg',
                image_url=f'https://upload.wikimedia.org/wikipedia/commons/pic{n}.jpg', attribution='', license='')


def test_avoid_hosts_split_from_visual_avoid_list():
    hosts, text = bq.avoid_hosts('Earthy palettes, rust, AI generated images, Pexels.com\nwww.Shein.com; node.js stickers')
    assert hosts == {'pexels.com', 'shein.com'}
    assert text == 'Earthy palettes, rust, AI generated images, node.js stickers'
    assert bq.avoid_hosts('') == (set(), '')


def test_host_rules_block_cap_and_avoid():
    stats = {'pexels.com': [1, 30], 'etsy.com': [3, 8], 'pinterest.com': [90, 30], 'tiny.com': [0, 4]}
    rules = bq.HostRules({'shein.com'}, stats)
    assert rules.blocked == {'pexels.com'} and rules.capped == {'etsy.com'}
    assert not rules.allow('https://www.pexels.com/photo/1', 'https://images.pexels.com/1.jpg')
    assert not rules.allow('https://example.org/a', 'https://img.shein.com/a.jpg')  # image host counts too
    assert [rules.allow(f'https://www.etsy.com/listing/{i}') for i in range(5)] == [True] * 3 + [False] * 2
    assert rules.allow('https://uk.pinterest.com/pin/1') and rules.allow('https://tiny.com/x')
    assert rules.dropped == {'pexels.com': 1, 'shein.com': 1, 'etsy.com': 2}
    assert {r['host'] for r in rules.report()} == {'shein.com', 'pexels.com', 'etsy.com'}
    assert bq.domain('shop.example.co.uk') == 'example.co.uk' and bq.domain('uk.pinterest.com') == 'pinterest.com'


def test_dhash_near_duplicates():
    a, b = bq.dhash(png(0)), bq.dhash(png(0, (128, 96)))
    other = bq.dhash(png(3))
    assert a and bq.distance(a, b) <= bq.DUP_DISTANCE
    assert bq.near_duplicate(b, [a]) and not bq.near_duplicate(other, [a])
    assert bq.dhash(b'not an image') == 0 and not bq.near_duplicate(0, [0])


def test_lessons_group_reasons_and_use_vision_descriptions(client):
    b = make(client)
    with session() as s:
        for n in range(4):
            pin = MoodPin(board_id=b['id'], profile_id=1, title=f'Stock photo title {n}', source_url=f'https://x.org/{n}',
                          image_url=f'https://x.org/{n}.jpg')
            s.add(pin)
            s.flush()
            s.add(MoodVerdict(board_id=b['id'], pin_id=pin.id, fits=True, description={
                'summary': f'Woman in pointed witch hat and lace dress {n}', 'era_read': 'contemporary',
                'people': 'adult', 'photo_type': 'street', 'costume_like': True}))
            note = ['Costume / party look', 'too costume', 'Costume / party look + Wrong era', ''][n]
            s.add(MoodFeedback(pin_id=pin.id, board_id=b['id'], profile_id=1, verdict='bad' if n < 3 else 'good',
                               note=note, title=pin.title))
        s.commit()
        text = mb.feedback_lessons(s, b['id'])
    assert 'Costume or party version' in text and 'rejected 3x' in text and '"too costume"' in text
    assert 'witch hat' in text and 'costume-like' in text and 'Stock photo title' not in text
    assert 'Wrong era' in text and 'WHAT GOOD LOOKS LIKE' in text
    assert bq.reasons_in('AI generated. Yuck.') == ['ugly', 'ai'] and bq.reasons_in('Too 50\'s') == ['era']
    assert bq.reasons_in('context') == []


def test_owner_disputes_curator_tags_and_judge_learns(client):
    b = make(client)
    with session() as s:
        pin = MoodPin(board_id=b['id'], profile_id=1, title='Kept', source_url='https://x.org/k', image_url='https://x.org/k.jpg')
        s.add(pin)
        s.flush()
        s.add(MoodPinNote(pin_id=pin.id, seen='Beige knit sweater by a window', why='w', tags=['Warm', 'Cozy', 'Minimal']))
        s.commit()
        pin_id = pin.id
    url = f"/api/hub/boards/{b['id']}/pins/{pin_id}"
    page = client.get(f"/api/hub/boards/{b['id']}").json()
    assert {p['id']: p['vision_tags'] for p in page['pins']}[pin_id] == ['Warm', 'Cozy', 'Minimal']
    r = client.put(f'{url}/tags', json={'disputed': ['cozy', 'Not a tag']})
    assert r.status_code == 200 and r.json()['pin']['vision_disputed'] == ['Cozy']
    with session() as s:
        text = mb.feedback_lessons(s, b['id'])
    assert 'TAGS THE OWNER DISAGREED WITH' in text and '"cozy" (1x)' in text and 'Beige knit sweater' in text
    assert client.put(f'{url}/tags', json={'disputed': []}).json()['pin']['vision_disputed'] == []
    with session() as s:
        assert 'DISAGREED' not in mb.feedback_lessons(s, b['id'])


def test_lessons_quote_why_owner_liked_a_picture(client):
    b = make(client)
    with session() as s:
        pin = MoodPin(board_id=b['id'], profile_id=1, title='Loved one', source_url='https://x.org/l',
                      image_url='https://x.org/l.jpg')
        s.add(pin)
        s.flush()
        s.add(MoodFeedback(pin_id=pin.id, board_id=b['id'], profile_id=1, verdict='good',
                           note='Great colours + Natural light', title=pin.title))
        s.commit()
        text = mb.feedback_lessons(s, b['id'])
    assert 'owner liked: "Great colours + Natural light"' in text


def test_describe_pass_feeds_judge_and_survives_failure(monkeypatch):
    calls = []
    def call(system, user, schema, **kw):
        calls.append(list(schema['properties']))
        if 'descriptions' in schema['properties']:
            n = schema['properties']['descriptions']['maxItems']
            if len(calls) == 1:
                return {'descriptions': [{'index': i, 'summary': f'velvet flares {i}', 'people': 'adult',
                                          'costume_like': False} for i in range(n)]}
            raise TimeoutError('busy')
        assert 'velvet flares 0' in user and 'no description' in user
        n = schema['properties']['verdicts']['maxItems']
        assert 'seen' in schema['properties']['verdicts']['items']['properties']  # one image lacks a description
        return {'verdicts': [{'index': 0, 'fits': True, 'keep_likelihood': 90, 'violations': [], 'why': 'Velvet flares',
                              'tags': ['Warm', 'Cozy', ' warm ', 'Velvet'] + [f'T{k}' for k in range(10)]},
                             {'index': 1, 'fits': True, 'keep_likelihood': 10, 'violations': [], 'why': 'Unsure'},
                             {'index': 2, 'fits': False, 'keep_likelihood': 5, 'violations': ['costume'], 'why': 'Hat'}][:n]}
    monkeypatch.setattr(mb.ai, '_call', call)
    rows = [{**cand(i), '_captured': (png(i), 'image/png', 64, 48)} for i in range(3)]
    kept = mb.select_candidates(rows, 'Style', 'Velvet')
    assert kept == [rows[0]]  # a 10 % keep-likelihood is too low for a strict board; fits=false is rejected
    assert rows[0]['_verdict']['seen'] == 'velvet flares 0'
    assert rows[0]['_verdict']['tags'] == ['Warm', 'Cozy', 'Velvet'] + [f'T{k}' for k in range(7)]  # distinct, max 10
    assert rows[1]['_judgement']['fits'] is False and rows[2]['_judgement']['violations'] == ['costume']
    assert calls[0][0] == 'descriptions' and calls[1][0] == 'descriptions' and calls[2][0] == 'verdicts'
    balanced = [{**cand(i), '_captured': (png(i), 'image/png', 64, 48)} for i in range(3)]
    calls.clear()
    assert mb.select_candidates(balanced, 'Style', 'Velvet', strict=False) == balanced[:2]


def test_lenient_judge_keeps_on_likelihood_but_child_boards_need_yes(monkeypatch):
    monkeypatch.setattr(mb, 'describe_images', lambda batch: [None] * len(batch))
    monkeypatch.setattr(mb.ai, '_call', lambda *a, **k: {'verdicts': [
        {'index': 0, 'seen': 's', 'fits': False, 'keep_likelihood': 50, 'violations': ['beige'], 'why': 'w'}]})
    rows = [{**cand(0), '_captured': (png(0), 'image/png', 64, 48)}]
    assert mb.select_candidates(rows, 'Style', 'Velvet') == rows  # a "no" with 50 % keep-likelihood is kept
    rows = [{**cand(0), '_captured': (png(0), 'image/png', 64, 48)}]
    assert mb.select_candidates(rows, 'Kids', 'Dinosaurs', child=True) == []


def test_run_filters_hosts_skips_duplicates_and_stores_verdicts(client, monkeypatch):
    b = make(client, avoid='rust, pexels.com')
    with session() as s:
        old = MoodPin(board_id=b['id'], profile_id=1, title='old', source_url='https://x.org/old', image_url='https://x.org/o.jpg')
        s.add(old)
        s.flush()
        s.add(MoodHash(pin_id=old.id, board_id=b['id'], dhash=bq.dhash(png(9))))
        s.commit()
    found = [cand(1, 'www.pexels.com'), cand(2), cand(3), cand(4)]
    monkeypatch.setattr(moodboard_sources, 'search', lambda *a, **kw: found)
    pixels = {'pic2': png(2), 'pic3': png(9, (128, 96)), 'pic4': png(4)}
    monkeypatch.setattr(mb, 'capture_image', lambda url: (pixels[url.rsplit('/', 1)[1][:4]], 'image/png', 64, 48))
    systems = []
    def call(system, user, schema, **kw):
        systems.append(system + user)
        if 'queries' in schema['properties']:
            return {'queries': ['velvet flares']}
        if 'descriptions' in schema['properties']:
            n = schema['properties']['descriptions']['maxItems']
            return {'descriptions': [{'index': i, 'summary': f'look {i}'} for i in range(n)]}
        n = schema['properties']['verdicts']['maxItems']
        return {'verdicts': [{'index': i, 'fits': i == 0, 'keep_likelihood': 80 if i == 0 else 10, 'violations': [],
                              'why': 'w'} for i in range(n)]}
    monkeypatch.setattr(mb.ai, '_call', call)
    assert mb.dispatch_due()
    judge = [t for t in systems if 'Curator instructions' in t][0]
    assert 'rust' in judge and 'pexels' not in judge.lower()  # host is a source filter, not a visual rule
    with session() as s:
        verdicts = s.exec(select(MoodVerdict).where(MoodVerdict.board_id == b['id'])).all()
        assert sorted(v.source_url.rsplit('Pic', 1)[1] for v in verdicts) == ['2.jpg', '4.jpg']  # 1 avoided, 3 duplicate
        kept = [v for v in verdicts if v.fits]
        assert len(kept) == 1 and kept[0].pin_id and kept[0].description['summary'] == 'look 0'
        assert s.get(MoodHash, kept[0].pin_id).dhash == bq.dhash(png(2))
        assert s.get(MoodPinNote, kept[0].pin_id).seen == 'look 0'
    sources = client.get(f"/api/hub/boards/{b['id']}/sources").json()
    assert sources['held_back'][0]['host'] == 'pexels.com'
    assert client.delete(f"/api/hub/boards/{b['id']}").status_code == 200
    with session() as s:
        assert not s.exec(select(MoodVerdict)).all() and not s.exec(select(MoodHash)).all()


def test_hash_sweep_backfills_and_prunes(client):
    import time
    from bt.hub.models import MoodAsset
    b = make(client)
    with session() as s:
        pin = MoodPin(board_id=b['id'], profile_id=1, title='t', source_url='https://x.org/1', image_url='https://x.org/1.jpg')
        s.add(pin)
        s.flush()
        s.add(MoodAsset(pin_id=pin.id, mime='image/png', data=png(1)))
        s.add(MoodVerdict(board_id=b['id'], created_at=time.time() - 40 * 86400))
        s.add(MoodVerdict(board_id=b['id'], pin_id=pin.id, created_at=time.time() - 40 * 86400))
        s.commit()
        pid = pin.id
    assert bq.hash_sweep() == 1 and bq.hash_sweep() == 0
    with session() as s:
        assert s.get(MoodHash, pid).dhash == bq.dhash(png(1))
        assert [v.pin_id for v in s.exec(select(MoodVerdict)).all()] == [pid]


def test_lessons_warn_when_owner_also_approves_the_kind(client):
    b = make(client)
    with session() as s:
        for n, (verdict, note, seen) in enumerate([('bad', 'Messy collage', 'Busy collage of boots and crystals'),
                                                   ('bad', 'messy', 'Digital collage of dresses on stars'),
                                                   ('good', '', 'Tidy collage of velvet flares and lace tops'),
                                                   ('good', '', 'Mood board collage in plum and green')]):
            pin = MoodPin(board_id=b['id'], profile_id=1, title='t', source_url=f'https://x.org/{n}', image_url='https://x.org/i.jpg')
            s.add(pin)
            s.flush()
            s.add(MoodPinNote(pin_id=pin.id, seen=seen, why=''))
            s.add(MoodFeedback(pin_id=pin.id, board_id=b['id'], profile_id=1, verdict=verdict, note=note, title='t'))
        s.commit()
        text = mb.feedback_lessons(s, b['id'])
    assert 'Messy collage' in text and 'BUT the owner also approved 2 pictures of this kind' in text


def test_cleanup_retires_title_inferred_restrictions_only(client):
    from bt.knowledge import cleanup, weave
    from bt.knowledge.models import Teaching
    b = make(client)
    with session() as s:
        pins = []
        for n, note in enumerate(['Off-topic for this board', '', 'Off-topic for this board', 'costume', 'teen']):
            pin = MoodPin(board_id=b['id'], profile_id=1, title='t', source_url=f'https://x.org/{n}', image_url='https://x.org/i.jpg')
            s.add(pin)
            s.flush()
            s.add(MoodFeedback(pin_id=pin.id, board_id=b['id'], profile_id=1, verdict='bad', note=note, title='t'))
            pins.append(pin.id)
        sig = lambda ids: [{'key': f'pin:{i}', 'ref_type': 'pin', 'ref_id': i} for i in ids]  # noqa: E731
        common = dict(profile_id=1, surface='moodboard', section='moodboards', origin='mood', board_id=b['id'])
        harsh = Teaching(text='Avoid urban shoots', polarity=-1, signals=sig(pins[:3]), origin_id='a', status='merged', **common)
        noted = Teaching(text='Avoid costume', polarity=-1, signals=sig(pins[3:]), origin_id='b', **common)
        liked = Teaching(text='Prefer velvet', polarity=1, signals=sig(pins[:3]), origin_id='c', **common)
        s.add_all([harsh, noted, liked])
        s.flush()
        sections = weave._normalized([])
        next(x for x in sections if x['key'] == 'moodboards')['statements'] = [
            {'sid': 's1', 'text': 'Avoid urban shoots', 'polarity': -1, 'teaching_ids': [harsh.id], 'board_id': b['id']},
            {'sid': 's2', 'text': 'Locked one', 'polarity': -1, 'teaching_ids': [harsh.id], 'locked': True, 'board_id': b['id']}]
        weave.write_version(s, 1, sections, 'migration', None)
        s.commit()
        ids = {'harsh': harsh.id, 'noted': noted.id, 'liked': liked.id}
    verdicts = {r['id']: r['action'] for r in cleanup.review(1)}
    assert verdicts == {ids['harsh']: 'retire', ids['noted']: 'keep', ids['liked']: 'keep'}
    out = cleanup.retire([ids['harsh'], ids['liked']])  # liked is not harsh but the owner of the tool asked: still retired
    assert sorted(out['retired']) == sorted([ids['harsh'], ids['liked']]) and out['versions'][1]['statements_removed'] == 1
    from bt.knowledge import render
    with session() as s:
        assert s.get(Teaching, ids['harsh']).status == 'superseded'
        st = [x for sec in render.current(s, 1).sections for x in sec['statements']]
        assert [x['sid'] for x in st] == ['s2']  # locked statement kept
