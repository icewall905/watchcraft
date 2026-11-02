import asyncio
import json
import logging

import pytest

from bt.agent.timing import PREFIX, new_trace_id, span


@pytest.fixture(autouse=True)
def enable_test_capture(monkeypatch):
    from bt.agent import timing
    monkeypatch.setattr(timing.log, "propagate", True)
    monkeypatch.setattr(timing.log, "disabled", False)


def test_span_emits_bounded_event_and_monotonic_duration(caplog):
    ticks = iter([10.0, 10.125])
    with caplog.at_level(logging.INFO, logger="bt.agent.timing"):
        with span("research", trace_id=new_trace_id(), candidate_id=12, clock=lambda: next(ticks)):
            pass
    event = json.loads(caplog.records[0].getMessage().removeprefix(PREFIX))
    assert event["duration_ms"] == 125
    assert event["outcome"] == "ok"
    assert event["candidate_id"] == 12
    assert set(event) == {"v", "timestamp", "stage", "trace_id", "candidate_id", "category_id", "profile_id", "outcome", "duration_ms"}


@pytest.mark.parametrize("exc,outcome", [(RuntimeError("secret url"), "error"), (asyncio.CancelledError(), "cancelled")])
def test_failure_recorded_without_exception_details(caplog, exc, outcome):
    with caplog.at_level(logging.INFO, logger="bt.agent.timing"):
        with pytest.raises(type(exc)):
            with span("judgment"):
                raise exc
    assert json.loads(caplog.records[0].getMessage().removeprefix(PREFIX))["outcome"] == outcome
    assert "secret" not in caplog.text


@pytest.mark.parametrize("kwargs", [{"stage": "secret"}, {"stage": "pass", "trace_id": "a url"}, {"stage": "pass", "candidate_id": True}])
def test_rejects_unbounded_fields(kwargs):
    with pytest.raises(ValueError):
        with span(**kwargs):
            pass


def test_nested_spans_inherit_trace_and_restore_context(caplog):
    trace = new_trace_id()
    with caplog.at_level(logging.INFO, logger='bt.agent.timing'):
        with span('research', trace_id=trace):
            with span('metadata'):
                pass
        with span('metadata'):
            pass
    events = [json.loads(record.getMessage().removeprefix(PREFIX)) for record in caplog.records]
    assert [event['trace_id'] for event in events] == [trace, trace, None]


def test_worker_pause_is_recorded_as_cancellation(caplog):
    from bt.agent.runtime import Paused
    with caplog.at_level(logging.INFO, logger='bt.agent.timing'), pytest.raises(Paused):
        with span('research'):
            raise Paused()
    assert json.loads(caplog.records[0].getMessage().removeprefix(PREFIX))['outcome'] == 'cancelled'


def test_worker_log_is_private_and_rotates(tmp_path):
    import stat
    from bt.agent import timing
    logger = timing.log
    old_handlers, old_propagate, old_level, old_disabled = list(logger.handlers), logger.propagate, logger.level, logger.disabled
    try:
        logger.handlers = []
        logger.disabled = False
        assert timing.configure(tmp_path)
        handler = logger.handlers[0]
        handler.maxBytes, handler.backupCount = 256, 2
        for _ in range(12):
            with timing.span('pass'):
                pass
        handler.flush()
        files = list(tmp_path.glob('curation-timing.log*'))
        assert len(files) == 3
        assert all(stat.S_IMODE(f.stat().st_mode) == 0o600 for f in files)
        assert all(f.stat().st_size <= 256 for f in files)
    finally:
        for handler in logger.handlers:
            handler.close()
        logger.handlers, logger.propagate, logger.level, logger.disabled = old_handlers, old_propagate, old_level, old_disabled


def test_report_groups_cache_hits_and_rejects_invalid_input():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / 'scripts/curation_timing_report.py'
    spec = importlib.util.spec_from_file_location('curation_timing_report', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    def event(result, duration):
        return PREFIX + json.dumps({'v': 1, 'stage': 'metadata_cache', 'outcome': 'ok', 'result': result, 'duration_ms': duration})
    samples = module.summarize([event('hit', 5), event('miss', 10), event(['unsafe'], 3), 'invalid'])
    assert samples[('metadata_cache', 'ok', 'hit')] == [5]
    assert samples[('metadata_cache', 'ok', 'miss')] == [10]
    assert sum(map(len, samples.values())) == 2


def test_report_filters_by_utc_window_and_discards_bad_times():
    import importlib.util
    from datetime import datetime, timezone
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / 'scripts/curation_timing_report.py'
    spec = importlib.util.spec_from_file_location('timing_report_window', path)
    report = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(report)
    def event(timestamp):
        return PREFIX + json.dumps({'v': 1, 'stage': 'metadata', 'outcome': 'ok', 'duration_ms': 2, 'timestamp': timestamp})
    since = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
    samples = report.summarize([event('2026-11-02T11:59:59Z'), event('2026-11-02T13:00:00+01:00'), event(12)], since)
    assert samples[('metadata', 'ok', '')] == [2]
