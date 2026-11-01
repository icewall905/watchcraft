"""Capture-level regression coverage for PDF OCR and stored feed excerpts."""
import subprocess
from types import SimpleNamespace

import pytest
from sqlmodel import SQLModel

from bt import db
from bt.db import init_db, session
from bt.hub import capture
from bt.hub.models import CaptureJob, CapturedAsset, ContentItem, ItemState

PDF = b'%PDF-1.4\nscanned document'
OCR_TEXT = 'Recovered evidence from the scanned document.'


@pytest.fixture(autouse=True)
def database(monkeypatch):
    with db.engine.begin() as connection:
        connection.exec_driver_sql('DROP TABLE IF EXISTS content_fts')
    SQLModel.metadata.drop_all(db.engine)
    init_db()
    monkeypatch.setattr(capture.ai, 'enabled', lambda: False)
    yield
    SQLModel.metadata.drop_all(db.engine)
    init_db()


def seed(image=''):
    with session() as s:
        item = ContentItem(url='https://example.org/paper.pdf', title='Paper', type='document',
                           image=image, body='Existing feed excerpt')
        s.add(item)
        s.flush()
        iid = item.id
        s.add(ItemState(profile_id=1, item_id=iid))
        s.add(CaptureJob(profile_id=1, item_id=iid, attempts=3, lease=123))
        s.commit()
    return iid


@pytest.mark.parametrize('text_failure', [False, True])
@pytest.mark.parametrize('asset_failure', [False, True])
def test_pdf_ocr_recovers_empty_or_broken_text_layer(monkeypatch, text_failure, asset_failure):
    iid = seed(image='https://example.org/poster.png')
    calls = []

    def fetch(url, **kwargs):
        calls.append(url)
        if url.endswith('poster.png'):
            raise ValueError('Poster unavailable')
        return PDF, url, {'content_type': 'application/pdf'}

    def pdftotext(*args, **kwargs):
        if text_failure:
            raise subprocess.CalledProcessError(1, args[0])
        return SimpleNamespace(stdout=b'\f\n')

    def ocr(ctx):
        assert ctx['data'] == PDF
        return OCR_TEXT

    monkeypatch.setattr(capture.curation, 'fetch', fetch)
    monkeypatch.setattr(capture.subprocess, 'run', pdftotext)
    monkeypatch.setattr(capture.rescue, 'tool_ocr', ocr)
    monkeypatch.setattr(capture.rescue, 'rescue', lambda *a: pytest.fail('PDF must not use network rescue'))
    if asset_failure:
        def fail_asset(*args):
            raise ValueError('Storage full')
        monkeypatch.setattr(capture, 'save_asset', fail_asset)
    capture.run(1, iid, 123)
    with session() as s:
        assert s.get(ContentItem, iid).body == OCR_TEXT
        job = s.get(CaptureJob, (1, iid))
        assert job.status == 'partial'
        assert job.error == ('Document: Storage full; ' if asset_failure else '') + 'Image: Poster unavailable'
        asset = s.get(CapturedAsset, (iid, 'document'))
        assert asset is None if asset_failure else asset.data == PDF
    assert calls == ['https://example.org/paper.pdf', 'https://example.org/poster.png']


@pytest.mark.parametrize('ocr_text', [OCR_TEXT, ''])
def test_pdf_without_images_finishes_or_reports_unreadable_scan(monkeypatch, ocr_text):
    iid = seed()
    monkeypatch.setattr(capture.curation, 'fetch', lambda url, **kw: (PDF, url, {'content_type': 'application/pdf'}))
    monkeypatch.setattr(capture.subprocess, 'run', lambda *a, **kw: SimpleNamespace(stdout=b''))
    monkeypatch.setattr(capture.rescue, 'tool_ocr', lambda ctx: ocr_text)
    monkeypatch.setattr(capture.rescue, 'rescue', lambda *a: pytest.fail('PDF must not use network rescue'))
    capture.run(1, iid, 123)
    with session() as s:
        job = s.get(CaptureJob, (1, iid))
        assert job.status == ('complete' if ocr_text else 'partial')
        assert job.error == ('' if ocr_text else 'Document has no extractable text.')
        assert s.get(ContentItem, iid).body == (ocr_text or 'Existing feed excerpt')


def test_stored_feed_excerpt_does_not_suppress_rescue_or_get_erased(monkeypatch):
    iid = seed()
    def unavailable(*args, **kwargs):
        raise ValueError('Offline')
    monkeypatch.setattr(capture.curation, 'fetch', unavailable)
    contexts = []
    def rescue(ctx, reason):
        contexts.append((ctx, reason))
        return {'body': '', 'tool': ''}
    monkeypatch.setattr(capture.rescue, 'rescue', rescue)
    capture.run(1, iid, 123)
    assert len(contexts) == 1 and contexts[0][1] == 'Offline'
    with session() as s:
        assert s.get(ContentItem, iid).body == 'Existing feed excerpt'
        assert s.get(CaptureJob, (1, iid)).error == 'Offline'
