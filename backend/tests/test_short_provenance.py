"""Positive Shorts evidence survives richer watch-page metadata and additive migration."""
from types import SimpleNamespace

from sqlmodel import SQLModel, Session, create_engine
from bt import db, ytdl
from bt.agent import data
from bt.models import Category, Video


def test_verified_short_survives_metadata_refresh(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'shorts.db'}")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(ytdl, '_extract', lambda *a, **k: {
        'entries': [{'id': '26P1KyvrmjI', 'title': 'A real Short', 'duration': 150}]})
    _, entries = ytdl.channel_videos('@Tasty', tab='shorts')
    assert entries[0]['shorts_verified']
    with Session(engine) as s:
        ytdl.upsert_video(s, entries[0])
        s.commit()
    with Session(engine) as s:
        video = ytdl.upsert_video(s, {'id': '26P1KyvrmjI', 'formats': [], 'duration': 150,
                                       'webpage_url': 'https://www.youtube.com/watch?v=26P1KyvrmjI',
                                       'is_short': False})
        assert video.is_short and video.shorts_verified
        assert data.prefilter(video.model_dump(), Category(profile_id=1, name='Clips', kind='shorts',
                                                           min_duration_s=1, max_duration_s=180), SimpleNamespace(blocked=set(), disliked=set(), watched=set()), True) is None


def test_short_url_proof_uses_real_youtube_host():
    assert ytdl._shorts_url('https://www.youtube.com/shorts/26P1KyvrmjI', '26P1KyvrmjI')
    assert not ytdl._shorts_url('https://notyoutube.com/shorts/26P1KyvrmjI', '26P1KyvrmjI')
    assert not ytdl._shorts_url('https://www.youtube.com/watch?v=x/shorts/26P1KyvrmjI', '26P1KyvrmjI')


def test_short_provenance_additive_migration_preserves_old_rows(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    SQLModel.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql('ALTER TABLE video DROP COLUMN shorts_verified')
        conn.exec_driver_sql("INSERT INTO video (id, title, channel_id, channel_name, duration_s, thumbnail, view_count, like_count, is_short, is_live, description, tags, chapters, transcript, comments) VALUES ('26P1KyvrmjI', 'Old short', '', '', 45, '', 0, 0, 1, 0, '', '[]', '[]', '', '[]')")
    monkeypatch.setattr(db, 'engine', engine)
    db.init_db()
    db.init_db()
    with Session(engine) as s:
        video = s.get(Video, '26P1KyvrmjI')
        assert video.title == 'Old short' and video.is_short
        assert video.shorts_verified is False
