import pytest

from test_curation import client  # noqa: F401  (fixture)
from bt.music import dj, service, yt


def _t(vid: str, title: str, artist: str, **kw) -> dict:
    return {'video_id': vid, 'title': title, 'artist': artist, 'artist_id': kw.get('aid', ''), 'views': 1000,
            'duration_s': 200, 'thumbnail': f'https://i.ytimg.com/vi/{vid}/hqdefault.jpg'}


SEED = _t('AAAAAAAAAAA', 'Dancing Queen', 'ABBA')
RADIO = [SEED, _t('BBBBBBBBBBB', 'Titanium', 'David Guetta'), _t('CCCCCCCCCCC', 'Halo', 'Beyoncé'),
         _t('DDDDDDDDDDD', 'Take On Me', 'a-ha'), _t('EEEEEEEEEEE', 'Gimme Gimme Gimme', 'ABBA')]
SEARCH = {'abba': [SEED, RADIO[4]], 'beyonce halo': [RADIO[2]], 'a-ha take on me': [RADIO[3]]}


@pytest.fixture
def music(client, monkeypatch):  # noqa: F811
    calls = []
    monkeypatch.setattr(yt, 'radio', lambda vid, origin='user': (calls.append(('radio', vid)), list(RADIO))[1])
    monkeypatch.setattr(yt, 'search', lambda q, origin='user': (calls.append(('search', q)),
                                                                 list(SEARCH.get(q.lower(), RADIO[1:3])))[1])
    monkeypatch.setattr(yt, 'prepare', lambda vid: calls.append(('prepare', vid)))
    monkeypatch.setattr(service, 'spawn', lambda fn, *a: fn(*a))
    monkeypatch.setattr(dj.ai, 'enabled', lambda: False)
    service._for_you.clear()
    client.calls = calls
    return client


# ---------- InnerTube parsing

def _run(text, browse=''):
    r = {'text': text}
    if browse:
        r['navigationEndpoint'] = {'browseEndpoint': {'browseId': browse}}
    return r


def _search_item(vid, title, mtype, byline):
    return {'musicResponsiveListItemRenderer': {
        'playlistItemData': {'videoId': vid},
        'overlay': {'x': {'watchEndpoint': {'videoId': vid, 'watchEndpointMusicSupportedConfigs': {
            'watchEndpointMusicConfig': {'musicVideoType': mtype}}}}},
        'flexColumns': [{'musicResponsiveListItemFlexColumnRenderer': {'text': {'runs': [_run(title)]}}},
                        {'musicResponsiveListItemFlexColumnRenderer': {'text': {'runs': byline}}}]}}


def test_parse_search_keeps_only_official_music_videos():
    resp = {'contents': [
        _search_item('J294A-R1Cjk', 'Dancing on My Own', yt.OMV,
                     [_run('Robyn', 'UCtUP6i_G7Zf_e1ndgiRfi1g'), _run(' • '), _run('38M views'), _run(' • '), _run('4:07')]),
        _search_item('ASVo7oZjNz0', 'Dancing On My Own | Drum Cover', 'MUSIC_VIDEO_TYPE_UGC',
                     [_run('Someone'), _run(' • '), _run('3.8K views'), _run(' • '), _run('5:10')]),
        _search_item('-sVB91NTa4A', 'Dancing Queen (Official Lyric Video)', yt.OMV,
                     [_run('ABBA'), _run(' • '), _run('36M views'), _run(' • '), _run('3:54')]),
    ]}
    assert yt.parse_search(resp) == [{
        'video_id': 'J294A-R1Cjk', 'title': 'Dancing on My Own', 'artist': 'Robyn',
        'artist_id': 'UCtUP6i_G7Zf_e1ndgiRfi1g', 'views': 38_000_000, 'duration_s': 247,
        'thumbnail': 'https://i.ytimg.com/vi/J294A-R1Cjk/hqdefault.jpg'}]


def test_parse_radio_reads_length_and_joined_artists():
    item = {'playlistPanelVideoRenderer': {
        'videoId': 't4H_Zoh7G5A', 'title': {'runs': [_run('On The Floor')]},
        'longBylineText': {'runs': [_run('Jennifer Lopez', 'UCabcabcabcabcabcabcabca'), _run(' & '), _run('Pitbull'),
                                    _run(' • '), _run('2.9B views'), _run(' • '), _run('12M likes')]},
        'lengthText': {'runs': [_run('4:27')]},
        'navigationEndpoint': {'watchEndpoint': {'watchEndpointMusicSupportedConfigs': {
            'watchEndpointMusicConfig': {'musicVideoType': yt.OMV}}}}}}
    atv = {'playlistPanelVideoRenderer': {**item['playlistPanelVideoRenderer'], 'videoId': 'xxxxxxxxxxx',
                                          'navigationEndpoint': {}}}
    out = yt.parse_radio({'a': [item, atv]})
    assert [(t['video_id'], t['artist'], t['duration_s'], t['views']) for t in out] == [
        ('t4H_Zoh7G5A', 'Jennifer Lopez & Pitbull', 267, 2_900_000_000)]


def test_duration_parses_clock_text_only():
    assert yt.duration('1:02:03') == 3723 and yt.duration('4:07') == 247 and yt.duration('38M views') == 0


# ---------- favorites, search and suggestions

def test_favorites_tracks_and_artists_round_trip(music):
    r = music.post('/api/music/favorites', json={'kind': 'track', 'track': SEED})
    assert [t['video_id'] for t in r.json()['tracks']] == [SEED['video_id']]
    r = music.post('/api/music/favorites', json={'kind': 'artist', 'artist': 'ABBA'})
    assert r.json()['artists'] == [{'name': 'ABBA', 'artist_id': '', 'key': 'abba'}]
    hits = music.get('/api/music/search', params={'q': 'abba'}).json()['tracks']
    assert hits[0]['fav'] and hits[0]['fav_artist'] and not hits[1]['fav'] and hits[1]['fav_artist']
    music.post('/api/music/favorites', json={'kind': 'track', 'track': SEED, 'on': False})
    assert music.get('/api/music/favorites').json()['tracks'] == []


def test_favorites_are_per_profile(music):
    music.post('/api/music/favorites', json={'kind': 'track', 'track': SEED})
    pid2 = music.post('/api/profiles', json={'name': 'Guest', 'role': 'adult'}).json()['id']
    other = music.get('/api/music/favorites', headers={'X-Profile-Id': str(pid2)}).json()
    assert other['tracks'] == []


def test_artist_page_keeps_only_that_artist(music):
    r = music.get('/api/music/artist', params={'name': 'ABBA'}).json()
    assert {t['artist'] for t in r['tracks']} == {'ABBA'}


def test_for_you_has_loved_and_fresh_shelves(music):
    music.post('/api/music/favorites', json={'kind': 'track', 'track': SEED})
    r = music.get('/api/music/for-you').json()
    assert [t['video_id'] for t in r['loved']] == [SEED['video_id']]
    fresh = [t['video_id'] for t in r['fresh']]
    assert fresh and SEED['video_id'] not in fresh
    assert all(t['reason'] == 'New for you' for t in r['fresh'])


def test_for_you_cold_start_searches_party_music(music):
    r = music.get('/api/music/for-you').json()
    assert r['loved'] == [] and r['fresh']
    assert ('search', service.COLD_START[0]) in music.calls


# ---------- mixes

def test_seed_mix_builds_from_track_radio(music):
    m = music.post('/api/music/mixes', json={'kind': 'seed', 'seed': SEED}).json()
    got = music.get(f'/api/music/mixes/{m["id"]}').json()
    assert got['status'] == 'ready' and got['kind'] == 'seed'
    assert [t['video_id'] for t in got['tracks']] == [t['video_id'] for t in RADIO]


def test_curator_mix_without_model_searches_the_description(music):
    m = music.post('/api/music/mixes', json={'kind': 'curator', 'description': 'abba', 'curator': 'retro'}).json()
    got = music.get(f'/api/music/mixes/{m["id"]}').json()
    assert got['status'] == 'ready' and got['curator_name'] == 'Rewind Rae'
    assert [t['video_id'] for t in got['tracks']] == [SEED['video_id'], RADIO[4]['video_id']]


def test_curator_mix_with_model_resolves_named_songs_first(music, monkeypatch):
    monkeypatch.setattr(dj.ai, 'enabled', lambda: True)
    monkeypatch.setattr(dj.ai, '_call', lambda *a, **kw: {
        'name': 'Disco Nap', 'emoji': '🪩', 'blurb': 'Pure joy.', 'queries': ['abba'],
        'picks': [{'artist': 'Beyoncé', 'title': 'Halo'}, {'artist': 'Nobody', 'title': 'Unknown song'}]})
    m = music.post('/api/music/mixes', json={'kind': 'curator', 'description': 'happy disco'}).json()
    got = music.get(f'/api/music/mixes/{m["id"]}').json()
    assert got['name'] == 'Disco Nap' and got['blurb'] == 'Pure joy.'
    assert [t['video_id'] for t in got['tracks']][:2] == ['CCCCCCCCCCC', SEED['video_id']]


def test_failed_mix_reports_error(music, monkeypatch):
    monkeypatch.setattr(yt, 'radio', lambda vid, origin='user': [])
    m = music.post('/api/music/mixes', json={'kind': 'seed', 'seed': SEED}).json()
    got = music.get(f'/api/music/mixes/{m["id"]}').json()
    assert got['status'] == 'error' and 'No official music videos' in got['error']


def test_manual_mix_add_remove_and_ownership(music):
    m = music.post('/api/music/mixes', json={'kind': 'manual', 'name': 'Party'}).json()
    music.post(f'/api/music/mixes/{m["id"]}/tracks', json=SEED)
    music.post(f'/api/music/mixes/{m["id"]}/tracks', json=RADIO[1])
    music.post(f'/api/music/mixes/{m["id"]}/tracks', json=SEED)  # no duplicates
    got = music.get(f'/api/music/mixes/{m["id"]}').json()
    assert [t['video_id'] for t in got['tracks']] == [SEED['video_id'], RADIO[1]['video_id']]
    pid2 = music.post('/api/profiles', json={'name': 'Guest', 'role': 'adult'}).json()['id']
    assert music.get(f'/api/music/mixes/{m["id"]}', headers={'X-Profile-Id': str(pid2)}).status_code == 404
    got = music.delete(f'/api/music/mixes/{m["id"]}/tracks/{SEED["video_id"]}').json()
    assert [t['video_id'] for t in got['tracks']] == [RADIO[1]['video_id']]
    assert music.post(f'/api/music/mixes/{m["id"]}/refresh').status_code == 400
    assert music.delete(f'/api/music/mixes/{m["id"]}').json() == {'ok': True}
    assert music.get('/api/music/mixes').json()['mixes'] == []


def test_stale_curator_mix_refreshes_when_listed(music):
    m = music.post('/api/music/mixes', json={'kind': 'curator', 'description': 'abba'}).json()
    from bt.db import session
    from bt.music.models import MusicMix
    with session() as s:
        row = s.get(MusicMix, m['id'])
        row.refreshed_at = 1
        s.add(row)
        s.commit()
    music.calls.clear()
    music.get('/api/music/mixes')
    assert ('search', 'abba') in music.calls


# ---------- the DJ

def test_dj_without_model_plays_radio_and_skips_played(music):
    music.post('/api/music/plays', json={'video_id': SEED['video_id']})
    music.get('/api/music/search', params={'q': 'abba'})  # stores SEED so played ids resolve
    r = music.post('/api/music/dj', json={'played': [SEED['video_id']], 'queue': ['BBBBBBBBBBB'], 'n': 2}).json()
    ids = [t['video_id'] for t in r['tracks']]
    assert r['source'] == 'radio' and len(ids) == 2 and r['line']
    assert SEED['video_id'] not in ids and 'BBBBBBBBBBB' not in ids
    assert ('radio', SEED['video_id']) in music.calls and ('prepare', ids[0]) in music.calls


def test_dj_avoids_same_artist_back_to_back(music):
    music.get('/api/music/search', params={'q': 'abba'})
    for _ in range(5):
        r = music.post('/api/music/dj', json={'played': [SEED['video_id']], 'n': 1}).json()
        assert r['tracks'][0]['artist'] != 'ABBA'


def test_dj_guest_request_comes_first(music):
    r = music.post('/api/music/dj', json={'request': 'beyonce halo', 'n': 2}).json()
    assert r['tracks'][0]['video_id'] == 'CCCCCCCCCCC' and r['tracks'][0]['reason'] == 'Guest request'
    assert 'beyonce halo' in r['line']


def test_dj_with_model_uses_its_picks_and_line(music, monkeypatch):
    monkeypatch.setattr(dj.ai, 'enabled', lambda: True)
    monkeypatch.setattr(dj.ai, '_call', lambda *a, **kw: {
        'line': 'Hands up!', 'picks': [{'artist': 'a-ha', 'title': 'Take On Me', 'why': '80s gold'}]})
    r = music.post('/api/music/dj', json={'n': 2}).json()
    assert r['source'] == 'ai' and r['line'] == 'Hands up!'
    assert r['tracks'][0]['video_id'] == 'DDDDDDDDDDD' and r['tracks'][0]['reason'] == '80s gold'
    assert len(r['tracks']) == 2  # radio fills what the model's picks could not


def test_dj_model_failure_falls_back(music, monkeypatch):
    monkeypatch.setattr(dj.ai, 'enabled', lambda: True)

    def boom(*a, **kw):
        raise RuntimeError('model down')
    monkeypatch.setattr(dj.ai, '_call', boom)
    r = music.post('/api/music/dj', json={'vibe': 'abba', 'n': 1}).json()
    assert r['source'] == 'radio' and r['tracks']


def test_home_lists_curators_and_recent(music):
    music.get('/api/music/search', params={'q': 'abba'})
    music.post('/api/music/plays', json={'video_id': SEED['video_id']})
    music.post('/api/music/plays', json={'video_id': RADIO[4]['video_id'], 'skipped': True})
    r = music.get('/api/music/home').json()
    assert [t['video_id'] for t in r['recent']] == [SEED['video_id']]
    assert {c['key'] for c in r['curators']} == set(dj.CURATORS) and r['ai'] is False


def test_music_is_hub_gated_for_children(music, monkeypatch):
    from bt import access
    monkeypatch.setattr(access, 'hub_allowed', lambda s, pid: False)  # what a child profile gets
    assert music.get('/api/music/home').status_code == 403
    assert music.post('/api/music/dj', json={}).status_code == 403


def test_client_cannot_rewrite_shared_track_details(music):
    music.get('/api/music/search', params={'q': 'abba'})  # YouTube's own details are stored
    forged = {**SEED, 'title': 'Hacked', 'artist': 'Mallory', 'thumbnail': 'https://evil.example/pixel.gif'}
    music.post('/api/music/favorites', json={'kind': 'track', 'track': forged})
    t = music.get('/api/music/favorites').json()['tracks'][0]
    assert (t['title'], t['artist']) == ('Dancing Queen', 'ABBA')
    assert t['thumbnail'] == f'https://i.ytimg.com/vi/{SEED["video_id"]}/hqdefault.jpg'
    new = {**RADIO[1], 'video_id': 'NNNNNNNNNNN', 'thumbnail': 'https://evil.example/x.gif'}
    music.post('/api/music/favorites', json={'kind': 'track', 'track': new})  # unseen: fills in
    t = [x for x in music.get('/api/music/favorites').json()['tracks'] if x['video_id'] == 'NNNNNNNNNNN'][0]
    assert t['title'] == 'Titanium' and 'evil' not in t['thumbnail']


def test_track_numbers_are_bounded(music):
    r = music.post('/api/music/favorites', json={'kind': 'track', 'track': {**SEED, 'views': 2 ** 70}})
    assert r.status_code == 422


def _age(mid, **fields):
    from bt.db import session
    from bt.music.models import MusicMix
    with session() as s:
        row = s.get(MusicMix, mid)
        for k, v in fields.items():
            setattr(row, k, v)
        s.add(row)
        s.commit()


def test_dead_build_is_retried_and_live_one_is_not(music, monkeypatch):
    m = music.post('/api/music/mixes', json={'kind': 'seed', 'seed': SEED}).json()
    _age(m['id'], status='building', build_started=1)  # its thread died long ago
    music.get('/api/music/mixes')
    assert music.get(f'/api/music/mixes/{m["id"]}').json()['status'] == 'ready'
    import time
    _age(m['id'], status='building', build_started=time.time())
    music.calls.clear()
    music.get('/api/music/mixes')
    music.post(f'/api/music/mixes/{m["id"]}/refresh')
    assert not [c for c in music.calls if c[0] == 'radio']


def test_listing_refreshes_one_stale_curator_mix_at_a_time(music):
    ids = [music.post('/api/music/mixes', json={'kind': 'curator', 'description': 'abba'}).json()['id'] for _ in range(3)]
    for mid in ids:
        _age(mid, refreshed_at=1)
    music.calls.clear()
    music.get('/api/music/mixes')
    assert len([c for c in music.calls if c == ('search', 'abba')]) == 1
