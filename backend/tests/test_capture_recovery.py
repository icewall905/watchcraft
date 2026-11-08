"""Capture-level regression coverage for PDF OCR and stored feed excerpts."""
import subprocess
from types import SimpleNamespace

import pytest
from sqlmodel import SQLModel

from bt import db
from bt.db import init_db, session
from bt.hub import capture
from bt.hub.models import (CaptureJob, CapturedAsset, CapturedPage, ContentItem, CuratorDiscovery,
                           ItemState, MasterQueue)
from bt.models import Profile

PDF = b'%PDF-1.4\nscanned document'
OCR_TEXT = 'Recovered evidence from the scanned document.'


@pytest.fixture(autouse=True)
def database(monkeypatch):
    with db.engine.begin() as connection:
        connection.exec_driver_sql('DROP TABLE IF EXISTS content_fts')
    SQLModel.metadata.drop_all(db.engine)
    init_db()
    monkeypatch.setattr(capture.ai, 'enabled', lambda: False)
    monkeypatch.setattr(capture.article_cleanup, 'review', lambda title, body: {'body': body, 'status': 'reviewed', 'removed': 0})
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


@pytest.mark.parametrize('unavailable', [False, True])
def test_lemmy_linked_article_preserves_message_and_summarizes_article(monkeypatch, unavailable):
    iid = seed()
    article_url = 'https://example.org/full-report'
    with session() as s:
        item = s.get(ContentItem, iid)
        item.type, item.provider, item.body = 'news', 'lemmy', 'User message context'
        item.author, item.publisher, item.published_at = 'Community author', 'Community', 77.0
        item.meta = {'social_post': True, 'article_url': article_url}
        s.add(item); s.commit()
    article = 'Full article evidence. ' * 600 + 'Essential article ending.'
    calls, summarized = [], []

    def fetch(url, **kwargs):
        calls.append(url)
        if unavailable: raise ValueError('Article unavailable')
        return ('<article><p>' + article + '</p></article>').encode(), url, {'content_type': 'text/html'}

    def summarize(title, body):
        summarized.append(body)
        return 'Article overview'

    monkeypatch.setattr(capture.curation, 'fetch', fetch)
    monkeypatch.setattr(capture.rescue, 'rescue', lambda *args: {'body': '', 'tool': ''})
    monkeypatch.setattr(capture.ai, 'enabled', lambda: True)
    monkeypatch.setattr(capture, 'full_summary', summarize)
    capture.run(1, iid, 123)
    with session() as s:
        item, job = s.get(ContentItem, iid), s.get(CaptureJob, (1, iid))
        assert 'User message context' in item.body
        assert item.meta['post_body'] == 'User message context'
        assert (item.author, item.publisher, item.published_at) == ('Community author', 'Community', 77.0)
        assert job.status == ('partial' if unavailable else 'complete')
        if unavailable:
            assert item.body == 'User message context'
            assert 'Article unavailable' in job.error
        else:
            assert 'Linked article:' in item.body
            assert item.body.endswith('Essential article ending.')
            assert summarized[0] == item.body
    assert calls == [article_url]
    if not unavailable:
        unavailable = True
        archived = summarized[0]
        capture.run(1, iid, 123)
        with session() as s:
            assert s.get(ContentItem, iid).body == archived
            assert s.get(CaptureJob, (1, iid)).status == 'partial'
            assert 'Article unavailable' in s.get(CaptureJob, (1, iid)).error
        assert summarized[-1] == archived


@pytest.mark.parametrize('existing_thumbnail', [False, True])
def test_social_news_archives_original_image_and_upgrades_thumbnail(monkeypatch, existing_thumbnail):
    preview, original = 'https://example.org/small.png', 'https://example.org/full.png'
    iid = seed(image=preview)
    image_data = b'\x89PNG\r\n\x1a\noriginal screenshot'
    with session() as s:
        item = s.get(ContentItem, iid)
        item.type, item.provider, item.body = 'news', 'mastodon', 'Complete message text'
        item.meta = {'social_post': True, 'media': {'type': 'image', 'url': original}}
        s.add(item)
        if existing_thumbnail:
            s.add(CapturedAsset(item_id=iid, kind='image', mime='image/png',
                                data=b'old thumbnail', size=13, source_url=preview))
        s.commit()
    calls = []

    def fetch(url, **kwargs):
        calls.append(url)
        return image_data, url, {'content_type': 'image/png'}

    monkeypatch.setattr(capture.curation, 'fetch', fetch)
    capture.run(1, iid, 123)
    with session() as s:
        asset = s.get(CapturedAsset, (iid, 'image'))
        assert asset.source_url == original
        assert asset.data == image_data
        assert s.get(ContentItem, iid).body == 'Complete message text'
        assert s.get(CaptureJob, (1, iid)).status == 'complete'
    assert calls == [original]
    capture.run(1, iid, 123)
    assert calls == [original]


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


def _html_capture(monkeypatch, page):
    monkeypatch.setattr(capture.curation, 'fetch',
                        lambda url, **kw: (b'<html>readable</html>', url,
                                           {'content_type': 'text/html'}))
    monkeypatch.setattr(capture, 'extract_page', lambda *args: page)


def test_readable_structured_article_promotes_web_and_queues_master(monkeypatch):
    iid = seed()
    with session() as s:
        item = s.get(ContentItem, iid)
        item.type = 'web'
        s.add(item)
        s.commit()
    _html_capture(monkeypatch, {'body': 'A long article body.', 'type': 'article',
                                'author': 'Ada', 'publisher': '', 'published_at': 1234.0})
    capture.run(1, iid, 123)
    with session() as s:
        item, queue = s.get(ContentItem, iid), s.get(MasterQueue, 1)
        assert item.type == 'article'
        assert item.author == 'Ada' and item.publisher == 'example.org'
        assert item.published_at == 1234.0
        assert queue is not None


def test_readable_generic_page_fills_metadata_without_promotion(monkeypatch):
    iid = seed()
    with session() as s:
        item = s.get(ContentItem, iid)
        item.type = 'web'
        s.add(item)
        s.commit()
    _html_capture(monkeypatch, {'body': 'A generic page.', 'type': 'web',
                                'author': 'Page owner', 'publisher': 'Example',
                                'published_at': 1234.0})
    capture.run(1, iid, 123)
    with session() as s:
        item = s.get(ContentItem, iid)
        assert item.type == 'web'
        assert item.author == 'Page owner' and item.publisher == 'Example'
        assert item.published_at == 1234.0
        assert s.get(MasterQueue, 1) is None


def test_existing_direct_identity_wins_over_captured_page(monkeypatch):
    iid = seed()
    with session() as s:
        item = s.get(ContentItem, iid)
        item.type, item.author, item.publisher, item.published_at = 'article', 'Known', 'Known Pub', 99.0
        s.add(item)
        s.commit()
    _html_capture(monkeypatch, {'body': 'A refreshed article.', 'type': 'article',
                                'author': 'Page Author', 'publisher': 'Page Pub',
                                'published_at': 1234.0})
    capture.run(1, iid, 123)
    with session() as s:
        item = s.get(ContentItem, iid)
        assert item.type == 'article'
        assert (item.author, item.publisher, item.published_at) == ('Known', 'Known Pub', 99.0)


def test_capture_identity_merge_keeps_concurrent_values(monkeypatch):
    iid = seed()
    with session() as s:
        item = s.get(ContentItem, iid)
        item.type = 'web'
        s.add(item)
        s.commit()

    def fetch(url, **kwargs):
        with session() as s:
            item = s.get(ContentItem, iid)
            item.author, item.publisher, item.published_at = 'Concurrent', 'Concurrent Pub', 88.0
            s.add(item)
            s.commit()
        return b'<html>readable</html>', url, {'content_type': 'text/html'}

    monkeypatch.setattr(capture.curation, 'fetch', fetch)
    monkeypatch.setattr(capture, 'extract_page', lambda *args: {
        'body': 'A structured article.', 'type': 'article', 'author': 'Fetched',
        'publisher': 'Fetched Pub', 'published_at': 1234.0})
    capture.run(1, iid, 123)
    with session() as s:
        item = s.get(ContentItem, iid)
        assert item.type == 'article'
        assert (item.author, item.publisher, item.published_at) == ('Concurrent', 'Concurrent Pub', 88.0)


def test_shared_promotion_queues_each_discovering_profile(monkeypatch):
    iid = seed()
    with session() as s:
        second = Profile(name='Second', role='adult')
        s.add(second)
        s.flush()
        s.add(CuratorDiscovery(curator_id=2, item_id=iid, profile_id=second.id, query='topic'))
        item = s.get(ContentItem, iid)
        item.type = 'web'
        s.add(item)
        s.commit()
        second_id = second.id
    _html_capture(monkeypatch, {'body': 'Shared article.', 'type': 'news',
                                'author': '', 'publisher': 'Publisher', 'published_at': 1234.0})
    capture.run(1, iid, 123)
    with session() as s:
        assert s.get(MasterQueue, 1) is not None
        assert s.get(MasterQueue, second_id) is not None


@pytest.mark.parametrize('page', [
    {'body': '', 'type': 'article', 'author': 'Author', 'publisher': 'Publisher', 'published_at': 1234.0},
    None,
])
def test_empty_or_failed_extraction_cannot_promote_or_fill_identity(monkeypatch, page):
    iid = seed()
    with session() as s:
        item = s.get(ContentItem, iid)
        item.type = 'web'
        s.add(item)
        s.commit()
    if page is None:
        monkeypatch.setattr(capture.curation, 'fetch', lambda *a, **kw: (_ for _ in ()).throw(ValueError('Offline')))
    else:
        _html_capture(monkeypatch, page)
    monkeypatch.setattr(capture.rescue, 'rescue', lambda *a, **kw: {'body': '', 'tool': ''})
    capture.run(1, iid, 123)
    with session() as s:
        item = s.get(ContentItem, iid)
        assert item.type == 'web'
        assert not item.author and not item.publisher and item.published_at is None
        assert s.get(MasterQueue, 1) is None


def test_capture_preserves_known_identity_and_social_link_identity(monkeypatch):
    iid = seed()
    with session() as s:
        item = s.get(ContentItem, iid)
        item.type, item.author, item.publisher, item.published_at = 'web', 'Known', 'Known Pub', 99.0
        item.provider, item.meta = 'lemmy', {'social_post': True, 'article_url': 'https://publisher.example/story'}
        s.add(item)
        s.commit()
    _html_capture(monkeypatch, {'body': 'Linked article body.', 'type': 'article',
                                'author': 'Publisher Author', 'publisher': 'Publisher',
                                'published_at': 1234.0})
    capture.run(1, iid, 123)
    with session() as s:
        item = s.get(ContentItem, iid)
        assert item.type == 'web'
        assert (item.author, item.publisher, item.published_at) == ('Known', 'Known Pub', 99.0)
        assert s.get(MasterQueue, 1) is None


@pytest.mark.parametrize('truncated', [True, False])
def test_ocr_snapshot_retains_truncation_metadata(monkeypatch, truncated):
    iid = seed()
    monkeypatch.setattr(capture.curation, 'fetch', lambda url, **kw: (PDF, url, {'content_type': 'application/pdf'}))
    monkeypatch.setattr(capture.subprocess, 'run', lambda *a, **kw: SimpleNamespace(stdout=b''))
    def ocr(context):
        context['_ocr_truncated'] = truncated
        return OCR_TEXT
    monkeypatch.setattr(capture.rescue, 'tool_ocr', ocr)
    capture.run(1, iid, 123)
    with session() as s:
        assert s.get(CapturedPage, iid).truncated is truncated
        assert s.get(CaptureJob, (1, iid)).status == 'complete'


def test_nvd_challenge_recovers_authoritative_description(monkeypatch):
    import json
    iid = seed()
    with session() as s:
        item = s.get(ContentItem, iid)
        item.url = 'https://nvd.nist.gov/vuln/detail/CVE-2024-21545'
        item.type, item.provider = 'advisory', 'nvd'
        s.add(item); s.commit()
    description = 'The official description of this vulnerability.'
    calls = []
    def fetch(url, **kw):
        calls.append(url)
        if 'services.nvd.nist.gov' in url:
            return json.dumps({'vulnerabilities': [{'cve': {'id': 'CVE-2024-21545',
                'descriptions': [{'lang': 'en', 'value': description}]}}]}).encode(), url, {'content_type': 'application/json'}
        return b'<html><script>challenge()</script></html>', url, {'content_type': 'text/html'}
    monkeypatch.setattr(capture.curation, 'fetch', fetch)
    capture.run(1, iid, 123)
    with session() as s:
        assert description in s.get(ContentItem, iid).body
        job = s.get(CaptureJob, (1, iid))
        assert job.status == 'complete' and not job.error
    assert len(calls) == 2


def test_article_review_precedes_summary_and_retains_uncleaned_source(monkeypatch):
    from bt.hub.models import ArticleText
    iid = seed()
    with session() as s:
        item = s.get(ContentItem, iid);item.type='article';item.url='https://example.org/article';s.add(item);s.commit()
    original = 'Full original article. ' * 30 + '\n\nSubscribe to the newsletter.'
    clean = 'Full original article. ' * 30
    monkeypatch.setattr(capture.curation, 'fetch', lambda url, **kw: (b'<html/>',url,{'content_type':'text/html'}))
    monkeypatch.setattr(capture, 'extract_page', lambda *a: {'body':original})
    calls=[]
    def review(title, body):
        calls.append(body)
        return {'body':clean,'status':'reviewed','removed':1}
    monkeypatch.setattr(capture.article_cleanup, 'review', review)
    monkeypatch.setattr(capture.ai,'enabled',lambda:True)
    monkeypatch.setattr(capture,'full_summary',lambda title,body: calls.append(body) or 'Clean summary')
    capture.run(1,iid,123)
    with session() as s:
        item=s.get(ContentItem,iid);raw=s.get(ArticleText,iid)
        assert item.body==clean and raw.raw_body==original and raw.status=='reviewed'
        assert item.enrichment['summary']=='Clean summary'
    assert calls==[original,clean]


def test_legacy_review_does_not_refetch_or_duplicate_post_context(monkeypatch):
    from bt.hub.models import ArticleText
    iid=seed()
    original='Post text:\nUser context\n\nLinked article:\nOriginal archived report.'
    with session() as s:
        item=s.get(ContentItem,iid);item.type='news';item.provider='lemmy';item.body=original;item.meta={'article_url':'https://example.org/news','post_body':'User context'}
        s.add(item);s.add(ArticleText(item_id=iid,raw_body=original,backfill=True));s.commit()
    def never(*a,**kw): raise AssertionError('Legacy text review must not refetch publishers')
    monkeypatch.setattr(capture.curation,'fetch',never)
    capture.run(1,iid,123)
    with session() as s:
        assert s.get(ContentItem,iid).body==original
        assert s.get(ArticleText,iid).raw_body==original
        assert s.get(CaptureJob,(1,iid)).status=='complete'


def test_successful_legacy_cleanup_is_not_repeated_on_summary_retry(monkeypatch):
    from bt.hub.models import ArticleText
    iid=seed()
    with session() as s:
        item=s.get(ContentItem,iid);item.type='article';item.body='Already cleaned source text.';s.add(item)
        s.add(ArticleText(item_id=iid,raw_body='Original with removed clutter.',backfill=True,status='reviewed',version=capture.article_cleanup.VERSION,removed=1));s.commit()
    def never(*a,**kw):raise AssertionError('Successful cleanup must not run again on summary retry')
    monkeypatch.setattr(capture.article_cleanup,'review',never)
    monkeypatch.setattr(capture.curation,'fetch',never)
    capture.run(1,iid,123)
    with session() as s:assert s.get(ContentItem,iid).body=='Already cleaned source text.'


def test_inaccessible_newer_archives_do_not_starve_backfill(monkeypatch):
    from bt.hub.models import ArticleText
    with session() as s:
        for n in range(12):
            item=ContentItem(url=f'https://example.org/{n}',title=str(n),type='article',body='Article source text.')
            s.add(item);s.flush();s.add(CaptureJob(profile_id=1,item_id=item.id,status='complete',summary_status='ready'))
            if n==0:
                valid=item.id;s.add(ItemState(profile_id=1,item_id=item.id))
        s.commit()
    monkeypatch.setattr(capture.ai,'enabled',lambda:True)
    monkeypatch.setattr(capture.slots,'spawn_lane',lambda *a,**kw:False)
    capture.dispatch()
    with session() as s:
        assert s.get(ArticleText,valid).backfill
        assert s.get(CaptureJob,(1,valid)).status=='queued'


def test_linked_article_fetch_failure_reuses_prior_successful_review(monkeypatch):
    from bt.hub.models import ArticleText
    iid=seed()
    original='Post text:\nContext\n\nLinked article:\nPreviously cleaned reporting.'
    with session() as s:
        item=s.get(ContentItem,iid);item.type='news';item.provider='lemmy';item.body=original
        item.meta={'article_url':'https://example.org/news','post_body':'Context'};s.add(item)
        s.add(ArticleText(item_id=iid,raw_body='Raw original article',status='reviewed',version=capture.article_cleanup.VERSION,backfill=False));s.commit()
    def fail(*a,**kw):raise ValueError('Publisher unavailable')
    def never(*a,**kw):raise AssertionError('Do not delete further source on retry')
    monkeypatch.setattr(capture.curation,'fetch',fail)
    monkeypatch.setattr(capture.rescue,'rescue',lambda *a,**kw:{'body':'','tool':''})
    monkeypatch.setattr(capture.article_cleanup,'review',never)
    capture.run(1,iid,123)
    with session() as s:assert s.get(ContentItem,iid).body==original


def test_legacy_social_article_review_skips_missing_remote_video(monkeypatch):
    from bt.hub.models import ArticleText
    iid=seed()
    with session() as s:
        item=s.get(ContentItem,iid);item.type='news';item.provider='mastodon';item.body='Existing source report.'
        item.meta={'social_post':True,'media':{'type':'video','url':'https://example.org/clip.mp4'}};s.add(item)
        s.add(ArticleText(item_id=iid,raw_body=item.body,backfill=True));s.commit()
    def never(*a,**kw):raise AssertionError('Legacy cleanup must not download missing remote attachments')
    monkeypatch.setattr(capture.curation,'fetch',never)
    capture.run(1,iid,123)
    with session() as s:assert s.get(CaptureJob,(1,iid)).status=='complete'
