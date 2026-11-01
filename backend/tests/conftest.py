"""Shared test guards."""
import os
import tempfile

# Before anything imports bt.config: DATA_DIR is resolved at import time, and a conftest that
# imported it first would point the whole suite at the real ./data directory.
os.environ.setdefault('BT_DATA_DIR', tempfile.mkdtemp(prefix='bt-test-'))

import pytest  # noqa: E402

from bt.hub import rescue  # noqa: E402


@pytest.fixture(autouse=True)
def _no_browser(monkeypatch):
    """Rendering launches a real browser against a real URL, which no unit test should do.

    The tool is disabled through the registry rather than the module attribute, because the
    registry holds the function object. Tests that exercise the command line call
    `rescue.tool_render` directly and are unaffected.
    """
    monkeypatch.setitem(rescue.TOOLS, 'render', ('disabled in tests', lambda ctx: ''))
