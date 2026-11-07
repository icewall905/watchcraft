"""Music videos are stored offline: queued by the downloader, given their own lane, kept from eviction."""
import time

import pytest
from sqlmodel import delete, select

from bt.config import MEDIA_DIR
from bt.db import init_db, save_setting, session
from bt.downloader import service as svc
from bt.models import Candidate, Category, Download, Profile, Setting, Video, WatchHistory
from bt.music import offline
from bt.music.models import MusicFavorite, MusicMix, MusicMixTrack, MusicPlay, MusicTrack

V = [f'mus{i:08d}' for i in range(8)]


def wipe():
    with session() as s:
        for m in (Download, Candidate, WatchHistory, Category, MusicFavorite, MusicMix, MusicMixTrack, MusicPlay,
                  MusicTrack):
            s.exec(delete(m))
        s.exec(delete(Setting).where(Setting.key.startswith(offline.FOR_YOU_KEY)))
        s.commit()


@pytest.fixture(autouse=True)
def clean():
    init_db()
    wipe()
    save_setting('cache', {'enabled': True, 'max_gb': 1, 'max_height': 720, 'rate_limit': 0, 'min_gap_s': 0,
                           'auto': 'manual'})
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    yield
    wipe()  # music left behind would leak into other modules' download queues
    save_setting('cache', {'enabled': True, 'max_gb': 500, 'max_height': 1080, 'rate_limit': 0, 'min_gap_s': 0,
                           'auto': 'accepted'})


def _pid(s):
    return s.exec(select(Profile)).first().id


def _library(s):
    pid = _pid(s)
    s.add(MusicTrack(video_id=V[0], title='Dancing Queen', artist='ABBA', duration_s=233))
    s.add(MusicFavorite(profile_id=pid, kind='track', ref=V[0]))
    s.add(MusicFavorite(profile_id=pid, kind='artist', ref='abba', name='ABBA'))  # artists are not files
    m = MusicMix(profile_id=pid, name='Disco', kind='curator')
    s.add(m)
    s.commit()
    s.refresh(m)
    for i, v in enumerate([V[2], V[0], V[3]]):
        s.add(MusicMixTrack(mix_id=m.id, video_id=v, position=i))
    s.add(MusicPlay(profile_id=pid, video_id=V[4]))
    s.add(MusicPlay(profile_id=pid, video_id=V[5], skipped=True))
    s.add(MusicPlay(profile_id=pid, video_id=V[6], played_at=time.time() - 90 * 86400))
    s.commit()
    offline.save_for_you(s, pid, [V[1]])
    return pid


def test_wanted_music_in_priority_order():
    with session() as s:
        _library(s)
        assert offline.wanted_ids(s) == [V[0], V[1], V[2], V[3], V[4]]  # no skips, no old plays
        assert offline.wanted_ids(s, limit=2) == [V[0], V[1]]
        assert all(offline.is_wanted(s, v) for v in V[:5]) and not any(offline.is_wanted(s, v) for v in V[5:])


def test_only_the_newest_mixes_of_each_profile_are_kept():
    with session() as s:
        pid = _pid(s)
        for i in range(offline.MIXES + 2):
            m = MusicMix(profile_id=pid, name=f'm{i}', created_at=1000 + i)
            s.add(m)
            s.commit()
            s.refresh(m)
            s.add(MusicMixTrack(mix_id=m.id, video_id=f'mix{i:08d}'))
        s.commit()
        ids = offline.wanted_ids(s)
        assert len(ids) == offline.MIXES and ids[0] == f'mix{offline.MIXES + 1:08d}' and 'mix00000000' not in ids


def test_sync_queue_stores_music_even_in_manual_mode_and_names_it():
    with session() as s:
        _library(s)
        assert svc.sync_queue(s, {'auto': 'manual'}) == 5
        assert {d.video_id for d in s.exec(select(Download)).all()} == set(V[:5])
        assert s.get(Video, V[0]).title == 'ABBA – Dancing Queen' and s.get(Video, V[0]).duration_s == 233
        for v, err in ((V[2], ''), (V[3], offline.HAND_DELETED)):
            d = s.get(Download, v)
            d.status, d.error = 'evicted', err
            s.add(d)
        s.commit()
        assert svc.sync_queue(s, {'auto': 'manual'}) == 1  # evicted under pressure: back in the queue
        assert s.get(Download, V[2]).status == 'queued' and s.get(Download, V[3]).status == 'evicted'


def test_next_download_takes_music_in_order_and_alternates_with_playlists():
    with session() as s:
        _library(s)
        svc.sync_queue(s, {'auto': 'manual'})
        c = Category(profile_id=_pid(s), name='c')
        s.add(c)
        s.commit()
        s.refresh(c)
        s.add(Video(id='musplay0001', duration_s=10))
        s.add(Candidate(category_id=c.id, video_id='musplay0001', status='accepted', score=90))
        s.add(Download(video_id='musplay0001', status='queued'))
        s.commit()
        d, lane = svc.next_download(s, None)
        assert (d.video_id, lane) == (V[0], svc.MUSIC_LANE)  # favourites first
        d, lane = svc.next_download(s, lane)
        assert (d.video_id, lane) == ('musplay0001', c.id)  # then the playlist lane gets its turn
        assert svc.drop_dead_queue(s) == 0  # music rows have no candidate but are not dead


def test_wanted_music_is_protected_like_accepted_videos():
    with session() as s:
        pid = _library(s)
        for v in (V[0], V[7]):  # V[7]: no longer wanted (unfavourited long ago)
            p = MEDIA_DIR / svc.rel_path(v)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b'x')
            s.add(Download(video_id=v, status='done', path=svc.rel_path(v), size_bytes=10))
        s.add(WatchHistory(profile_id=pid, video_id=V[0], finished=True))  # watched to the end once
        s.commit()
        assert [d.video_id for d in svc.eviction_order(s, allow_wanted=False)] == [V[7]]
        assert svc._unwanted(s, V[0]) is None
        assert V[0] not in svc.unwanted_reasons(s)


def test_music_tracks_report_offline_state(monkeypatch):
    from fastapi.testclient import TestClient
    from bt.hub import ai
    from bt.main import app
    from bt.music import yt
    monkeypatch.setattr(ai, 'enabled', lambda: False)
    tr = {'video_id': V[0], 'title': 'Dancing Queen', 'artist': 'ABBA', 'artist_id': '', 'views': 1,
          'duration_s': 233, 'thumbnail': ''}
    monkeypatch.setattr(yt, 'search', lambda q, origin='user': [dict(tr)])
    with session() as s:
        s.add(Download(video_id=V[0], status='done', path=svc.rel_path(V[0])))
        s.commit()
    c = TestClient(app)
    assert c.get('/api/music/search', params={'q': 'abba'}).json()['tracks'][0]['offline'] is True
    c.post('/api/music/favorites', json={'kind': 'track', 'track': tr})
    assert c.get('/api/music/home').json()['offline'] == {'wanted': 1, 'saved': 1, 'pending': 0}
