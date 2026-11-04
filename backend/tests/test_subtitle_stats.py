import os
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from bt.main import app
from bt.models import Profile, Category, Candidate, Video, Download, GeneratedSubtitles
from bt.subtitle_stats import snapshot


def test_coverage_distinct_valid_tracks_scope_and_heartbeat(tmp_path, monkeypatch):
    engine = create_engine('sqlite://')
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr('bt.subtitle_stats.abs_path', lambda value: tmp_path / value)
    monkeypatch.setattr('bt.subtitle_stats.subtitle_jobs.enabled', lambda: True)
    beat = tmp_path / 'heartbeat'; beat.write_text('999999')
    os.utime(beat, (100, 100))
    with Session(engine) as s:
        admin = Profile(name='Admin', role='admin'); adult = Profile(name='Adult', role='adult')
        s.add(admin); s.add(adult); s.commit()
        cats = [Category(name=str(i), kind='shorts', profile_id=adult.id if i < 2 else admin.id) for i in range(3)]
        s.add_all(cats); s.commit()
        for i in range(7):
            vid = f'statsvid{i:03}'
            (tmp_path / vid).write_bytes(b'media')
            s.add(Video(id=vid, title=vid, is_short=True, duration_s=60))
            s.add(Download(video_id=vid, status='done', path=vid))
            s.add(Candidate(category_id=cats[0 if i < 6 else 2].id, video_id=vid, status='accepted'))
            if i == 0: s.add(Candidate(category_id=cats[1].id, video_id=vid, status='accepted'))
            if i < 5:
                states = ['ready', 'ready', 'queued', 'queued', 'running']
                s.add(GeneratedSubtitles(video_id=vid, status=states[i], retry_at=1000 if i == 3 else 0,
                    vtt='WEBVTT\n\n00:00.000 --> 00:01.000\nHello' if i == 0 else '', progress=25))
        s.commit()
        result = snapshot(s, adult.id, beat, 200)
        assert result['eligible'] == 6
        assert result['coverage'] == dict(ready=1, failed=1, pending=1, retry_waiting=1, running=1, missing=1)
        assert not result['service']['heartbeat_fresh']
        assert result['service']['heartbeat_age_s'] == 100
        assert result['active'][0]['progress'] == 25
        assert len(result['recent_failures']) == 1
        assert snapshot(s, admin.id, beat, 150)['eligible'] == 7
        assert snapshot(s, admin.id, beat, 150)['service']['heartbeat_fresh']


def test_unknown_profile_cannot_read_subtitle_stats():
    with TestClient(app) as client:
        result = client.get('/api/stats/subtitles', headers={'X-Profile-Id': '999999'})
        assert result.status_code == 404
