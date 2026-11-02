"""Fair scheduling of research and AI without bypassing source blockers."""
import threading

from sqlmodel import select
from test_agent import fresh_db, make_category, make_worker  # noqa: F401
from bt.agent import data, judge, learn, memory
from bt.db import session
from bt.models import Candidate, Video, now


def test_cached_video_advances_during_block_without_call_or_sleep(monkeypatch):
    cat = make_category()
    worker = make_worker()
    with session() as s:
        s.add(Video(id='cache-ready', title='Cached', transcript='kept', enriched_at=now()))
        s.add(Candidate(category_id=cat.id, video_id='cache-ready'))
        s.commit()
    monkeypatch.setattr(worker.rl, 'blocker', lambda: (600, 'backoff', 'blocked'))
    monkeypatch.setattr(worker.rl, 'wait_blocker', lambda **kwargs: (_ for _ in ()).throw(AssertionError('slept before processing cached work')))
    monkeypatch.setattr(learn, 'process', lambda rt: False)
    monkeypatch.setattr(learn, 'pending_count', lambda: 0)
    monkeypatch.setattr(memory, 'process', lambda rt: False)
    monkeypatch.setattr(judge, 'pending', lambda: [])
    assert worker.iterate()
    with session() as s:
        candidate = s.exec(select(Candidate).where(Candidate.video_id == 'cache-ready')).one()
        assert candidate.status == 'enriched'
        assert s.get(Video, 'cache-ready').transcript == 'kept'
    assert worker.rt.yt.calls == []


def test_blocked_selection_skips_cold_work_in_earlier_playlist(monkeypatch):
    first = make_category(name='First')
    second = make_category(name='Second')
    worker = make_worker()
    with session() as s:
        for category, vid, enriched in ((first, 'cold', None), (second, 'warm', now())):
            s.add(Video(id=vid, enriched_at=enriched))
            s.add(Candidate(category_id=category.id, video_id=vid))
        s.commit()
        needs = data.category_needs(s)
    monkeypatch.setattr(worker.rl, 'blocker', lambda: (600, 'backoff', 'blocked'))
    assert worker._next_found(needs, cached_only=True).video_id == 'warm'
    assert worker._research_one()
    assert worker.rt.yt.calls == []
    with session() as s:
        assert s.exec(select(Candidate).where(Candidate.video_id == 'cold')).one().status == 'found'
        assert s.exec(select(Candidate).where(Candidate.video_id == 'warm')).one().status == 'enriched'


def test_ai_yields_after_research_finishes_but_keeps_busy_before(monkeypatch):
    worker = make_worker()
    monkeypatch.setattr(learn, 'process', lambda rt: False)
    monkeypatch.setattr(learn, 'pending_count', lambda: 0)
    monkeypatch.setattr(memory, 'process', lambda rt: False)
    candidates = [Candidate(id=n, category_id=1, video_id=str(n), status='enriched') for n in range(1, 5)]
    monkeypatch.setattr(judge, 'pending', lambda: candidates)
    done, seen = threading.Event(), []
    def verdict(rt, cand):
        seen.append(cand.id)
        if cand.id == 2:
            done.set()
        return 'accepted'
    monkeypatch.setattr(judge, 'judge_one', verdict)
    assert worker._ai_work(done)
    assert seen == [1, 2]
    seen.clear()
    assert worker._ai_work(done)
    assert seen == [1]  # A completed research lane still permits one verdict.


def test_handled_failures_are_not_counted_as_success(monkeypatch, caplog):
    import json
    import logging
    from bt.agent import enrich, timing
    monkeypatch.setattr(timing.log, 'propagate', True)
    monkeypatch.setattr(timing.log, 'disabled', False)
    cat = make_category()
    worker = make_worker()
    with session() as s:
        s.add(Video(id='failure', title='failure'))
        s.add(Candidate(category_id=cat.id, video_id='failure'))
        s.commit()
    monkeypatch.setattr(worker.rl, 'blocker', lambda: None)
    monkeypatch.setattr(enrich, 'enrich_one', lambda *args: 'error')
    monkeypatch.setattr(learn, 'process', lambda rt: False)
    monkeypatch.setattr(learn, 'pending_count', lambda: 0)
    monkeypatch.setattr(memory, 'process', lambda rt: False)
    monkeypatch.setattr(judge, 'pending', lambda: [Candidate(id=1, category_id=cat.id, video_id='failure')])
    monkeypatch.setattr(judge, 'judge_one', lambda *args: None)
    with caplog.at_level(logging.INFO, logger='bt.agent.timing'):
        assert worker._research_one()
        assert not worker._ai_work()
    events = [json.loads(r.getMessage().removeprefix(timing.PREFIX)) for r in caplog.records]
    assert {(e['stage'], e['outcome']) for e in events} == {('research', 'error'), ('judgment', 'error')}
