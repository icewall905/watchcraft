"""Local meme clips: bounded capture, real media validation and authorized byte ranges."""
import os
import subprocess
import tempfile
os.environ.setdefault('BT_DATA_DIR', tempfile.mkdtemp(prefix='wc-meme-video-'))
import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel
from bt import db
from bt.db import init_db, session
from bt.hub import capture, memes
from bt.hub.models import CaptureJob, CapturedAsset, ContentItem, ItemState
from bt.models import Profile
from bt.main import app
H = {'X-Profile-Id': '1'}

@pytest.fixture
def client(monkeypatch):
    with db.engine.begin() as c: c.exec_driver_sql('DROP TABLE IF EXISTS content_fts')
    SQLModel.metadata.drop_all(db.engine); init_db()
    monkeypatch.setattr(capture.ai, 'enabled', lambda: False)
    yield TestClient(app)
    SQLModel.metadata.drop_all(db.engine); init_db()

@pytest.fixture(scope='module')
def clip():
    return subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=size=64x64:rate=10',
                           '-t', '1', '-pix_fmt', 'yuv420p', '-c:v', 'libx264', '-movflags', 'frag_keyframe+empty_moov',
                           '-f', 'mp4', 'pipe:1'], capture_output=True, check=True).stdout

def seed():
    with session() as s:
        it = ContentItem(type='meme', provider='mastodon', url='https://m.test/@a/clip', title='Video meme', body='Caption',
                         meta={'media': {'type': 'video', 'url': 'https://cdn.test/clip.mp4'}})
        s.add(it); s.flush(); iid = it.id
        s.add(ItemState(profile_id=1, item_id=iid))
        s.add(CaptureJob(profile_id=1, item_id=iid, status='running', attempts=1, lease=123))
        s.commit()
    return iid

def test_video_capture_and_scoped_range_playback(client, monkeypatch, clip):
    iid = seed(); calls = []
    def fetch(url, **kwargs):
        calls.append((url, kwargs))
        assert url == 'https://cdn.test/clip.mp4'
        return clip, url, {'content_type': 'video/mp4'}
    monkeypatch.setattr(capture.curation, 'fetch', fetch)
    capture.run(1, iid, 123)
    with session() as s:
        assert s.get(CaptureJob, (1, iid)).status == 'complete'
        assert s.get(CapturedAsset, (iid, 'video')).data == clip
        other = Profile(name='Other'); s.add(other); s.commit(); pid = other.id
    assert calls[0][1]['limit'] == capture.VIDEO_MAX_BYTES
    detail = client.get(f'/api/hub/items/{iid}', headers=H).json()
    assert detail['local_video'] and 'video' in detail['local_assets']
    url = f'/api/hub/items/{iid}/assets/video'
    r = client.get(url, headers={**H, 'Range': 'bytes=2-19'})
    assert r.status_code == 206 and r.content == clip[2:20]
    assert r.headers['content-range'] == f'bytes 2-19/{len(clip)}'
    assert client.get(url, headers={**H, 'Range': 'bytes=-10'}).content == clip[-10:]
    assert client.get(url, headers={**H, 'Range': 'bytes=999999-'}).status_code == 416
    assert client.get(url, headers={**H, 'Range': 'bytes=0-1,3-4'}).status_code == 416
    head = client.head(url, headers=H)
    assert head.status_code == 200 and not head.content and int(head.headers['content-length']) == len(clip)
    assert client.get(url, headers={'X-Profile-Id': str(pid)}).status_code == 404
    assert client.get(url, headers=H).content == clip

def test_invalid_and_over_limit_media_are_not_saved(client, monkeypatch, clip):
    iid = seed()
    with pytest.raises(ValueError): capture.save_asset(1, iid, 'video', 'https://cdn.test/bad.mp4', b'not a video', 'video/mp4')
    monkeypatch.setattr(capture, 'VIDEO_MAX_SECONDS', .5)
    with pytest.raises(ValueError): capture.save_asset(1, iid, 'video', 'https://cdn.test/long.mp4', clip, 'video/mp4')
    with session() as s: assert s.get(CapturedAsset, (iid, 'video')) is None
    monkeypatch.setattr(capture, 'VIDEO_MAX_BYTES', 10)
    with pytest.raises(ValueError): capture.save_asset(1, iid, 'video', 'https://cdn.test/big.mp4', clip, 'video/mp4')


def test_video_identity_is_rebuilt_when_clip_arrives_after_poster(client, clip):
    from bt.hub.models import MemeExample
    iid = seed()
    with session() as s:
        s.add(MemeExample(item_id=iid, media_sha='old-poster-hash', dup_of=999))
        s.commit()
    capture.save_asset(1, iid, 'video', 'https://cdn.test/clip.mp4', clip, 'video/mp4')
    with session() as s:
        ex = s.get(MemeExample, iid)
        assert ex.media_sha == memes.media_sha(clip) and ex.dup_of is None


def test_clip_without_playable_frames_is_rejected(client, clip):
    iid = seed()
    broken = clip[:clip.index(b'mdat') + 4]
    with pytest.raises(ValueError):
        capture.save_asset(1, iid, 'video', 'https://cdn.test/broken.mp4', broken, 'video/mp4')


def test_identical_video_bytes_deduplicate_across_posts(client, clip):
    from bt.hub.models import MemeExample
    first = seed()
    with session() as s:
        it = ContentItem(type='meme', provider='mastodon', url='https://m.test/@b/clip', title='Reposted clip',
                         meta={'media': {'type': 'video', 'url': 'https://cdn.test/repost.mp4'}})
        s.add(it); s.flush(); second = it.id
        s.add(ItemState(profile_id=1, item_id=second)); s.commit()
    capture.save_asset(1, first, 'video', 'https://cdn.test/clip.mp4', clip, 'video/mp4')
    capture.save_asset(1, second, 'video', 'https://cdn.test/repost.mp4', clip, 'video/mp4')
    with session() as s:
        assert s.get(MemeExample, second).dup_of == first
    assert client.get('/api/hub/memes/items', headers=H).json()['total'] == 1
