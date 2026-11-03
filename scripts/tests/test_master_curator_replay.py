import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def _fixture_database(tmp_path, hub_ai=False):
    data = tmp_path / 'source-data'
    data.mkdir(mode=0o700)
    env = {**os.environ, 'BT_DATA_DIR': str(data),
           'PYTHONPATH': str(ROOT / 'backend')}
    setup = f'''
from bt.db import init_db, session
from bt.models import Profile, Setting
from bt.hub.models import ContentItem, Curator, CuratorDiscovery
from sqlmodel import select
init_db()
with session() as s:
    p = s.exec(select(Profile).order_by(Profile.id)).first()
    p.name = 'PRIVATE PROFILE NAME'; p.role = 'admin'; s.add(p); s.commit(); s.refresh(p)
    s.add(Setting(key='private_fixture', value={{'secret': 'DO NOT REPORT'}}))
    s.add(Setting(key='search', value={{'hub_ai': {hub_ai!r}}}))
    c = Curator(profile_id=p.id, name='private curator')
    s.add(c); s.commit(); s.refresh(c)
    item = ContentItem(url='https://example.test/story', type='news', title='A useful current story',
                       summary='A sufficiently detailed summary for a useful report.', first_seen=1793707200)
    s.add(item); s.commit(); s.refresh(item)
    s.add(CuratorDiscovery(curator_id=c.id, item_id=item.id, profile_id=p.id, found_at=1793707200))
    s.commit()
'''
    subprocess.run([sys.executable, '-c', setup], cwd=ROOT, env=env, check=True,
                   capture_output=True, text=True)
    return data / 'bt.sqlite3'


def test_replay_reads_source_and_emits_private_sanitized_reports(tmp_path):
    source = _fixture_database(tmp_path)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    output = tmp_path / 'reports'
    env = {**os.environ, 'BT_DATA_DIR': str(tmp_path / 'must-not-be-used')}
    result = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'master_curator_replay.py'),
                             '--database', str(source), '--output', str(output),
                             '--now', '2026-11-03T12:00:00Z', '--judge'], cwd=ROOT, env=env,
                            check=True, capture_output=True, text=True)

    after = hashlib.sha256(source.read_bytes()).hexdigest()
    assert before == after
    report = json.loads((output / 'report.json').read_text())
    assert report['now_utc'] == '2026-11-03T12:00:00+00:00'
    assert len(report['profiles']) == 1
    profile = report['profiles'][0]
    assert profile['profile_id'] == 1 and profile['role'] == 'admin' and profile['hub_allowed']
    assert profile['baseline']['counts']['feature'] + profile['baseline']['counts']['keep'] >= 1
    assert profile['model']['judge']['stopped'] == 'no model'
    assert profile['model']['judge']['judged'] == 0
    all_output = result.stdout + (output / 'report.json').read_text() + (output / 'report.md').read_text()
    assert 'PRIVATE PROFILE NAME' not in all_output
    assert 'DO NOT REPORT' not in all_output
    assert 'private curator' not in all_output
    assert 'A useful current story' in all_output
    assert (output.stat().st_mode & 0o777) == 0o700
    assert (output / 'report.json').stat().st_mode & 0o777 == 0o600


def test_replay_rejects_nonempty_output_without_overwriting(tmp_path):
    source = _fixture_database(tmp_path)
    output = tmp_path / 'reports'
    output.mkdir()
    sentinel = output / 'sentinel'
    sentinel.write_text('preserve')
    result = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'master_curator_replay.py'),
                             '--database', str(source), '--output', str(output)], cwd=ROOT,
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert sentinel.read_text() == 'preserve'
    assert 'output directory must be empty' in result.stderr


def test_model_replay_changes_only_model_copy_and_reports_type_results(tmp_path):
    source = _fixture_database(tmp_path, hub_ai=True)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    output = tmp_path / 'model-reports'
    spec = importlib.util.spec_from_file_location('master_curator_replay', ROOT / 'scripts' / 'master_curator_replay.py')
    replay_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(replay_module)
    calls = []

    def fake_judge(_system, _user, _schema, **_kwargs):
        calls.append(True)
        return {'verdicts': [{'n': 1, 'quality': 100, 'relevance': 100, 'reason': 'fixture judge reason'}]}

    report = replay_module.replay(source, output, '2026-11-03T12:00:00Z', True, judge_call=fake_judge)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert calls
    profile = report['profiles'][0]
    baseline_news = next(row for tier in ('feature', 'keep', 'set_aside', 'reject')
                         for row in profile['baseline']['by_type']['news']['samples'][tier])
    model_news = next(row for tier in ('feature', 'keep', 'set_aside', 'reject')
                      for row in profile['model']['by_type']['news']['samples'][tier])
    assert baseline_news and model_news
    assert baseline_news['final'] != model_news['final']
    assert any('fixture judge reason' in reason for reason in model_news['reasons'])
    assert report['profiles'][0]['model']['judge']['judged'] == 1


def test_queue_busy_waits_for_queue_and_fails_closed(tmp_path):
    spec = importlib.util.spec_from_file_location('master_curator_replay', ROOT / 'scripts' / 'master_curator_replay.py')
    replay_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(replay_module)
    metrics = tmp_path / 'metrics'
    metrics.write_text('vllm:num_requests_running{engine="0"} 1.0\nvllm:num_requests_waiting{engine="0"} 0.0\n')
    assert replay_module.queue_busy(metrics.as_uri())() is False
    metrics.write_text('vllm:num_requests_waiting{engine="0"} 2.0\n')
    assert replay_module.queue_busy(metrics.as_uri(), wait=0, poll=0)() is True
    try:
        replay_module.queue_busy((tmp_path / 'missing').as_uri(), wait=0, poll=0)()
    except OSError:
        pass
    else:
        raise AssertionError('unreadable metrics must not report idle')
