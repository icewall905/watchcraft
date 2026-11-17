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
SERIES = 'b' * 32
EP1, EP2 = 'c' * 32, 'd' * 32
TOKEN = 'secret-jellyfin-token'


class FakeJellyfin:
    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.direct = True

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
        if p == f'/Items/{EP1}':
            return httpx.Response(200, json={'Id': EP1, 'Name': 'Pilot', 'Type': 'Episode', 'SeriesId': SERIES,
                                             'RunTimeTicks': 1800 * jf.TICKS, 'MediaSources': [{'Id': EP1}]})
        if p == f'/Shows/{SERIES}/Episodes':
            assert q['startItemId'] == EP1
            return httpx.Response(200, json={'Items': [{'Id': EP1, 'Type': 'Episode'},
                                                       {'Id': EP2, 'Name': 'Second', 'Type': 'Episode'}]})
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
