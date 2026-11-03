"""Local-only independent Shorts feeds, without network providers."""
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

from bt import db
from bt.agent import data
from bt.downloader import service
from bt.main import app
from bt.models import Candidate, Category, Download, Video


def test_category_kind_migration_preserves_existing_playlists(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(Category(id=1, profile_id=1, name='Existing'))
        s.commit()
    with engine.begin() as conn:
        conn.exec_driver_sql('ALTER TABLE category DROP COLUMN kind')
        conn.exec_driver_sql("INSERT INTO profile (id,name,emoji,color,role,prefs,created_at) VALUES (1,'Old','x','#fff','admin','{}',CURRENT_TIMESTAMP)")
    monkeypatch.setattr(db, 'engine', engine)
    db.init_db()
    db.init_db()
    with engine.connect() as conn:
        assert conn.exec_driver_sql('SELECT name,kind,min_duration_s,max_duration_s FROM category').all() == [('Existing', 'playlist', 60, 3600)]


def test_short_feed_configuration_local_files_counts_and_home(tmp_path, monkeypatch):
    monkeypatch.setattr(service, 'MEDIA_DIR', tmp_path)
    with TestClient(app) as c:
        p = c.post('/api/profiles', json={'name': 'Local feed'}).json()
        h = {'X-Profile-Id': str(p['id'])}
        feed = c.post('/api/categories', json={'name': 'Cooking clips', 'kind': 'shorts'}, headers=h).json()
        assert (feed['min_duration_s'], feed['max_duration_s']) == (1, 180)
        regular = c.post('/api/categories', json={'name': 'Regular'}, headers=h).json()
        assert regular['kind'] == 'playlist'
        assert c.patch(f"/api/categories/{regular['id']}", json={'kind': 'shorts'}, headers=h).status_code == 400
        assert c.post('/api/categories', json={'name': 'Bad', 'kind': 'typo'}, headers=h).status_code == 422
        ids = [f'local{i:06d}' for i in range(6)]
        with db.session() as s:
            for i, vid in enumerate(ids):
                s.add(Video(id=vid, duration_s=30, is_short=True))
                s.add(Candidate(category_id=feed['id'], video_id=vid, status='accepted', score=90))
                if i < 4:
                    s.add(Download(video_id=vid, status='done', path=f'{vid}.mp4'))
                elif i == 4:
                    s.add(Download(video_id=vid, status='error', error='failed'))
            # Only first exists and is nonempty. Second zero, third missing, fourth escapes root.
            (tmp_path / f'{ids[0]}.mp4').write_bytes(b'local video')
            (tmp_path / f'{ids[1]}.mp4').touch()
            s.commit()
            d = s.get(Download, ids[3]); d.path = '../escape.mp4'; s.add(d); s.commit()
        rows = c.get('/api/shorts/feeds', headers=h).json()
        assert len(rows) == 1
        assert (rows[0]['ready_count'], rows[0]['pending_count'], rows[0]['error_count']) == (1, 4, 1)
        assert c.get('/api/shorts', headers=h).status_code == 400
        assert c.get('/api/shorts', params={'category_id': regular['id']}, headers=h).status_code == 404
        got = c.get('/api/shorts', params={'category_id': feed['id']}, headers=h).json()
        assert [x['video']['id'] for x in got['items']] == ids[:1]
        assert got['items'][0]['video']['file_url'] == f'/api/video/{ids[0]}/file'
        assert got['items'][0]['video']['offline']
        home = c.get('/api/feed/home', headers=h).json()
        assert feed['id'] not in [r.get('category_id') for r in home]
        assert regular['id'] in [r.get('category_id') for r in home]
        (tmp_path / f'{ids[0]}.mp4').unlink()
        assert c.get('/api/shorts', params={'category_id': feed['id']}, headers=h).json()['items'] == []


def test_manual_download_mode_still_queues_feed_not_playlist():
    with TestClient(app) as c:
        p = c.post('/api/profiles', json={'name': 'Feed downloads'}).json()
        with db.session() as s:
            feed = Category(profile_id=p['id'], name='Clips', kind='shorts')
            regular = Category(profile_id=p['id'], name='Longs')
            s.add(feed); s.add(regular); s.commit()
            for cat, vid in [(feed, 'queuefeed01'), (regular, 'queuelong01')]:
                s.add(Video(id=vid, is_short=cat.kind == 'shorts'))
                s.add(Candidate(category_id=cat.id, video_id=vid, status='accepted'))
            s.commit()
            service.sync_queue(s, {'auto': 'manual'})
            assert s.get(Download, 'queuefeed01').status == 'queued'
            assert s.get(Download, 'queuelong01') is None


def test_feed_prefilter_ignores_hide_shorts_but_preserves_constraints():
    ctx = data.ProfileCtx(watched=set(), disliked=set(), blocked=set())
    cat = Category(profile_id=1, name='Clips', kind='shorts', min_duration_s=1, max_duration_s=180)
    item = {'id': 'clips000001', 'title': 'Good clip', 'is_short': True, 'duration_s': 30}
    assert data.prefilter(item, cat, ctx, True) is None
    assert data.prefilter({**item, 'is_short': False}, cat, ctx, False) == 'not a short'
    assert 'too long' in data.prefilter({**item, 'duration_s': 181}, cat, ctx, True)
    cat.exclude_keywords = ['bad']
    assert 'excluded keyword' in data.prefilter({**item, 'title': 'Bad clip'}, cat, ctx, True)
    ctx.blocked.add('UCblocked')
    assert data.prefilter({**item, 'channel_id': 'UCblocked'}, cat, ctx, True) == 'blocked/distrusted channel'


def test_short_feed_ownership_and_locked_profile_cannot_be_bypassed():
    with TestClient(app) as c:
        owner = c.post('/api/profiles', json={'name': 'Private clips', 'pin': '9321'}).json()
        other = c.post('/api/profiles', json={'name': 'Other clips'}).json()
        h = {'X-Profile-Id': str(owner['id'])}
        other_h = {'X-Profile-Id': str(other['id'])}
        assert c.post(f"/api/profiles/{owner['id']}/unlock", json={'pin': '9321'}).status_code == 200
        feed = c.post('/api/categories', json={'name': 'Private feed', 'kind': 'shorts'}, headers=h).json()
        assert feed['id'] in [f['id'] for f in c.get('/api/shorts/feeds', headers=h).json()]
        assert feed['id'] not in [f['id'] for f in c.get('/api/shorts/feeds', headers=other_h).json()]
        assert c.get('/api/shorts', params={'category_id': feed['id']}, headers=other_h).status_code == 404
        assert c.patch(f"/api/categories/{feed['id']}", json={'name': 'Hijacked'}, headers=other_h).status_code == 404
        assert c.delete(f"/api/categories/{feed['id']}", headers=other_h).status_code == 404
        assert c.post(f"/api/profiles/{owner['id']}/lock").status_code == 200
        for path in ['/api/shorts/feeds', f"/api/shorts?category_id={feed['id']}", f"/api/categories/{feed['id']}"]:
            assert c.get(path, headers=h).status_code == 403
        assert c.post('/api/categories', json={'name': 'Bypass', 'kind': 'shorts'}, headers=h).status_code == 403


def test_short_feed_counts_batch_queries_without_loading_video_payloads(tmp_path, monkeypatch):
    from sqlalchemy import event
    from bt.api import core
    from bt.models import Profile, WatchHistory

    monkeypatch.setattr(service, 'MEDIA_DIR', tmp_path)
    (tmp_path / 'shared.mp4').write_bytes(b'local clip')
    with db.session() as s:
        p = Profile(name='Batch Shorts', role='child')
        s.add(p); s.flush()
        cats = [Category(profile_id=p.id, name=f'Clips {n}', kind='shorts') for n in range(4)]
        s.add_all(cats); s.flush()
        for vid in ['batchclip01', 'batchseen01', 'batchwait01', 'batchfail01']:
            s.add(Video(id=vid, is_short=True, transcript='Large transcript ' * 1000))
        s.add(Download(video_id='batchclip01', status='done', path='shared.mp4'))
        s.add(Download(video_id='batchseen01', status='done', path='shared.mp4'))
        s.add(Download(video_id='batchfail01', status='error'))
        s.add(WatchHistory(profile_id=p.id, video_id='batchseen01', finished=True))
        for cat in cats:
            for vid in ['batchclip01', 'batchseen01', 'batchwait01', 'batchfail01']:
                s.add(Candidate(category_id=cat.id, video_id=vid, status='accepted'))
        s.commit()
        pid = p.id
    statements, paths = [], []
    original = core._readable_short_file
    def readable(path):
        paths.append(path)
        return original(path)
    monkeypatch.setattr(core, '_readable_short_file', readable)
    def record(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith('SELECT'):
            statements.append(statement)
    event.listen(db.engine, 'before_cursor_execute', record)
    try:
        with db.session() as s:
            result = core.shorts_feeds(pid, s)
    finally:
        event.remove(db.engine, 'before_cursor_execute', record)
    assert len(result) == 4
    assert all((r['ready_count'], r['pending_count'], r['error_count']) == (1, 1, 1) for r in result)
    assert paths == ['shared.mp4']
    assert len(statements) == 4  # profile feed list, watched IDs, stats and availability
    assert not any('video.transcript' in sql or 'video.comments' in sql for sql in statements)
