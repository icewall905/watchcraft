import os
import tempfile

os.environ.setdefault('BT_DATA_DIR', tempfile.mkdtemp(prefix='bt-jellyfin-test-'))

import json
import urllib.parse as up

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from bt.db import drop_kv, init_db, session
from bt.jellyfin import client as jf
from bt.jellyfin.models import JellyfinLink
from bt.main import app

BASE = 'http://jf.lan:8096'
MOVIE = 'a' * 32
TRAILER = '1' * 32
PERSON = '2' * 32
SERIES = 'b' * 32
EP1, EP2 = 'c' * 32, 'd' * 32
TOKEN = 'secret-jellyfin-token'


class FakeJellyfin:
    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.direct = True
        self.local_trailer = True

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        p, q = req.url.path, dict(req.url.params)
        if p == '/System/Info/Public':
            return httpx.Response(200, json={'Id': 'srv', 'ServerName': 'Den', 'Version': '10.10.3'})
        if p == '/Users/AuthenticateByName':
            body = json.loads(req.content)
            if body['Pw'] != 'pw':
                return httpx.Response(401)
            return httpx.Response(200, json={'AccessToken': TOKEN, 'ServerId': 'srv',
                                             'User': {'Id': 'u1', 'Name': body['Username'],
                                                      'Policy': {'IsAdministrator': True}}})
        assert TOKEN in req.headers.get('authorization', ''), p
        if p == '/UserViews':
            return httpx.Response(200, json={'Items': [{'Id': 'e' * 32, 'Name': 'Movies', 'CollectionType': 'movies',
                                                        'ImageTags': {'Primary': 't1'}}]})
        if p == '/Items/Latest':
            return httpx.Response(200, json=[self.movie()])
        if p in ('/UserItems/Resume', '/Shows/NextUp'):
            return httpx.Response(200, json={'Items': [self.movie(position=600)] if p.endswith('Resume') else []})
        if p == f'/Items/{MOVIE}':
            return httpx.Response(200, json=self.movie(detail=True))
        if p == f'/Items/{MOVIE}/LocalTrailers':
            assert q['userId'] == 'u1'
            return httpx.Response(200, json=[{'Id': TRAILER, 'Type': 'Trailer'}] if self.local_trailer else [])
        if p == f'/Items/{TRAILER}':
            return httpx.Response(200, json={'Id': TRAILER, 'Name': 'Official Trailer', 'Type': 'Trailer'})
        if p == f'/Items/{SERIES}':
            return httpx.Response(200, json={'Id': SERIES, 'Type': 'Series', 'OfficialRating': 'TV-14', 'Genres': ['Drama']})
        if p == f'/Items/{EP1}':
            return httpx.Response(200, json={'Id': EP1, 'Name': 'Pilot', 'Type': 'Episode', 'SeriesId': SERIES,
                                             'RunTimeTicks': 1800 * jf.TICKS, 'MediaSources': [{'Id': EP1}]})
        if p == f'/Shows/{SERIES}/Episodes':
            assert q['startItemId'] == EP1
            return httpx.Response(200, json={'Items': [{'Id': EP1, 'Type': 'Episode'},
                                                       {'Id': EP2, 'Name': 'Second', 'Type': 'Episode'}]})
        if p == '/Items/Filters':
            return httpx.Response(200, json={'Genres': ['Comedy', 'Action'],
                                             'OfficialRatings': ['G', 'PG', 'FSK-6', 'PG-13', 'R', 'NR', 'de/12']})
        if p == '/Items':
            return httpx.Response(200, json={'Items': [self.movie()], 'TotalRecordCount': 1})
        if p.endswith('/PlaybackInfo'):
            item = p.split('/')[2]
            body = json.loads(req.content)
            src = {'Id': item, 'RunTimeTicks': 7200 * jf.TICKS, 'DefaultAudioStreamIndex': 1,
                   'MediaStreams': self.streams()}
            if self.direct and body['EnableDirectPlay']:
                src['SupportsDirectPlay'] = True
            else:
                src['SupportsDirectStream'] = False
                src['TranscodingUrl'] = (f'/videos/{item}/master.m3u8?DeviceId=x&MediaSourceId={item}'
                                         f'&VideoCodec=h264&ApiKey={TOKEN}&StartTimeTicks=600&TranscodeReasons=VideoCodecNotSupported')
            return httpx.Response(200, json={'PlaySessionId': 'ps1', 'MediaSources': [src]})
        if p.startswith('/Sessions/Playing') or p == '/Videos/ActiveEncodings' or p.startswith('/UserPlayedItems'):
            return httpx.Response(204)
        if p == '/Sessions':
            return httpx.Response(200, json=[{'PlayState': {'PlaySessionId': 'ps1', 'PlayMethod': 'Transcode'},
                                              'TranscodingInfo': {'VideoCodec': 'h264', 'HardwareAccelerationType': 'vaapi',
                                                                  'TranscodeReasons': ['VideoCodecNotSupported']}}])
        if p == '/System/Configuration/encoding':
            return httpx.Response(200, json={'HardwareAccelerationType': 'nvenc', 'HardwareDecodingCodecs': ['h264', 'hevc'],
                                             'AllowHevcEncoding': True})
        if p == f'/videos/{MOVIE}/master.m3u8':
            return httpx.Response(200, headers={'content-type': 'application/x-mpegURL'}, text=(
                '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\n'
                f'main.m3u8?DeviceId=x&api_key={TOKEN}\n'
                f'#EXT-X-MEDIA:TYPE=SUBTITLES,URI="/videos/{MOVIE}/subs.m3u8?ApiKey={TOKEN}"\n'))
        if p == f'/Videos/{MOVIE}/stream':
            assert req.headers.get('range') == 'bytes=0-9'
            return httpx.Response(206, headers={'content-type': 'video/mp4', 'content-range': 'bytes 0-9/100'},
                                  content=b'0123456789')
        if p.startswith(f'/Items/{MOVIE}/Images/'):
            return httpx.Response(200, headers={'content-type': 'image/jpeg'}, content=b'jpg')
        return httpx.Response(404)

    @staticmethod
    def streams():
        return [{'Index': 0, 'Type': 'Video'},
                {'Index': 1, 'Type': 'Audio', 'DisplayTitle': 'English', 'IsDefault': True},
                {'Index': 2, 'Type': 'Audio', 'DisplayTitle': 'German'},
                {'Index': 3, 'Type': 'Subtitle', 'DisplayTitle': 'English SRT', 'IsTextSubtitleStream': True,
                 'DeliveryMethod': 'External', 'DeliveryUrl': f'/Videos/{MOVIE}/{MOVIE}/Subtitles/3/0/Stream.vtt?ApiKey={TOKEN}'},
                {'Index': 4, 'Type': 'Subtitle', 'DisplayTitle': 'PGS', 'DeliveryMethod': 'Encode'}]

    @staticmethod
    def movie(position=0, detail=False):
        m = {'Id': MOVIE, 'Name': 'Film', 'Type': 'Movie', 'ProductionYear': 2020, 'RunTimeTicks': 7200 * jf.TICKS,
             'ImageTags': {'Primary': 'tag1'}, 'BackdropImageTags': ['bd1'],
             'UserData': {'PlaybackPositionTicks': position * jf.TICKS, 'Played': False}}
        if detail:
            m.update(OfficialRating='PG-13', CriticRating=82, Studios=[{'Name': 'Studio One'}],
                     ProductionLocations=['Denmark'], OriginalTitle='Filmen', Taglines=['A quiet mystery'],
                     People=[{'Id': PERSON, 'Name': 'A Performer', 'Role': 'Lead', 'Type': 'Actor', 'PrimaryImageTag': 'headshot'},
                             {'Name': 'A Director', 'Type': 'Director'}],
                     RemoteTrailers=[{'Url': 'https://youtube.com.evil.test/watch?v=abcdefghijk'},
                                     {'Url': 'https://youtu.be/abcdefghijk?si=tracking'}])
            m['MediaStreams'] = FakeJellyfin.streams()
            m['MediaSources'] = [{'Id': MOVIE, 'DefaultAudioStreamIndex': 1}]
        return m


class _Body(httpx.AsyncByteStream):
    def __init__(self, data: bytes):
        self.data = data

    async def __aiter__(self):
        yield self.data


class StreamingMock(httpx.AsyncBaseTransport):
    """Like a real server: bodies arrive as an unread stream (MockTransport pre-reads them)."""
    def __init__(self, handler):
        self.handler = handler

    async def handle_async_request(self, request):
        await request.aread()
        r = self.handler(request)
        return httpx.Response(r.status_code, headers=r.headers, stream=_Body(r.content), request=request)


@pytest.fixture
def fake(monkeypatch):
    f = FakeJellyfin()
    monkeypatch.setattr(jf, 'transport', httpx.MockTransport(f))
    monkeypatch.setattr(jf, 'async_transport', StreamingMock(f))
    jf._aclients.clear()
    init_db()
    drop_kv(jf.CONFIG_KEY)
    with session() as s:
        for row in s.exec(select(JellyfinLink)).all():
            s.delete(row)
        s.commit()
    yield f
    jf._aclients.clear()
    drop_kv(jf.CONFIG_KEY)


def _setup(c):
    assert c.put('/api/jellyfin/config', json={'url': 'jf.lan:8096/'}).json()['server_name'] == 'Den'
    assert c.post('/api/jellyfin/link', json={'username': 'me', 'password': 'nope'}).status_code == 401
    st = c.post('/api/jellyfin/link', json={'username': 'me', 'password': 'pw'}).json()
    assert st['linked'] and st['user_name'] == 'me' and TOKEN not in json.dumps(st)


def test_setup_link_browse_never_leaks_token(fake):
    with TestClient(app) as c:
        st = c.get('/api/jellyfin/status').json()
        assert not st['configured'] and not st['linked']
        assert c.get('/api/jellyfin/home').status_code == 409
        _setup(c)
        assert jf.config()['url'] == BASE
        home = c.get('/api/jellyfin/home')
        assert home.status_code == 200 and TOKEN not in home.text
        h = home.json()
        assert h['resume'][0]['progress'] == round(600 / 7200, 3)
        assert h['latest'][0]['view']['name'] == 'Movies'
        img = h['resume'][0]['image']
        assert img.startswith('/api/jellyfin/img/')
        r = c.get(img + '?w=300')
        assert r.content == b'jpg' and 'immutable' in r.headers['cache-control']
        assert c.get('/api/jellyfin/img/forged').status_code == 403
        assert c.get('/api/jellyfin/items', params={'types': 'Movie,Evil'}).json()['total'] == 1
        sent = up.parse_qs(fake.calls[-1].url.query.decode())
        assert sent['includeItemTypes'] == ['Movie']
        d = c.get(f'/api/jellyfin/items/{MOVIE}').json()
        assert [a['label'] for a in d['audio']] == ['English', 'German'] and len(d['subtitles']) == 2
        assert c.get('/api/jellyfin/items/../../System').status_code in (404, 422)
        assert c.post(f'/api/jellyfin/items/{MOVIE}/played', json={'on': True}).json()['ok']
        assert c.get('/api/jellyfin/encoding').json()['hardware'] == 'nvenc'


def test_direct_play_proxies_ranges(fake):
    with TestClient(app) as c:
        _setup(c)
        p = c.post(f'/api/jellyfin/items/{MOVIE}/play', json={}).json()
        assert p['method'] == 'DirectPlay' and p['kind'] == 'file' and p['start_s'] == 0
        assert TOKEN not in json.dumps(p)
        r = c.get(p['url'], headers={'Range': 'bytes=0-9'})
        assert r.status_code == 206 and r.content == b'0123456789'
        assert r.headers['content-range'] == 'bytes 0-9/100'
        started = next(x for x in fake.calls if x.url.path == '/Sessions/Playing')
        assert json.loads(started.content)['PlayMethod'] == 'DirectPlay'


def test_transcode_hls_rewrites_playlists_and_reports_hardware(fake):
    fake.direct = False
    with TestClient(app) as c:
        _setup(c)
        p = c.post(f'/api/jellyfin/items/{MOVIE}/play', json={'start_s': 60, 'force_transcode': True}).json()
        assert p['method'] == 'Transcode' and p['kind'] == 'hls' and p['start_s'] == 60
        assert p['transcode_reasons'] == ['VideoCodecNotSupported']
        assert TOKEN not in p['url'] and 'starttimeticks' not in p['url'].lower()
        info = next(x for x in fake.calls if x.url.path.endswith('/PlaybackInfo'))
        body = json.loads(info.content)
        assert body['EnableDirectPlay'] is False and body['AllowVideoStreamCopy'] is False
        assert body['StartTimeTicks'] == 60 * jf.TICKS
        subs = {s['index']: s for s in p['subtitles']}
        assert subs[3]['url'].startswith('/api/jellyfin/media/') and subs[3]['url'].endswith('/Stream.vtt')
        assert subs[4]['burned']
        m3u8 = c.get(p['url'])
        assert m3u8.status_code == 200 and TOKEN not in m3u8.text
        assert 'main.m3u8?DeviceId=x\n' in m3u8.text
        assert f'URI="/api/jellyfin/media/{p["session"]}/videos/{MOVIE}/subs.m3u8"' in m3u8.text
        # A session token only opens its own item's media.
        assert c.get(f'/api/jellyfin/media/{p["session"]}/Items/{MOVIE}/Images/Primary').status_code == 403
        assert c.get(f'/api/jellyfin/media/{p["session"]}/videos/{"f" * 32}/master.m3u8').status_code == 403
        dashed = f'{MOVIE[:8]}-{MOVIE[8:12]}-{MOVIE[12:16]}-{MOVIE[16:20]}-{MOVIE[20:]}'
        assert c.get(f'/api/jellyfin/media/{p["session"]}/videos/{dashed}/master.m3u8').status_code != 403
        info = c.get('/api/jellyfin/playing', params={'session': p['session']}).json()
        assert info['method'] == 'Transcode' and info['transcode']['hardware'] == 'vaapi'
        assert c.post('/api/jellyfin/progress', json={'session': p['session'], 'position_s': 75}).json()['ok']
        assert c.post('/api/jellyfin/progress', json={'session': p['session'], 'position_s': 80,
                                                      'event': 'stopped'}).json()['ok']
        paths = [x.url.path for x in fake.calls]
        assert '/Sessions/Playing/Progress' in paths and '/Sessions/Playing/Stopped' in paths
        assert '/Videos/ActiveEncodings' in paths  # frees the hardware encoder


def test_non_default_audio_disables_direct_play_and_next_episode(fake):
    with TestClient(app) as c:
        _setup(c)
        p = c.post(f'/api/jellyfin/items/{MOVIE}/play', json={'audio_index': 2}).json()
        assert p['kind'] == 'hls' and p['audio_index'] == 2
        ep = c.post(f'/api/jellyfin/items/{EP1}/play', json={}).json()
        assert ep['next']['id'] == EP2


def test_config_requires_admin_and_rejects_bad_urls(fake):
    with TestClient(app) as c:
        assert c.put('/api/jellyfin/config', json={'url': 'ftp://x'}).status_code == 422
        assert c.put('/api/jellyfin/config', json={'url': 'http://u:p@x'}).status_code == 422
        assert c.post('/api/jellyfin/progress', json={'session': 'x' * 20}).status_code == 403


def test_device_profile_keeps_hdr_off_sdr_screens(fake):
    """HDR sources must transcode (and tonemap on the server's GPU) unless the screen shows HDR."""
    def ranges(profile, codec):
        cp = next(x for x in profile['CodecProfiles'] if x['Codec'] == codec)
        return next(c['Value'] for c in cp['Conditions'] if c['Property'] == 'VideoRangeType').split('|')
    sdr = jf.device_profile({'max_bitrate': 8_000_000, 'hevc': True})
    hdr = jf.device_profile({'max_bitrate': 8_000_000, 'hevc': True, 'hdr': True})
    assert 'HDR10' not in ranges(sdr, 'hevc') and 'SDR' in ranges(sdr, 'hevc')
    assert 'HDR10' in ranges(hdr, 'hevc') and 'DOVI' not in ranges(hdr, 'hevc')
    assert ranges(hdr, 'h264') == ['SDR', 'DOVIWithSDR']  # 8-bit H.264 output is always SDR
    assert sdr['TranscodingProfiles'][0]['VideoCodec'] == 'hevc,h264'
    with TestClient(app) as c:
        _setup(c)
        c.post(f'/api/jellyfin/items/{MOVIE}/play', json={'caps': {'hevc': True, 'hdr': True}})
        info = next(x for x in fake.calls if x.url.path.endswith('/PlaybackInfo'))
        assert 'HDR10' in ranges(json.loads(info.content)['DeviceProfile'], 'hevc')


def test_search_feeds_find(fake):
    with TestClient(app) as c:
        assert c.get('/api/jellyfin/search', params={'q': 'film'}).status_code == 409  # unset: Find skips it
        _setup(c)
        assert c.get('/api/jellyfin/search', params={'q': ' '}).json() == {'items': []}
        r = c.get('/api/jellyfin/search', params={'q': 'film'})
        assert r.status_code == 200 and TOKEN not in r.text
        hit = r.json()['items'][0]
        assert hit['id'] == MOVIE and hit['playable'] and hit['image'].startswith('/api/jellyfin/img/')
        sent = up.parse_qs(fake.calls[-1].url.query.decode())
        assert sent['searchTerm'] == ['film'] and sent['recursive'] == ['true']


def test_play_on_sends_marquee_titles_to_linked_profiles_only(fake):
    with TestClient(app) as c:
        r = c.post('/api/playback/targets', json={'name': 'Den TV'}).json()
        path = f'/api/playback/targets/{r["id"]}'
        rx = {'X-Receiver-Token': r['token']}
        try:
            assert c.post(path + '/pair', json={'code': r['pair_code']}).status_code == 200
            send = lambda body: c.post(path + '/commands', json={'action': 'play', **body})  # noqa: E731
            assert send({'jellyfin_id': '../../x'}).status_code == 422
            assert send({'jellyfin_id': MOVIE}).status_code == 409  # not linked to Jellyfin yet
            _setup(c)
            assert send({'jellyfin_id': MOVIE, 'video_id': 'abcdefghijk'}).status_code == 422
            sent = send({'jellyfin_id': MOVIE, 'position_s': 42})
            assert sent.status_code == 200
            cmd = c.post(path + '/heartbeat', json={}, headers=rx).json()['command']
            assert cmd['jellyfin_id'] == MOVIE and cmd['video_id'] is None and cmd['position_s'] == 42
            from bt.jellyfin import cast
            assert cast.grant_pid(cmd['grant']) == cmd['profile_id']  # a screen on another account plays as the sender
            assert 'grant' not in json.dumps(c.get('/api/playback/targets').json())
            beat = {'ack': cmd['id'], 'jellyfin_id': MOVIE, 'paused': False, 'position_s': 50, 'duration_s': 7200}
            assert c.post(path + '/heartbeat', json=beat, headers=rx).json()['command'] is None
            state = next(t for t in c.get('/api/playback/targets').json() if t['id'] == r['id'])['state']
            assert state['jellyfin_id'] == MOVIE and state['position_s'] == 50
            c.delete('/api/jellyfin/link')
            assert send({'jellyfin_id': MOVIE}).status_code == 409
        finally:
            c.delete(path, headers=rx)


def test_rating_age():
    cases = {'G': 0, 'TV-Y7': 7, 'PG': 8, 'US-PG-13': 13, 'FSK-6': 6, 'DE-12': 12, '12A': 12, 'PEGI 7': 7,
             'GB-U': 0, 'AU-M': 15, 'R': 17, 'NR': None, 'Unrated': None, '': None, None: None, 'XYZ': None}
    for r, age in cases.items():
        assert jf.rating_age(r) == age, r


def test_age_and_genre_filters(fake):
    with TestClient(app) as c:
        _setup(c)
        f = c.get('/api/jellyfin/filters', params={'parent_id': 'e' * 32, 'types': 'Movie'}).json()
        assert f['genres'] == ['Action', 'Comedy']
        assert f['ages'] == [3, 7, 10, 13]  # 16 adds nothing over 13
        assert c.get('/api/jellyfin/filters', params={'parent_id': '../x'}).status_code == 422
        r = c.get('/api/jellyfin/items', params={'parent_id': 'e' * 32, 'types': 'Movie', 'max_age': 7,
                                                 'genre': 'Action'})
        assert r.json()['total'] == 1
        q = dict(fake.calls[-1].url.params)
        assert q['officialRatings'] == 'G|FSK-6' and q['genres'] == 'Action'
        c.get('/api/jellyfin/items', params={'parent_id': 'e' * 32})
        assert 'officialRatings' not in dict(fake.calls[-1].url.params)


def test_cast_grant_plays_as_sender(fake):
    from bt.jellyfin import cast
    assert cast.grant_pid(cast.grant(7, 't')) == 7
    assert cast.grant_pid('forged') is None and cast.grant_pid(None) is None
    with TestClient(app) as c:
        _setup(c)
        sender = c.get('/api/jellyfin/status').json()
        assert sender['linked']
        pids = [p['id'] for p in c.get('/api/profiles').json()]
        other = c.post('/api/profiles', json={'name': 'Shared TV'}).json()['id']
        h = {'X-Profile-Id': str(other)}
        assert c.get('/api/jellyfin/home', headers=h).status_code == 409  # the screen's own profile is unlinked
        h['X-Cast-Grant'] = cast.grant(pids[0], 'rx')
        assert c.get('/api/jellyfin/home', headers=h).status_code == 200
        h['X-Cast-Grant'] = 'forged'
        assert c.get('/api/jellyfin/home', headers=h).status_code == 409



def test_marquee_detail_trailers_credits_and_series_rating(fake):
    with TestClient(app) as c:
        _setup(c)
        movie_path = f'/api/jellyfin/items/{MOVIE}'
        movie = c.get(movie_path).json()
        assert movie['trailer'] == {'item_id': TRAILER}
        assert movie['official_rating'] == 'PG-13'
        assert movie['critic_rating'] == 82 and movie['studios'] == ['Studio One']
        assert movie['countries'] == ['Denmark'] and movie['original_title'] == 'Filmen'
        actor, director = movie['people']
        assert actor['role'] == 'Lead' and actor['type'] == 'Actor' and actor['image'].startswith('/api/jellyfin/img/')
        assert director['type'] == 'Director' and not director['image']
        played = c.post(f'/api/jellyfin/items/{TRAILER}/play', json={'start_s': 0})
        assert played.status_code == 200 and played.json()['item']['type'] == 'Trailer'
        assert played.json()['url'].startswith('/api/jellyfin/media/') and TOKEN not in played.text

        fake.local_trailer = False
        assert c.get(movie_path).json()['trailer'] == {'url': 'https://www.youtube.com/watch?v=abcdefghijk'}
        episode = c.get(f'/api/jellyfin/items/{EP1}').json()
        assert episode['official_rating'] == 'TV-14' and episode['rating_from_series']
        assert episode['genres'] == ['Drama'] and 'trailer' not in episode
