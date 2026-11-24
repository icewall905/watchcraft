"""The whole article behind a post, so the curator judges it and the reader gets a real summary.

Scraped listings and RSS entries often bring only a teaser, and their link text can glue an image label,
a category and the teaser onto the headline. Before the judge sees such a post, ``complete`` fetches its
page and swaps in the full text and the page's own headline. After a post is kept, ``sweep`` reads the full
article (the post's own page, or the article a link post points to) and stores a short summary of it. A pick
that holds nothing but a headline (no picture, clip, article text or text of its own) is scrapped.

Network I/O never runs inside a DB transaction. Child profiles keep seeing only kid-checked text: when a
picked post's body is replaced, its original stays in ``meta['teaser']``."""
import logging
import time
from urllib.parse import urlsplit

from sqlmodel import func, select, update

from .. import curation
from ..db import session
from ..hub import ai, extract
from .adapters.reddit import direct_image
from .models import CRCandidate, CRComments, CRPick, CRPost

log = logging.getLogger(__name__)
OWN_PAGE = ('web', 'rss', 'discourse', 'stackexchange')  # platforms whose post page is the article itself
TEASER = 1500                    # a body shorter than this is probably a teaser
MIN_ARTICLE = 400                # extracted text shorter than this is not an article
MAX_TEXT = 40_000
HTML_LIMIT = 3_000_000
JUDGE_FETCH_MAX = 12             # full articles fetched per curation run before judging
SUMMARY_MIN = 800                # texts shorter than this need no summary
MIN_SELF = 150                   # a post's own text this long is content in itself (a toot, a question)
MIN_DISCUSSION = 3               # stored replies that make a bare question worth showing
PER_TICK = 3                     # page fetches per tick
SUMMARY_BATCH = 6                # posts per summary call (one call per tick)
SCAN = 300                       # picked posts looked at per tick; most need neither a fetch nor a summary
FETCH_TRIES = 3
SUMMARY_TRIES = 3
NOT_ARTICLES = ('youtube.com', 'youtu.be', 'reddit.com', 'redd.it', 'news.ycombinator.com', 'imgur.com',
                'twitter.com', 'x.com', 'tiktok.com', 'instagram.com', 'facebook.com',
                'reddclips.com', 'streamable.com', 'vimeo.com')


def _host(url: str) -> str:
    return (urlsplit(url or '').hostname or '').lower().removeprefix('www.')


def article_url(post) -> tuple[str, bool]:
    """(url of the article behind this post, whether it is the post's own page); ('', False) for none."""
    if post.platform in OWN_PAGE and post.url and not post.link_url:
        url, own = post.url, True
    elif post.link_url:
        url, own = post.link_url, False
    else:
        return '', False
    host = _host(url)
    if not url.startswith('https://') or direct_image(url) or any(host == h or host.endswith('.' + h)
                                                                  for h in NOT_ARTICLES):
        return '', False
    return url, own


def fetch(url: str) -> dict | None:
    """{'title', 'text'} of the article at url, or None when the page holds no real article."""
    html = curation.fetch(url, kinds=curation.HTML_TYPES, limit=HTML_LIMIT)
    page = extract.extract_page(html, url)
    text = (page.get('body') or '').strip()
    if len(text) < MIN_ARTICLE:
        return None
    return {'title': (page.get('title') or '').strip(), 'text': text[:MAX_TEXT]}


def better_title(listing: str, page: str, text: str = '') -> str:
    """The article's own headline when the listing's link text merely wraps it (image label, category, teaser).
    Candidates: the page title and the first lines of the article (where its heading usually is)."""
    lt = ' '.join((listing or '').split())
    lines = [' '.join(x.split()) for x in (text or '').splitlines() if x.strip()][:6]
    cands = [c for c in [' '.join((page or '').split()), *lines]
             if 15 <= len(c) < len(lt) and c.lower() in lt.lower()]
    return max(cands, key=len)[:300] if cands else lt


def complete(posts: list, limit: int = JUDGE_FETCH_MAX) -> int:
    """Before judging: give own-page teaser posts their full article and headline (saved, so it happens once)."""
    done = 0
    for p in posts:
        if done >= limit:
            break
        url, own = article_url(p)
        if not own or len(p.body or '') >= TEASER or (p.meta or {}).get('article_checked'):
            continue
        done += 1
        try:
            got = fetch(url)
        except Exception as e:  # noqa: BLE001 — the teaser is judged instead
            log.debug('Common Room article %s failed: %s', url, e)
            got = None
        with session() as s:
            s.connection().exec_driver_sql('BEGIN IMMEDIATE')
            row = s.get(CRPost, p.id)
            if row:
                meta = {**(row.meta or {}), 'article_checked': time.time()}
                if got and len(got['text']) > len(row.body or ''):
                    meta['teaser'] = row.body or ''
                    row.body, row.title = got['text'], better_title(row.title, got['title'], got['text'])
                row.meta = meta
                s.add(row)
                p.body, p.title, p.meta = row.body, row.title, row.meta
            s.commit()
    return done


def _own_media(post) -> bool:
    """A picture or clip that is the post itself (a link's preview thumbnail does not count)."""
    return any(m.get('kind') == 'video' or m.get('from') != 'link' for m in post.media or [])


def _youtube(url: str) -> bool:
    host = _host(url)
    return host in ('youtube.com', 'm.youtube.com', 'youtu.be')


def captured(post, article: str, discussion: int = 0, body: str | None = None) -> bool:
    """Whether the Commons holds the post itself, not just a headline that sends the reader to the source:
    its own picture or clip, a playable YouTube video, the article text, its own text, or a stored discussion."""
    body = len(post.body or '' if body is None else body)
    if _own_media(post) or _youtube(post.link_url) or len(article) >= MIN_ARTICLE:
        return True
    if post.platform in OWN_PAGE and not post.link_url:
        return body >= MIN_ARTICLE  # an unreadable page leaves only its teaser
    return body >= MIN_SELF or discussion >= MIN_DISCUSSION


def _due(scan: int) -> list[CRPost]:
    """Picked posts not yet captured, newest first."""
    with session() as s:
        return list(s.exec(select(CRPost).where(CRPost.id.in_(select(CRPick.post_id)),
                                                func.json_extract(CRPost.meta, '$.captured').is_(None))
                           .order_by(CRPost.id.desc()).limit(scan)).all())


def _scrap(s, post_id: int) -> int:
    """Drop the picks of a post with nothing to show (saved ones stay) and keep threads from judging it again."""
    picks = list(s.exec(select(CRPick).where(CRPick.post_id == post_id, CRPick.saved == False)).all())  # noqa: E712
    for k in picks:
        s.exec(update(CRCandidate).where(CRCandidate.thread_id == k.thread_id, CRCandidate.post_id == post_id)
               .values(status='rejected'))
        s.delete(k)
    return len(picks)


def sweep(limit: int = PER_TICK, scan: int = SCAN) -> int:
    """For picked posts: read the whole article, store a short summary of the full text, and scrap picks that
    hold nothing but a headline. At most `limit` page fetches and one summary call per tick."""
    fetched, todo = 0, []
    due = _due(scan)
    with session() as s:
        sizes = {pid: len(tree or []) for pid, tree in s.exec(select(CRComments.post_id, CRComments.tree).where(
            CRComments.post_id.in_([p.id for p in due]))).all()} if due else {}
    for p in due:
        meta = p.meta or {}
        url, own = article_url(p)
        text, title, article, failed = '', '', (meta.get('article') or {}).get('text', ''), False
        need = bool(url) and not meta.get('article_checked') and (not own or len(p.body or '') < TEASER)
        if need:
            if fetched >= limit:
                continue
            fetched += 1
            try:
                got = fetch(url)
            except Exception as e:  # noqa: BLE001 — retried a few times, then judged without the page
                log.debug('Common Room article %s failed: %s', url, e)
                got, failed = None, meta.get('fetch_tries', 0) < FETCH_TRIES - 1
            if got:
                title = got['title']
                if own:
                    text = got['text'] if len(got['text']) > len(p.body or '') else ''
                else:
                    article = got['text']
        body = text or p.body or ''
        source = article if len(article) >= MIN_ARTICLE else body
        wants = len(source) >= SUMMARY_MIN and not meta.get('summary')
        if wants and sum(1 for t in todo if t['wants']) >= SUMMARY_BATCH:
            continue
        todo.append({'id': p.id, 'own': own, 'url': url, 'title': title, 'text': text, 'article': article,
                     'source': source, 'wants': wants, 'failed': failed, 'need': need,
                     'ok': captured(p, article, sizes.get(p.id, 0), body)})
    rows = [t for t in todo if t['wants'] and t['ok'] and not t['failed']]
    summaries = ai.summarize_items([{'title': t['title'], 'body': t['source']} for t in rows]) \
        if rows and ai.enabled() else {}
    for n, t in enumerate(rows):
        t['summary'] = summaries.get(n, '')
    scrapped = 0
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        for t in todo:
            row = s.get(CRPost, t['id'])
            if not row:
                continue
            meta = dict(row.meta or {})
            if t['failed']:  # the page did not answer: try again on a later tick
                meta['fetch_tries'] = meta.get('fetch_tries', 0) + 1
                row.meta = meta
                s.add(row)
                continue
            now = time.time()
            if t['need']:
                meta['article_checked'] = now
            if t['own'] and t['text']:
                meta.setdefault('teaser', row.body or '')
                row.body, row.title = t['text'], better_title(row.title, t['title'], t['text'])
            elif not t['own'] and t['url'] and len(t['article']) >= MIN_ARTICLE and not meta.get('article'):
                meta['article'] = {'url': t['url'], 'title': t['title'][:300], 'text': t['article']}
            done = True
            if not t['ok']:
                scrapped += _scrap(s, row.id)
                meta['scrapped'] = now
            elif t.get('summary'):
                meta['summary'] = t['summary']
            elif t['wants'] and ai.enabled() and meta.get('summary_tries', 0) < SUMMARY_TRIES:
                meta['summary_tries'] = meta.get('summary_tries', 0) + 1  # the summary call failed: try again later
                done = meta['summary_tries'] >= SUMMARY_TRIES
            if done:
                meta['captured'] = now
                meta['summary_checked'] = now
            row.meta = meta
            s.add(row)
        s.commit()
    if scrapped:
        log.info('Common Room scrapped %d picks that held only a headline', scrapped)
    return len(todo)
