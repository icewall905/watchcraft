"""The whole article behind a post, so the curator judges it and the reader gets a real summary.

Scraped listings and RSS entries often bring only a teaser, and their link text can glue an image label,
a category and the teaser onto the headline. Before the judge sees such a post, ``complete`` fetches its
page and swaps in the full text and the page's own headline. After a post is kept, ``sweep`` reads the full
article (the post's own page, or the article a link post points to) and stores a short summary of it.

Network I/O never runs inside a DB transaction. Child profiles keep seeing only kid-checked text: when a
picked post's body is replaced, its original stays in ``meta['teaser']``."""
import logging
import time
from urllib.parse import urlsplit

from sqlmodel import select

from .. import curation
from ..db import session
from ..hub import ai, extract
from .adapters.reddit import direct_image
from .models import CRPick, CRPost

log = logging.getLogger(__name__)
OWN_PAGE = ('web', 'rss')        # platforms whose post page is the article itself
TEASER = 1500                    # a body shorter than this is probably a teaser
MIN_ARTICLE = 400                # extracted text shorter than this is not an article
MAX_TEXT = 40_000
HTML_LIMIT = 3_000_000
JUDGE_FETCH_MAX = 12             # full articles fetched per curation run before judging
SUMMARY_MIN = 800                # texts shorter than this need no summary
PER_TICK = 3
NOT_ARTICLES = ('youtube.com', 'youtu.be', 'reddit.com', 'redd.it', 'news.ycombinator.com', 'imgur.com',
                'twitter.com', 'x.com', 'tiktok.com', 'instagram.com', 'facebook.com', 'github.com',
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


def _due(limit: int) -> list[CRPost]:
    with session() as s:
        rows = s.exec(select(CRPost).where(CRPost.id.in_(select(CRPick.post_id)))
                      .order_by(CRPost.id.desc()).limit(400)).all()
        out = []
        for p in rows:
            meta = p.meta or {}
            if meta.get('summary_checked') or not article_url(p)[0]:
                continue
            out.append(p)
            if len(out) >= limit:
                break
        return out


def sweep(limit: int = PER_TICK) -> int:
    """For kept posts: read the whole article and store a short summary of it (one summary call per tick)."""
    todo = []
    for p in _due(limit):
        url, own = article_url(p)
        text, title = (p.body or '') if own else '', ''
        if not own or (len(text) < TEASER and not (p.meta or {}).get('article_checked')):
            try:
                got = fetch(url)
            except Exception as e:  # noqa: BLE001 — no summary for a page we cannot read
                log.debug('Common Room article %s failed: %s', url, e)
                got = None
            if got:
                text, title = (got['text'] if len(got['text']) > len(text) else text), got['title']
        todo.append((p.id, own, url, title, text))
    rows = [{'title': t or '', 'body': text} for _, _, _, t, text in todo if len(text) >= SUMMARY_MIN]
    summaries = ai.summarize_items(rows) if rows and ai.enabled() else {}
    n = 0
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        for pid, own, url, title, text in todo:
            row = s.get(CRPost, pid)
            if not row:
                continue
            meta = {**(row.meta or {}), 'summary_checked': time.time(), 'article_checked': time.time()}
            if own and len(text) > len(row.body or ''):
                meta.setdefault('teaser', row.body or '')
                row.body, row.title = text, better_title(row.title, title, text)
            elif not own and len(text) >= MIN_ARTICLE:
                meta['article'] = {'url': url, 'title': title[:300], 'text': text}
            if len(text) >= SUMMARY_MIN:
                summary = summaries.get(n, '')
                n += 1
                if summary:
                    meta['summary'] = summary
                elif rows and not summaries and ai.enabled() and meta.get('summary_tries', 0) < 3:
                    meta.pop('summary_checked', None)  # the summary call failed: try again later (3 times)
                    meta['summary_tries'] = meta.get('summary_tries', 0) + 1
            row.meta = meta
            s.add(row)
        s.commit()
    return len(todo)
