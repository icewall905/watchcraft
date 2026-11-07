import pytest

from test_curation import client  # noqa: F401  (fixture)
from test_music import RADIO, SEED, music  # noqa: F401  (fixtures)
from bt.music import lyrics

LRC = '[00:27.83] She was more like a beauty queen\n[00:32.96] I said dont mind\n[01:10.00][02:10.00] Billie Jean is not my lover\nno stamp here\n'


def test_clean_title_drops_video_noise_and_artist_prefix():
    assert lyrics.clean_title('Billie Jean (Official Video)', 'Michael Jackson') == 'Billie Jean'
    assert lyrics.clean_title('Michael Jackson - Billie Jean [HD Remastered]', 'Michael Jackson') == 'Billie Jean'
    assert lyrics.clean_title('Titanium (feat. Sia)', 'David Guetta') == 'Titanium'
    assert lyrics.main_artist('Jennifer Lopez & Pitbull') == 'Jennifer Lopez'


def test_parse_lrc_lines_repeats_and_words():
    lines = lyrics.parse_lrc(LRC)
    assert [(x['t'], x['text']) for x in lines] == [
        (27830, 'She was more like a beauty queen'), (32960, 'I said dont mind'),
        (70000, 'Billie Jean is not my lover'), (130000, 'Billie Jean is not my lover')]
    w = lyrics.parse_lrc('[00:01.00] <00:01.00> Hey <00:01.50> you')
    assert w[0]['text'] == 'Hey you' and [x['t'] for x in w[0]['words']] == [1000, 1500]


def test_best_match_prefers_timed_lyrics_closest_to_the_video_length():
    c = [{'id': 1, 'artistName': 'Michael Jackson', 'trackName': 'Billie Jean', 'duration': 290, 'syncedLyrics': '', 'plainLyrics': 'x'},
         {'id': 2, 'artistName': 'Michael Jackson', 'trackName': 'Billie Jean', 'duration': 297, 'syncedLyrics': LRC},
         {'id': 3, 'artistName': 'Michael Jackson', 'trackName': 'Billie Jean', 'duration': 292, 'syncedLyrics': LRC},
         {'id': 4, 'artistName': 'Cover Band', 'trackName': 'Billie Jean', 'duration': 294, 'syncedLyrics': LRC},
         {'id': 5, 'artistName': 'Michael Jackson', 'trackName': 'Billie Jean (12" mix)', 'duration': 400, 'syncedLyrics': LRC}]
    assert lyrics.best_match(c, 'Michael Jackson', 'Billie Jean', 294)['id'] == 3


def test_align_offset_uses_the_agreeing_lines():
    lines = [{'t': 10_000, 'text': 'she was more like a beauty queen'}, {'t': 20_000, 'text': 'from a movie scene tonight'},
             {'t': 30_000, 'text': 'billie jean is not my lover'}, {'t': 40_000, 'text': 'la la'}]
    cues = [{'t': 14_000, 'end': 16_000, 'text': 'She was more like a beauty queen'},
            {'t': 24_000, 'end': 26_000, 'text': 'from a movie scene tonight'},
            {'t': 34_000, 'end': 36_000, 'text': 'Billie Jean is not my lover'}]
    assert abs(lyrics.align_offset(lines, cues) - 4000) < 800
    assert lyrics.align_offset(lines[:2], cues) is None


WORDS = ('amber river morning light falls across the quiet harbour stones while seven silver birds '
         'circle older towers under heavy painted clouds that drift beyond the northern hills tonight').split()


def _song(n=18, start=10000):
    """Made-up lyric lines (no real song text) at uneven gaps; line 5 repeats line 1 like a chorus."""
    gaps = (5300, 7900, 6400, 9100, 4700, 8300)
    lines, t = [], start
    for i in range(n):
        k = 1 if i == 5 else i
        lines.append({'t': t, 'text': ' '.join(WORDS[(k * 3) % 20:(k * 3) % 20 + 4])})
        t += gaps[i % len(gaps)]
    return lines


def _transcript(lines, rate=1.0, offset=0, junk=True):
    """What speech-to-text returns: chunks of about two lines, cut at the pauses between lines (as the
    chunker does), each one cue without word timing; plus music junk at the start."""
    sung = []
    for ln in lines:
        toks = ln['text'].split()
        sung.append((rate * ln['t'] + offset, rate * (ln['t'] + 450 * len(toks)) + offset, toks))
    cues = [{'t': 0, 'end': int(max(1, sung[0][0] - 500)), 'text': '呜呜呜呜 oh, oh, oh, oh, oh'}] if junk else []
    for i in range(0, len(sung), 2):
        group = sung[i:i + 2]
        cues.append({'t': int(group[0][0] - 300), 'end': int(group[-1][1] + 300),
                     'text': ' '.join(w for g in group for w in g[2])})
    return cues


@pytest.mark.parametrize('rate,offset', [(1.0, 0), (1.0, 21000), (1.0, -3500), (1.08, 800), (0.93, 12000)])
def test_fit_recovers_shift_and_speed(rate, offset):
    lines = _song()
    f = lyrics.fit(lines, _transcript(lines, rate, offset))
    assert f is not None
    for ln in lines:  # every line lands within a second of where it is really sung
        assert abs(f['rate'] * ln['t'] + f['offset_ms'] - (rate * ln['t'] + offset)) < 1000, (ln, f)
    if rate == 1.0:
        assert f['rate'] == 1.0


def test_fit_refuses_unrelated_transcript():
    lines = _song()
    other = [{'t': i * 8000, 'end': i * 8000 + 8000, 'text': 'completely different words about nothing here'} for i in range(20)]
    assert lyrics.fit(lines, other) is None


def test_parse_vtt():
    vtt = 'WEBVTT\n\n00:00:01.000 --> 00:00:03.500\nHello <b>there</b>\n\n1\n00:01:02.250 --> 00:01:04.000\nAgain\n'
    assert lyrics.parse_vtt(vtt) == [{'t': 1000, 'end': 3500, 'text': 'Hello there'}, {'t': 62250, 'end': 64000, 'text': 'Again'}]


@pytest.fixture
def lrclib(monkeypatch):
    calls = []

    def fake(params):
        calls.append(params)
        if params.get('track_name') == 'Dancing Queen':
            return [{'id': 9, 'artistName': 'ABBA', 'trackName': 'Dancing Queen', 'duration': 200, 'syncedLyrics': LRC,
                     'plainLyrics': 'plain'}]
        return []
    monkeypatch.setattr(lyrics, '_fetch', fake)
    return calls


def test_lyrics_endpoint_caches_and_reports_missing(music, lrclib):  # noqa: F811
    music.get('/api/music/search', params={'q': 'abba'})  # stores SEED + Gimme
    r = music.get(f'/api/music/lyrics/{SEED["video_id"]}').json()
    assert r['status'] == 'found' and r['synced'] and r['lines'][0]['t'] == 27830 and r['offset_ms'] == 0
    assert 'transcript' not in r
    music.get(f'/api/music/lyrics/{SEED["video_id"]}')
    assert len(lrclib) == 1  # cached
    miss = music.get(f'/api/music/lyrics/{RADIO[4]["video_id"]}').json()
    assert miss['status'] == 'missing' and miss['transcript']['status'] == 'idle'


def test_offset_nudge_is_bounded_and_needs_lyrics(music, lrclib):  # noqa: F811
    music.get('/api/music/search', params={'q': 'abba'})
    vid = SEED['video_id']
    assert music.post(f'/api/music/lyrics/{vid}/offset', json={'offset_ms': 1500}).status_code == 400  # not fetched
    music.get(f'/api/music/lyrics/{vid}')
    assert music.post(f'/api/music/lyrics/{vid}/offset', json={'offset_ms': 1500}).json()['offset_ms'] == 1500
    assert music.post(f'/api/music/lyrics/{vid}/offset', json={'offset_ms': 10**7}).status_code == 422


def test_listen_aligns_lyrics_with_the_transcript(music, lrclib, monkeypatch):  # noqa: F811
    from bt.music import api as music_api
    music.get('/api/music/search', params={'q': 'abba'})
    vid = SEED['video_id']
    queued = []
    state = {'status': 'idle'}
    monkeypatch.setattr(music_api.subtitle_jobs, 'status', lambda v: {'enabled': True, 'status': state['status'], 'progress': 0,
                                                                     'track': {'url': 'x'} if state['status'] == 'ready' else None})
    monkeypatch.setattr(music_api.subtitle_jobs, 'enqueue', lambda v, pid: (queued.append(v), state.update(status='queued')))
    r = music.post(f'/api/music/lyrics/{vid}/listen').json()
    assert queued == [vid] and r['transcript']['status'] == 'queued' and not r['aligned']
    from bt.db import session
    from bt.models import GeneratedSubtitles
    with session() as s:
        s.add(GeneratedSubtitles(video_id=vid, status='ready', vtt='WEBVTT\n\n00:00:29.830 --> 00:00:31.000\nShe was more like a beauty queen\n\n'
                                 '00:00:34.960 --> 00:00:36.000\nI said dont mind\n\n00:01:12.000 --> 00:01:14.000\nBillie Jean is not my lover\n'))
        s.commit()
    state['status'] = 'ready'
    r = music.post(f'/api/music/lyrics/{vid}/listen').json()
    assert r['aligned'] and abs(r['lyrics']['offset_ms'] - 2000) < 800 and r['lyrics']['offset_source'] == 'auto'
    assert r['lyrics']['rate'] == 1.0 and r['matched_lines'] == 3


def test_listen_refuses_when_speech_to_text_is_off(music, monkeypatch):  # noqa: F811
    from bt.music import api as music_api
    monkeypatch.setattr(music_api.subtitle_jobs, 'status', lambda v: {'enabled': False, 'status': 'idle', 'progress': 0, 'track': None})
    assert music.post(f'/api/music/lyrics/{SEED["video_id"]}/listen').status_code == 409


# ---------- saving mixes

def test_save_playing_queue_as_mix_copy_rename_and_recent(music):  # noqa: F811
    m = music.post('/api/music/mixes', json={'kind': 'manual', 'name': 'Friday DJ set', 'tracks': [SEED, RADIO[1], SEED]}).json()
    assert [t['video_id'] for t in m['tracks']] == [SEED['video_id'], RADIO[1]['video_id']]
    radio = music.post('/api/music/mixes', json={'kind': 'seed', 'seed': SEED}).json()
    copy = music.post(f'/api/music/mixes/{radio["id"]}/copy', json={}).json()
    assert copy['kind'] == 'manual' and copy['name'].endswith('(saved)') and len(copy['tracks']) == len(RADIO)
    assert music.patch(f'/api/music/mixes/{copy["id"]}', json={'name': 'Keepers'}).json()['name'] == 'Keepers'
    music.post(f'/api/music/mixes/{m["id"]}/played')
    recent = music.get('/api/music/home').json()['recent_mixes']
    assert recent[0]['id'] == m['id']


def test_racing_first_lookups_do_not_fail(music, monkeypatch):  # noqa: F811
    """Two requests miss the cache together; the slower one finds the other's row instead of crashing."""
    from bt.db import session
    from bt.music.models import MusicLyrics
    music.get('/api/music/search', params={'q': 'abba'})
    vid = SEED['video_id']

    def slow(artist, title, duration):  # meanwhile another request stores the lyrics first
        with session() as other:
            other.add(MusicLyrics(video_id=vid, status='found', synced=True, lines=[{'t': 1, 'text': 'first'}],
                                  fetched_at=1e12))
            other.commit()
        return {'source': 'lrclib', 'source_id': 1, 'synced': True, 'lines': [], 'plain': '', 'duration': 1}
    monkeypatch.setattr(lyrics, 'lookup', slow)
    r = music.get(f'/api/music/lyrics/{vid}')
    assert r.status_code == 200 and r.json()['status'] == 'found'


def test_failed_refresh_keeps_found_lyrics(music, lrclib, monkeypatch):  # noqa: F811
    music.get('/api/music/search', params={'q': 'abba'})
    vid = SEED['video_id']
    music.get(f'/api/music/lyrics/{vid}')

    def down(*a):
        raise RuntimeError('lrclib down')
    monkeypatch.setattr(lyrics, 'lookup', down)
    r = music.get(f'/api/music/lyrics/{vid}', params={'refresh': True}).json()
    assert r['status'] == 'found' and r['lines']  # still sung along to while LRCLIB is down


def test_full_mix_ignores_songs_it_already_has(music, monkeypatch):  # noqa: F811
    from bt.music import service
    monkeypatch.setattr(service, 'MAX_MIX_TRACKS', 2)
    m = music.post('/api/music/mixes', json={'kind': 'manual', 'name': 'Tiny', 'tracks': [SEED, RADIO[1]]}).json()
    assert music.post(f'/api/music/mixes/{m["id"]}/tracks', json=SEED).status_code == 200
    assert music.post(f'/api/music/mixes/{m["id"]}/tracks', json=RADIO[2]).status_code == 400


def test_previous_release_mix_table_gains_new_columns(client):  # noqa: F811
    from sqlalchemy import inspect
    from bt.db import engine, init_db
    with engine.begin() as c:
        c.exec_driver_sql('ALTER TABLE musicmix DROP COLUMN last_played_at')
        c.exec_driver_sql('ALTER TABLE musicmix DROP COLUMN build_started')
        c.exec_driver_sql('ALTER TABLE musiclyrics DROP COLUMN rate')
    init_db()
    cols = {c['name'] for c in inspect(engine).get_columns('musicmix')}
    assert {'last_played_at', 'build_started'} <= cols
    assert 'rate' in {c['name'] for c in inspect(engine).get_columns('musiclyrics')}


def test_length_hint_never_forces_a_speed_change():
    """A radio cut is shorter than the record without running faster: the hint must not bend it."""
    lines = _song()
    f = lyrics.fit(lines, _transcript(lines, 1.0, 21000), hint_rate=0.91)
    assert f['rate'] == 1.0 and abs(f['offset_ms'] - 21000) < 1000


def test_fit_ignores_a_chorus_sung_many_times():
    lines = _song(24)
    for i in (9, 13, 17, 21):  # the same chorus line four more times
        lines[i] = dict(lines[i], text=lines[1]['text'])
    f = lyrics.fit(lines, _transcript(lines, 1.0, 7000))
    assert f['rate'] == 1.0 and abs(f['offset_ms'] - 7000) < 1000


def test_hint_lets_a_close_slope_win():
    lines = _song(24)
    f = lyrics.fit(lines, _transcript(lines, 1.07, 1500), hint_rate=1.08)
    assert abs(f['rate'] - 1.07) < 0.01


def test_manual_nudge_keeps_the_fitted_speed(music, lrclib):  # noqa: F811
    from bt.db import session
    music.get('/api/music/search', params={'q': 'abba'})
    vid = SEED['video_id']
    music.get(f'/api/music/lyrics/{vid}')
    with session() as s:
        lyrics.set_offset(s, vid, 2000, 'auto', 1.06)
    r = music.post(f'/api/music/lyrics/{vid}/offset', json={'offset_ms': 2500}).json()
    assert r['offset_ms'] == 2500 and r['rate'] == 1.06 and r['offset_source'] == 'manual'


def test_new_recording_drops_an_old_auto_fit(music, lrclib, monkeypatch):  # noqa: F811
    from bt.db import session
    music.get('/api/music/search', params={'q': 'abba'})
    vid = SEED['video_id']
    music.get(f'/api/music/lyrics/{vid}')
    with session() as s:
        lyrics.set_offset(s, vid, 4000, 'auto', 1.05)
    monkeypatch.setattr(lyrics, 'lookup', lambda *a: {'source': 'lrclib', 'source_id': 77, 'synced': True,
                                                      'lines': [{'t': 1, 'text': 'x'}], 'plain': '', 'duration': 1})
    r = music.get(f'/api/music/lyrics/{vid}', params={'refresh': True}).json()
    assert r['rate'] == 1.0 and r['offset_ms'] == 0
