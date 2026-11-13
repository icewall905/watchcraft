"""Shared test guards."""
import os
import tempfile
import asyncio

# Before anything imports bt.config: DATA_DIR is resolved at import time, and a conftest that
# imported it first would point the whole suite at the real ./data directory.
os.environ.setdefault('BT_DATA_DIR', tempfile.mkdtemp(prefix='bt-test-'))

import pytest  # noqa: E402

from bt import subtitle_jobs
from bt.hub import rescue  # noqa: E402
from bt.hub import engine, slots  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_background_work(monkeypatch):
    """API lifespans must not dispatch real work against a test-owned database.

    Tests reset the shared schema, so cancelling the async scheduler is too late:
    its asyncio.to_thread tick and any capture jobs can still be running. Scheduler
    tests invoke dispatch_due explicitly; slot tests exercise spawn explicitly.
    Disable the actual launch entry point and the post-job scheduler callback.
    """
    async def idle_scheduler():
        await asyncio.Event().wait()

    monkeypatch.setattr(engine, 'scheduler', idle_scheduler)
    monkeypatch.setattr(subtitle_jobs, 'worker', idle_scheduler)
    monkeypatch.setattr(slots, 'after_release', None)


@pytest.fixture(autouse=True)
def _no_browser(monkeypatch):
    """Rendering launches a real browser against a real URL, which no unit test should do.

    The tool is disabled through the registry rather than the module attribute, because the
    registry holds the function object. Tests that exercise the command line call
    `rescue.tool_render` directly and are unaffected.
    """
    monkeypatch.setitem(rescue.TOOLS, 'render', ('disabled in tests', lambda ctx: ''))


@pytest.fixture(autouse=True)
def _no_article_age_guard(monkeypatch):
    """Feed fixtures carry fixed publication dates; the age guard would retire them as the calendar
    moves. Tests of the guard switch it back on."""
    from bt.hub import engine
    monkeypatch.setattr(engine, 'NEWS_MAX_AGE', None)


@pytest.fixture(autouse=True)
def _no_reddit_pace(monkeypatch):
    """Reddit feed reads are paced 60 s apart in production; tests read fixtures back to back."""
    from bt.hub import providers
    monkeypatch.setattr(providers, 'REDDIT_GAP_S', 0.0)
    monkeypatch.setattr(providers, '_reddit_blocked_until', 0.0)
