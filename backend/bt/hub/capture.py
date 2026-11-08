"""Durable local capture and summary work for profile-visible discoveries."""
import json
import logging
import subprocess
import time
from urllib.parse import urlsplit

from sqlalchemy import func, text, update, or_
from sqlmodel import select

from .. import curation
from ..access import hub_allowed
from ..db import get_settings, session
from ..downloader.service import VID_RE
from ..models import Download, Profile, Video
from . import ai, rescue, slots, article_cleanup
from .extract import TEXT_LIMIT, extract_page, page_links
from .models import ArticleText, CaptureJob, CapturedAsset, CapturedPage, ContentItem, CuratorDiscovery, ItemState, Source

log = logging.getLogger(__name__)
LEASE = 3600
MAX_ATTEMPTS = 3
ASSET_CAP = 512_000_000


def asset_cap(s):
    return int(get_settings(s)['local_library'].get('asset_max_mb', 512)) * 1_000_000
IMAGE_TYPES = ('image/jpeg', 'image/png', 'image/webp', 'image/gif')
DOCUMENT_TYPES = ('application/pdf',)
VIDEO_TYPES = ('video/mp4', 'video/webm')
VIDEO_MAX_BYTES = 25_000_000
VIDEO_MAX_SECONDS = 180


def _captured_page_identity(item_type, author, publisher, published_at, page, fetched_url,
                            *, social_post=False):
    """Return conservative identity updates from a successfully read HTML page.

    Capture must not turn a social post (including one with a linked article) into
    the linked publisher's item.  For ordinary items, structured article/news
    evidence may promote a generic web discovery; existing identity values win.
    """
    if social_post or not isinstance(page, dict):
        return item_type, author, publisher, published_at, False
    page_type = page.get('type')
    promoted = item_type == 'web' and page_type in ('article', 'news')
    new_type = page_type if promoted else item_type
    new_author = author or (page.get('author') or '')
    new_published = published_at if published_at is not None else page.get('published_at')
    new_publisher = publisher or (page.get('publisher') or '')
    # A hostname is a useful, stable display fallback only once the page has
    # positively identified itself as an article/news page.
    if not new_publisher and promoted:
        host = (urlsplit(fetched_url or '').hostname or '').lower()
        if host.startswith('www.'):
            host = host[4:]
        new_publisher = host
    return new_type, new_author, new_publisher, new_published, promoted


def validate_video(data, mime):
    if len(data) > VIDEO_MAX_BYTES:
        raise ValueError('Meme video exceeds the 25 MB limit.')
    signature = (mime == 'video/mp4' and data[4:8] == b'ftyp') or (mime == 'video/webm' and data.startswith(b'\x1a\x45\xdf\xa3'))
    if not signature:
        raise ValueError('Video does not match its advertised format.')
    try:
        result = subprocess.run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'pipe',
                                 '-show_entries', 'format=duration:stream=codec_type',
                                 '-of', 'json', '-i', 'pipe:0'], input=data, capture_output=True, timeout=15, check=True)
        decoded = subprocess.run(['ffmpeg', '-v', 'error', '-protocol_whitelist', 'pipe', '-i', 'pipe:0',
                                  '-map', '0:v:0', '-frames:v', '1', '-f', 'framemd5', '-'],
                                 input=data, capture_output=True, timeout=15, check=True)
        if not any(line and not line.startswith(b'#') for line in decoded.stdout.splitlines()):
            raise ValueError('Meme video contains no playable frames.')
        info = json.loads(result.stdout)
        duration = float(info.get('format', {}).get('duration') or 0)
        if not 0 < duration <= VIDEO_MAX_SECONDS or not any(s.get('codec_type') == 'video' for s in info.get('streams', [])):
            raise ValueError('Meme videos must have a video track and be at most 3 minutes long.')
    except (subprocess.SubprocessError, ValueError, KeyError) as exc:
        raise ValueError('Meme video is invalid or exceeds the 3 minute limit.') from exc



def enqueue(s, pid, iid, retry=False, cleanup_only=False):
    job = s.get(CaptureJob, (pid, iid))
    if retry and not cleanup_only and (not job or job.status != 'running'):
        review = s.get(ArticleText, iid)
        if review:
            review.backfill = False
            s.add(review)
    if not job:
        job = CaptureJob(profile_id=pid, item_id=iid)
        s.add(job)
    elif retry and job.status != 'running':
        job.status, job.attempts, job.next_run, job.error = 'queued', 0, 0, ''
        s.add(job)
    return job


def visible(s, pid, iid):
    return bool(s.exec(select(CuratorDiscovery.id).where(CuratorDiscovery.profile_id == pid,
                CuratorDiscovery.item_id == iid)).first() or s.get(ItemState, (pid, iid)))


def refill(limit=50):
    """Backfill without duplicating jobs; child and deleted profiles are excluded."""
    with session() as s:
        s.exec(text('BEGIN IMMEDIATE'))
        rows = s.exec(select(CuratorDiscovery.profile_id, CuratorDiscovery.item_id)
                      .join(Profile, Profile.id == CuratorDiscovery.profile_id)
                      .where(Profile.role.in_(('adult', 'admin')))
                      .where(~select(CaptureJob.item_id).where(CaptureJob.profile_id == CuratorDiscovery.profile_id,
                              CaptureJob.item_id == CuratorDiscovery.item_id).exists())
                      .distinct().order_by(CuratorDiscovery.item_id).limit(limit)).all()
        for pid, iid in rows:
            if hub_allowed(s, pid): enqueue(s, pid, iid)
        states = s.exec(select(ItemState.profile_id, ItemState.item_id)
                        .join(Profile, Profile.id == ItemState.profile_id)
                        .where(Profile.role.in_(('adult', 'admin')))
                        .where(~select(CaptureJob.item_id).where(CaptureJob.profile_id == ItemState.profile_id,
                               CaptureJob.item_id == ItemState.item_id).exists()).limit(limit)).all()
        for pid, iid in states:
            if hub_allowed(s, pid) and not s.get(CaptureJob, (pid, iid)): enqueue(s, pid, iid)
        s.commit()


def _valid_asset(kind, mime, data):
    if kind == 'video':
        validate_video(data, mime)
        return True
    if kind == 'document': return mime == 'application/pdf' and data.startswith(b'%PDF-')
    return ((mime == 'image/jpeg' and data.startswith(b'\xff\xd8\xff')) or
            (mime == 'image/png' and data.startswith(b'\x89PNG\r\n\x1a\n')) or
            (mime == 'image/gif' and data.startswith((b'GIF87a', b'GIF89a'))) or
            (mime == 'image/webp' and data.startswith(b'RIFF') and data[8:12] == b'WEBP'))


def save_asset(pid, iid, kind, url, data, mime):
    if not _valid_asset(kind, mime, data): raise ValueError('Asset does not match its advertised format.')
    with session() as s:
        s.exec(text('BEGIN IMMEDIATE'))
        if not hub_allowed(s, pid) or not visible(s, pid, iid):
            raise ValueError('Profile no longer has access to this item.')
        old = s.get(CapturedAsset, (iid, kind))
        used = s.exec(select(func.coalesce(func.sum(CapturedAsset.size), 0))).one()
        if used - (old.size if old else 0) + len(data) > asset_cap(s):
            raise ValueError('Local asset storage limit reached.')
        affected = []
        if kind == 'video':
            # Posters may have been analyzed before the clip arrived, including by older releases.
            from .models import MemeExample, MemeFormat
            from .memes import _refresh_format
            affected = s.exec(select(MemeExample).where((MemeExample.item_id == iid) | (MemeExample.dup_of == iid))).all()
            formats = {ex.format_id for ex in affected if ex.format_id}
            for ex in affected:
                s.delete(ex)
            s.flush()
            for fid in formats:
                fmt = s.get(MemeFormat, fid)
                if fmt: _refresh_format(s, fmt)
        asset = old or CapturedAsset(item_id=iid, kind=kind, mime=mime, data=data)
        asset.mime, asset.data, asset.size, asset.source_url = mime, data, len(data), url
        s.add(asset); s.flush()
        if kind in ('image', 'video'):
            from .memes import analyze_item
            # Make a new meme visible immediately, even during the sweep throttle.
            for item_id in {iid, *(ex.item_id for ex in affected)}:
                item = s.get(ContentItem, item_id)
                if item and item.type == 'meme': analyze_item(s, item)
        s.commit()


def full_summary(title, body):
    """Map/reduce bounded chunks so the summary can draw on all captured text."""
    chunks = [body[n:n + 12000] for n in range(0, len(body), 12000)]
    if not chunks: raise ValueError('No captured text available to summarize.')
    summaries = []
    for start in range(0, len(chunks), 5):
        group = chunks[start:start + 5]
        result = ai.summarize_items([{'title': title, 'body': chunk} for chunk in group])
        if len(result) != len(group) or any(
                not isinstance(result.get(n), str) or not result[n].strip() for n in range(len(group))):
            raise ValueError('AI did not summarize every part of the item.')
        summaries.extend(result[n].strip() for n in range(len(group)))
    while len(summaries) > 1:
        next_level = []
        for start in range(0, len(summaries), 5):
            result = ai.summarize_items([{'title': title, 'body': '\n'.join(summaries[start:start + 5])}])
            if not isinstance(result.get(0), str) or not result[0].strip():
                raise ValueError('AI did not produce a final summary.')
            next_level.append(result[0].strip())
        summaries = next_level
    return summaries[0]


def run(pid, iid, lease):
    from .engine import index, _safe_error
    with session() as s:
        job, item = s.get(CaptureJob, (pid, iid)), s.get(ContentItem, iid)
        if not job or job.lease != lease or not item or not hub_allowed(s, pid) or not visible(s, pid, iid):
            return
        url, typ, title, image, body = item.url, item.type, item.title, item.image, item.body
        video_id = str(item.meta.get('video_id', ''))
        social_post = item.provider in ('mastodon', 'mastodon_trends', 'lemmy') and bool(item.meta.get('social_post', item.provider == 'mastodon'))
        linked_url = item.meta.get('article_url', '') if item.provider == 'lemmy' and typ in ('news', 'article') else ''
        linked_url = linked_url if isinstance(linked_url, str) and linked_url and linked_url != url else ''
        post_body = item.meta.get('post_body', body) if linked_url else ''
        archived_body = body
        prior_review = s.get(ArticleText, iid)
        review_only = bool(prior_review and prior_review.backfill and body)
        capture_url = linked_url or url
        meme_media = item.meta.get('media', {}) if typ == 'meme' or social_post else {}
        if isinstance(meme_media, dict) and meme_media.get('type') == 'image' and meme_media.get('url'):
            image = meme_media['url']
        # The feed that found this item may carry the whole article even when the page will not.
        feed_url = s.exec(select(Source.url).join(CuratorDiscovery, CuratorDiscovery.source_id == Source.id)
                          .where(CuratorDiscovery.item_id == iid, Source.provider == 'rss')).first() or ''
    errors, summary_status = [], 'pending'
    primary_error = ''
    page = None
    page_identity = None
    promoted = False
    promote_profiles = []
    links = []
    has_document = False
    is_pdf = False
    truncated = False
    payload = b''
    try:
        if review_only:
            pass  # Existing archives are reviewed locally; do not refetch their publishers.
        elif social_post and not linked_url:
            pass  # The public API already supplied the post body and attachment metadata.
        elif typ == 'video' and VID_RE.fullmatch(video_id):
            with session() as s:
                if not hub_allowed(s, pid) or not visible(s, pid, iid): return
                video = s.get(Video, video_id)
                if not video:
                    video = Video(id=video_id)
                    s.add(video)
                if video.transcript:
                    body = video.transcript[:TEXT_LIMIT]
                d = s.get(Download, video_id) or Download(video_id=video_id)
                d.pinned, d.requested_by_profile = True, d.requested_by_profile or pid
                if d.status in ('evicted', 'error'): d.status, d.attempts, d.error = 'queued', 0, ''
                s.add(d); s.commit()
        else:
            # A stored feed excerpt is not proof that this capture read the article.
            body = ''
            data, final, meta = curation.fetch(capture_url, kinds=curation.HTML_TYPES + DOCUMENT_TYPES,
                                                limit=20_000_000, with_metadata=True)
            payload = data
            if meta['content_type'] == 'application/pdf':
                is_pdf = True
                try:
                    save_asset(pid, iid, 'document', final, data, 'application/pdf')
                    has_document = True
                except Exception as e:  # noqa: BLE001 — text recovery can still succeed
                    errors.append('Document: ' + _safe_error(e))
                result = subprocess.run(['pdftotext', '-f', '1', '-l', '100', '-layout', '-', '-'],
                                        input=data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        timeout=30, check=True)
                extracted = result.stdout.decode('utf-8', 'replace').strip()
                truncated = len(extracted) > TEXT_LIMIT or extracted.count('\f') >= 100
                body = extracted[:TEXT_LIMIT]
                if not body: raise ValueError('Document has no extractable text.')
            else:
                if len(data) > 3_000_000: raise ValueError('HTML page exceeds 3 MB limit.')
                page = extract_page(data, final)
                links = page_links(data, final)
                body = page['body']
                truncated = len(body) >= TEXT_LIMIT
                if not body: raise ValueError('No readable text was found on this page.')
                image = page.get('image') or image
                page_identity = _captured_page_identity(
                    typ, '', '', None, page, final,
                    social_post=social_post)
    except Exception as e:  # noqa: BLE001
        primary_error = _safe_error(e)
        errors.append(primary_error)
    if not body and typ not in ('video', 'meme') and (not social_post or linked_url) \
            and get_settings()['local_library'].get('rescue', True):
        # The plain fetch could not read it. Try the other ways before giving up on the item.
        try:
            ctx = {'url': capture_url, 'title': title, 'html': payload, 'data': payload, 'feed_url': feed_url}
            # A downloaded PDF needs local OCR, not article refetches or a browser.
            got = ({'body': rescue.tool_ocr(ctx), 'tool': 'ocr'} if is_pdf
                   else rescue.rescue(ctx, primary_error))
            if got['body']:
                body = got['body']
                if is_pdf:
                    truncated = bool(ctx.get('_ocr_truncated', True))
                # The failure is resolved, so it is no longer an error: the job is complete.
                errors = [e for e in errors if e != primary_error]
                log.info('capture %s recovered with %s', iid, got['tool'])
        except Exception as e:  # noqa: BLE001 — recovery is best effort
            errors.append('Rescue: ' + _safe_error(e))
    if linked_url and not review_only:
        combined = (f'Post text:\n{post_body}\n\nLinked article:\n{body}' if body
                    else archived_body or post_body)
        truncated = truncated or len(combined) > TEXT_LIMIT
        body = combined[:TEXT_LIMIT]
    if image and not review_only:
        try:
            with session() as s:
                old = s.get(CapturedAsset, (iid, 'image'))
                exists = old is not None and old.source_url == image
            if not exists:
                data, final, meta = curation.fetch(image, kinds=IMAGE_TYPES, limit=5_000_000, with_metadata=True)
                save_asset(pid, iid, 'image', final, data, meta['content_type'])
        except Exception as e:  # noqa: BLE001
            errors.append('Image: ' + _safe_error(e))
    if not review_only and isinstance(meme_media, dict) and meme_media.get('type') in ('video', 'gifv') and meme_media.get('url'):
        try:
            with session() as s: exists = s.get(CapturedAsset, (iid, 'video')) is not None
            if not exists:
                data, final, meta = curation.fetch(meme_media['url'], kinds=VIDEO_TYPES,
                                                   limit=VIDEO_MAX_BYTES, with_metadata=True)
                save_asset(pid, iid, 'video', final, data, meta['content_type'])
        except Exception as e:  # noqa: BLE001 — poster and original remain available on failure
            errors.append('Video: ' + _safe_error(e))
    raw_body = body
    cleanup = None
    if body and typ in ('news', 'article', 'web'):
        cleanup = ({'body': body, 'status': prior_review.status, 'removed': prior_review.removed}
                   if prior_review and body == archived_body and prior_review.status == 'reviewed' and prior_review.version == article_cleanup.VERSION
                   else article_cleanup.review(title, body))
        body = cleanup['body']
        if cleanup['status'] == 'failed':
            errors.append('Cleanup: worker review failed; original text retained.')
    summary = ''
    with session() as s:
        if not hub_allowed(s, pid) or not visible(s, pid, iid): return
    if body and ai.enabled():
        try:
            summary = full_summary(title, body)
            summary_status = 'ready'
        except Exception as e:  # noqa: BLE001
            summary_status = 'failed'
            errors.append('Summary: ' + _safe_error(e))
    else:
        summary_status = 'unavailable' if not ai.enabled() else 'no_text'
    with session() as s:
        s.exec(text('BEGIN IMMEDIATE'))
        job, item = s.get(CaptureJob, (pid, iid)), s.get(ContentItem, iid)
        if not job or job.lease != lease or not item or not hub_allowed(s, pid) or not visible(s, pid, iid): return
        if page is not None or has_document:
            snapshot = s.get(CapturedPage, iid) or CapturedPage(item_id=iid)
            snapshot.links, snapshot.captured_at = links, time.time()
            snapshot.truncated = truncated
            s.add(snapshot)
        if cleanup:
            review = s.get(ArticleText, iid) or ArticleText(item_id=iid, raw_body=max((archived_body, raw_body), key=len))
            review.status, review.version = cleanup['status'], article_cleanup.VERSION
            review.removed, review.reviewed_at = cleanup['removed'], time.time()
            s.add(review)
        if page_identity:
            # Merge into the freshly loaded row: another scout/profile may have
            # learned identity while this worker was fetching the page.
            page_type, page_author, page_publisher, page_published, _ = page_identity
            was_type = item.type
            if not social_post and item.type == 'web' and page_type in ('article', 'news'):
                item.type = page_type
            item.author = item.author or page_author
            item.publisher = item.publisher or page_publisher
            if item.published_at is None:
                item.published_at = page_published
            promoted = was_type == 'web' and item.type in ('article', 'news')
            if promoted:
                promote_profiles = set(s.exec(select(CuratorDiscovery.profile_id).where(
                    CuratorDiscovery.item_id == iid)).all())
                promote_profiles.update(s.exec(select(ItemState.profile_id).where(
                    ItemState.item_id == iid)).all())
                promote_profiles = [profile_id for profile_id in promote_profiles
                                    if (profile := s.get(Profile, profile_id))
                                    and profile.role in ('adult', 'admin') and hub_allowed(s, profile_id)]
        if body:
            item.body = body
            if linked_url: item.meta = {**item.meta, 'post_body': post_body}
            if summary: item.enrichment = {**item.enrichment, 'summary': summary}
            s.add(item); s.flush(); index(s, item)
        job.summary_status, job.updated_at = summary_status, time.time()
        job.error = '; '.join(errors)[:500]
        if errors and job.attempts < MAX_ATTEMPTS:
            job.status, job.next_run = 'queued', time.time() + 300 * (2 ** (job.attempts - 1))
        else:
            job.status, job.next_run = ('partial' if errors and (body or has_document or typ == 'video') else 'failed') if errors else 'complete', 0
        s.add(job); s.commit()
    if promoted:
        # Queue the normal profile-scoped master pass after the item commit. It
        # rejudges the promoted item without touching manual verdicts.
        try:
            from .master import schedule as master
            for profile_id in promote_profiles:
                master.mark(profile_id)
        except Exception:  # noqa: BLE001 — capture success must survive queue issues
            log.exception('could not queue master curator after article promotion for %s', iid)


def dispatch():
    """Claim one job across processes; a lost worker's lease becomes due again."""
    if not get_settings()['local_library'].get('enabled', True): return False
    refill()
    now = time.time()
    if ai.enabled():
        with session() as s:
            allowed = [p.id for p in s.exec(select(Profile).where(Profile.role.in_(('adult', 'admin')))).all()
                       if hub_allowed(s, p.id)]
            legacy = s.exec(select(CaptureJob, ContentItem).join(ContentItem, ContentItem.id == CaptureJob.item_id)
                            .where(CaptureJob.profile_id.in_(allowed), or_(
                                   select(CuratorDiscovery.id).where(CuratorDiscovery.profile_id == CaptureJob.profile_id, CuratorDiscovery.item_id == ContentItem.id).exists(),
                                   select(ItemState.item_id).where(ItemState.profile_id == CaptureJob.profile_id, ItemState.item_id == ContentItem.id).exists()),
                                   CaptureJob.status.in_(('complete', 'partial')), ContentItem.type.in_(('news', 'article', 'web')),
                                   ContentItem.body != '', ~select(ArticleText.item_id).where(ArticleText.item_id == ContentItem.id).exists())
                            .order_by(ContentItem.id.desc()).limit(10)).all()
            for pending, item in legacy:
                if hub_allowed(s, pending.profile_id) and visible(s, pending.profile_id, item.id) and not s.get(ArticleText, item.id):
                    s.add(ArticleText(item_id=item.id, raw_body=item.body, backfill=True))
                    enqueue(s, pending.profile_id, item.id, retry=True, cleanup_only=True)
                    pending.next_run = now
                    s.add(pending)
                    s.flush()
            jobs = s.exec(select(CaptureJob).join(ContentItem, ContentItem.id == CaptureJob.item_id)
                          .where(CaptureJob.status == 'complete', CaptureJob.summary_status == 'unavailable',
                                 ContentItem.body != '').limit(50)).all()
            for pending in jobs:
                pending.status, pending.next_run = 'queued', now
                s.add(pending)
            videos = s.exec(select(CaptureJob, ContentItem).join(ContentItem, ContentItem.id == CaptureJob.item_id)
                            .where(CaptureJob.status == 'complete', CaptureJob.summary_status == 'no_text',
                                   ContentItem.type == 'video').limit(100)).all()
            for pending, item in videos:
                video = s.get(Video, item.meta.get('video_id', ''))
                if video and video.transcript:
                    pending.status, pending.next_run = 'queued', now
                    s.add(pending)
            s.commit()
    with session() as s:
        s.exec(text('BEGIN IMMEDIATE'))
        job = s.exec(select(CaptureJob).where(((CaptureJob.status == 'queued') & (CaptureJob.next_run <= now)) |
                     ((CaptureJob.status == 'running') & (CaptureJob.lease <= now)))
                     .order_by(CaptureJob.next_run, CaptureJob.updated_at).limit(1)).first()
        if not job: return False
        if not hub_allowed(s, job.profile_id) or not visible(s, job.profile_id, job.item_id):
            job.status, job.error = 'failed', 'Profile no longer has access to this item.'
            s.add(job); s.commit(); return False
        job.status, job.lease, job.updated_at = 'running', now + LEASE, now
        job.attempts += 1
        pid, iid, lease = job.profile_id, job.item_id, job.lease
        s.add(job); s.commit()
    if slots.spawn_lane('local-capture', 'capture', iid, pid, 'Local capture', run, pid, iid, lease): return True
    with session() as s:
        s.exec(update(CaptureJob).where(CaptureJob.profile_id == pid, CaptureJob.item_id == iid, CaptureJob.lease == lease)
               .values(status='queued', attempts=CaptureJob.attempts - 1, lease=0,
                       next_run=time.time() + 30)); s.commit()
    return False
