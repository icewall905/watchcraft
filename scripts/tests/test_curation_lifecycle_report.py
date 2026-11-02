import json
from pathlib import Path
import sqlite3
import subprocess
import sys


def test_report_distinguishes_judgment_and_local_file_states(tmp_path):
    dbfile = tmp_path / 'test.sqlite3'
    media = tmp_path / 'media'
    media.mkdir()
    (media / 'ready.mp4').write_bytes(b'fixture')
    with sqlite3.connect(dbfile) as db:
        db.executescript('''CREATE TABLE category(id INTEGER, kind TEXT);
            CREATE TABLE candidate(category_id INTEGER, video_id TEXT, status TEXT, score INTEGER, red_flags TEXT, created_at TEXT, judged_at TEXT);
            CREATE TABLE download(video_id TEXT, status TEXT, path TEXT, finished_at TEXT);
            INSERT INTO category VALUES(1,'shorts');
            INSERT INTO candidate VALUES(1,'one','accepted',80,'[]','2026-11-02 10:00:00','2026-11-02 10:01:00');
            INSERT INTO candidate VALUES(1,'two','rejected',NULL,'["off-topic: test"]','2026-11-02 10:00:00','2026-11-02 10:02:00');
            INSERT INTO candidate VALUES(1,'three','accepted',80,'[]','2026-11-02 10:00:00','2026-11-02 10:03:00');
            INSERT INTO candidate VALUES(1,'four','rejected',12,'["off-topic: model"]','2026-11-02 10:00:00','2026-11-02 10:04:00');
            INSERT INTO download VALUES('one','done','ready.mp4','2026-11-02 10:02:00');
            INSERT INTO download VALUES('three','done','../escape.mp4','2026-11-02 10:04:00');''')
    script = Path(__file__).resolve().parents[1] / 'curation_lifecycle_report.py'
    out = subprocess.run([sys.executable, str(script), '--database', str(dbfile), '--since', '2026-11-02T10:00:00Z', '--media', str(media)],
                         capture_output=True, text=True, check=True)
    result = json.loads(out.stdout)
    shorts = result['playlist_kinds']['shorts']
    assert shorts['counts'] == {'full_judgment_accepted': 2, 'title_screen_rejected': 1,
                                'accepted_recorded_local': 2, 'accepted_existing_local_file': 1,
                                'accepted_missing_local_file': 1, 'full_judgment_rejected': 1}
    assert shorts['accepted_to_recorded_local']['n'] == 1
    assert shorts['found_to_decision_by_stage']['full_judgment']['n'] == 3
    assert shorts['found_to_decision_by_stage']['title_screen']['n'] == 1
    assert 'one' not in out.stdout and 'ready.mp4' not in out.stdout
