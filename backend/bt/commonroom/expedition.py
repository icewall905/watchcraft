"""Common Room source expedition: researches where a thread's kind of posts keep appearing and follows the
places whose sample the judge accepts. Modeled on the Mood Board source curator (hub/board_curator).

Tiers, until enough sources fit or time runs out:
1. the shared library: sources other threads already follow and keep posts from;
2. sources the model can name directly on every platform adapter (subreddits, Lemmy/Mbin communities,
   Mastodon tags, Discourse forums, Stack Exchange sites, Tildes groups, DEV tags, blog/newsletter feeds);
3. rounds of web research that learn from earlier failures: search hits are recognised as platform
   communities, probed for feeds, or turned into scraped listing recipes (plain fetch, then the isolated
   browser sidecar).

Every candidate source must return recent posts and pass a sample judge before it is linked. Runs as a
durable CRSourceHunt job with a lease. Search results and page markup are untrusted; the model only plans
searches, names candidates and scores samples; recipes come from fixed heuristics (adapters/web.propose).
"""
import asyncio
import json
import logging
import math
import re
import secrets
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

from sqlalchemy import update
from sqlmodel import select

from ..access import hub_worker_allowed
from ..db import session
from ..hub import ai
from ..hub import site_recipes
from . import curate, store
from .adapters import ADAPTERS
from .adapters import web as web_adapter
from .models import CRSource, CRSourceHunt, CRThread, CRThreadSource

log = logging.getLogger(__name__)

HUNT_DEADLINE_S = 1200
LEASE_S = 900                 # renewed by every progress note
MAX_ROUNDS = 3                # web research rounds; later rounds learn from earlier failures
MAX_SITES = 10                # search hits inspected per round
MAX_TRIALS = 40               # sources given a sample judgement per hunt
TARGET_NEW = 10               # stop once this many new sources fit
MAX_NAMED = 24                # named candidates tried per hunt
SAMPLE = 10                   # posts judged per candidate
MIN_POSTS = 5                 # a source must return at least this many recent posts
RECENT_S = 120 * 86400        # ... published within this window (undated posts count as recent)
LOG_LIMIT = 60
WEEKLY_S = 7 * 86400
DRY_GAP_S = 12 * 3600         # a dry feed asks for a new expedition at most this often
KID_SKIP = ('reddit',)        # platforms a child thread never follows
# Platforms where people reply to each other. Most blog, DEV and Mastodon-tag posts arrive with no replies, and
# Reddit blocks the production host, so discovery tries these first and offers Lemmy twins of subreddits.
DISCUSSION = ('lemmy', 'mbin', 'hackernews', 'lobsters', 'tildes', 'discourse', 'bluesky', 'stackexchange', 'reddit')
TWIN_INSTANCE = 'lemmy.world'  # where a subreddit's Lemmy twin is first looked for
SKIP_HOSTS = ('facebook.com', 'instagram.com', 'x.com', 'twitter.com', 'tiktok.com', 'youtube.com', 'youtu.be',
              'linkedin.com', 'pinterest.', 'amazon.', 'ebay.', 'etsy.com', 'wikipedia.org', 'quora.com',
              'play.google.com', 'apps.apple.com')


class Stop(Exception):
    pass


# --- Job bookkeeping -----------------------------------------------------------------------------------

def active(s, tid):
    return s.exec(select(CRSourceHunt).where(CRSourceHunt.thread_id == tid,
                                             CRSourceHunt.status.in_(['queued', 'running']))).first()


def enqueue(s, tid: int, reason: str = 'button') -> CRSourceHunt | None:
    """Queue an expedition unless one is waiting or running for this thread. Caller commits."""
    if active(s, tid):
        return None
    hunt = CRSourceHunt(thread_id=tid, reason=reason,
                        log=[{'at': time.time(), 'step': 'queued', 'message': 'Source expedition queued.'}])
    s.add(hunt)
    t = s.get(CRThread, tid)
    if t:
        t.discovered_at = time.time()
        s.add(t)
    return hunt


def request(tid: int, reason: str, min_gap: float = DRY_GAP_S) -> bool:
    """Queue an expedition when none ran for this thread within ``min_gap`` seconds."""
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        last = s.exec(select(CRSourceHunt).where(CRSourceHunt.thread_id == tid)
                      .order_by(CRSourceHunt.id.desc()).limit(1)).first()
        if last and (last.status in ('queued', 'running') or last.created_at > time.time() - min_gap):
            s.commit()
            return False
        queued = enqueue(s, tid, reason) is not None
        s.commit()
        return queued


def note(hid, lease, step, message):
    with session() as s:
        hunt = s.get(CRSourceHunt, hid)
        if not hunt or hunt.lease != lease:
            raise Stop()
        hunt.log = (list(hunt.log or []) + [{'at': time.time(), 'step': step, 'message': message[:300]}])[-LOG_LIMIT:]
        hunt.lease_until = time.time() + LEASE_S
        s.add(hunt)
        s.commit()


def finish(hid, lease, status, found, error=''):
    with session() as s:
        hunt = s.get(CRSourceHunt, hid)
        if not hunt or hunt.lease != lease:
            return
        hunt.status, hunt.found, hunt.error = status, found, error[:300]
        hunt.lease, hunt.lease_until, hunt.finished_at = '', 0, time.time()
        s.add(hunt)
        s.commit()


def dispatch_due() -> bool:
    now = time.time()
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        s.exec(update(CRSourceHunt).where(CRSourceHunt.status == 'running', CRSourceHunt.lease_until < now)
               .values(status='queued', lease=''))
        hunt = s.exec(select(CRSourceHunt).where(CRSourceHunt.status == 'queued')
                      .order_by(CRSourceHunt.id).limit(1)).first()
        if not hunt:
            s.commit()
            return False
        thread = s.get(CRThread, hunt.thread_id)
        if not thread or not hub_worker_allowed(s, thread.profile_id):
            hunt.status, hunt.error, hunt.finished_at = 'failed', 'Thread no longer available.', now
            s.add(hunt)
            s.commit()
            return True
        lease = secrets.token_hex(16)
        hunt.status, hunt.lease, hunt.lease_until, hunt.started_at = 'running', lease, now + LEASE_S, now
        s.add(hunt)
        s.commit()
        hid, tid = hunt.id, hunt.thread_id
    try:
        found = hunt_sources(hid, lease)
        finish(hid, lease, 'done', found)
    except Stop:
        pass
    except TimeoutError:
        with session() as s:
            found = len(s.exec(select(CRThreadSource.id).where(CRThreadSource.thread_id == tid,
                                                               CRThreadSource.created_at >= now)).all())
        try:
            note(hid, lease, 'done', f'Time is up; {found} new sources followed. Run Find sources again for more.')
        except Stop:
            return True
        finish(hid, lease, 'done', found)
    except Exception as exc:  # noqa: BLE001 — any failure ends this hunt, never the scheduler
        log.warning('Common Room source hunt %s failed: %s: %s', hid, type(exc).__name__, str(exc)[:300])
        message = str(exc)[:300] if isinstance(exc, ValueError) else 'Source expedition failed.'
        try:
            note(hid, lease, 'failed', message)
        except Stop:
            return True
        finish(hid, lease, 'failed', 0, message)
    return True


async def scheduler():
    while True:
        try:
            worked = await asyncio.to_thread(dispatch_due)
        except Exception:
            log.exception('Common Room source expedition tick failed')
            worked = False
        await asyncio.sleep(2 if worked else 15)


# --- Model calls ---------------------------------------------------------------------------------------

def _context(t: CRThread) -> str:
    out = f'Thread: {t.name}\nInterests: {t.interests}'
    if t.instructions.strip():
        out += f'\nInstructions: {t.instructions[:800]}'
    if t.avoid.strip():
        out += f'\nAvoid: {t.avoid[:400]}'
    return out


def _named(t: CRThread, child: bool, platforms: list) -> tuple[list[tuple[str, str]], list[str]]:
    """Sources the model can name without a web search, plus subtopics to search for."""
    schema = {'type': 'object', 'properties': {
        'sources': {'type': 'array', 'maxItems': 30, 'items': {'type': 'object', 'properties': {
            'platform': {'type': 'string'}, 'locator': {'type': 'string', 'maxLength': 300}},
            'required': ['platform', 'locator']}},
        'search_terms': {'type': 'array', 'maxItems': 20, 'items': {'type': 'string', 'maxLength': 80}}},
        'required': ['sources', 'search_terms']}
    ordered = sorted(platforms, key=lambda a: a.key not in DISCUSSION)
    listing = '\n'.join(f'- {a.key}: {a.label}, locator like {a.example!r}'
                        + (' (discussion)' if a.key in DISCUSSION else '') for a in ordered)
    try:
        out = ai._call(
            'You find lasting places where people keep posting and discussing things a reader cares about: '
            'active communities, forums, Q&A sites, tags, and blogs or newsletters with an RSS feed (use the "rss" '
            'platform with the full https feed URL, e.g. https://name.substack.com/feed or https://medium.com/feed/tag/x). '
            'Suggest up to 30 real, active places across the platforms below, most valuable first, mixing platforms; '
            'prefer focused, high-quality ones over huge generic ones. The feed lives on conversation: at least two '
            'thirds should be discussion platforms (marked below) where posts get replies: Lemmy and Mbin/PieFed '
            'communities, Hacker News, Lobsters tags, Tildes groups, Discourse forums, Bluesky accounts. Reddit is '
            'often unreachable, so for every subreddit you would suggest also name its Lemmy or Mbin equivalent '
            '(PieFed communities work as Lemmy ones via a Lemmy instance; e.g. for an "interesting things" thread: '
            'interestingasfuck@lemmy.world, damnthatsinteresting@lemmy.world, til@lemmy.world). Blogs, DEV tags and '
            'Mastodon hashtags rarely have replies: use them only for what discussion places lack, and never name '
            'directories, awesome-list mirrors, feed aggregators or vendor promo accounts. Use exactly the locator '
            'format shown. Only name '
            'places you are confident exist today. search_terms: 10-20 short, specific subtopics, names and keywords '
            'that find fresh posts for this reader (not just the interests repeated).'
            + (' The reader is a child: only places suitable for 10-12 year olds.' if child else ''),
            f'Platforms:\n{listing}\n\n<data>\n{ai._esc(_context(t))}\n</data>',
            schema, max_tokens=1800, role='commonroom_discover', timeout_s=120)
    except Exception as exc:  # noqa: BLE001
        log.info('Common Room named sources failed: %s', str(exc)[:200])
        return [], []
    keys = {a.key for a in platforms}
    sources = [(str(r.get('platform')), str(r.get('locator') or '')[:300]) for r in (out.get('sources') or [])
               if isinstance(r, dict) and r.get('platform') in keys and r.get('locator')]
    sources = favour_discussion(with_lemmy_twins(sources, 'lemmy' in keys))
    terms = [str(x).strip()[:80] for x in (out.get('search_terms') or []) if isinstance(x, str) and x.strip()]
    return sources, list(dict.fromkeys(terms))[:20]


_SUB = re.compile(r'^(?:https?://(?:www\.|old\.)?reddit\.com)?/?(?:r/)?([A-Za-z0-9_]{2,21})/?$')


def with_lemmy_twins(sources: list[tuple[str, str]], lemmy: bool = True) -> list[tuple[str, str]]:
    """Follow each subreddit with a guess at its Lemmy twin (same name on lemmy.world); the fetch and the sample
    judge drop guesses that do not exist or do not fit."""
    if not lemmy:
        return sources
    have = {(p, loc.lower()) for p, loc in sources}
    out = []
    for platform, loc in sources:
        out.append((platform, loc))
        m = _SUB.match(loc.strip()) if platform == 'reddit' else None
        if m:
            twin = ('lemmy', f'{m.group(1).lower()}@{TWIN_INSTANCE}')
            if twin not in have:
                have.add(twin)
                out.append(twin)
    return out


def favour_discussion(sources: list) -> list:
    """Discussion platforms first, keeping the model's order within each group."""
    return sorted(sources, key=lambda r: (r[0] if isinstance(r, tuple) else r.platform) not in DISCUSSION)


def _queries(t: CRThread, child: bool, known_hosts: list[str], tried: list[str] | None = None) -> list[str]:
    schema = {'type': 'object', 'properties': {'queries': {'type': 'array', 'minItems': 1, 'maxItems': 6,
              'items': {'type': 'string', 'maxLength': 120}}}, 'required': ['queries']}
    history = ('\nAlready tried (do not repeat these sites or angles; learn from why they failed):\n'
               + '\n'.join(tried[-30:])) if tried else ''
    try:
        out = ai._call(
            'You research where on the web a reader can keep finding fresh, worthwhile posts and articles on their '
            'interests. Write up to six varied web-search queries that find SOURCES, not single articles, favouring '
            'places where people reply to each other: forums and discussion boards (Discourse, phpBB, Flarum), Lemmy, '
            'Mbin and PieFed communities, Q&A sites, then blogs and newsletters (Substack, Ghost, Medium) with '
            'comments. Skip directories, "alternatives to" sites and feed aggregators. Vary the angle: forum, '
            'community, lemmy, discussion board, blog, newsletter. '
            + ('Earlier attempts failed: try clearly different kinds of sites and more specific niche terms. ' if tried else '')
            + ('The reader is a child: only family-friendly, educational sources. ' if child else '')
            + 'Return queries only.',
            '<data>' + ai._esc(_context(t)) + '\nAlready followed sites: ' + ai._esc(', '.join(known_hosts[:25]) or 'none')
            + ai._esc(history) + '</data>',
            schema, max_tokens=300, role='commonroom_discover', timeout_s=120)
        phrases = [q.strip()[:120] for q in out.get('queries', []) if isinstance(q, str) and q.strip()]
        if phrases:
            return list(dict.fromkeys(phrases))[:6]
    except Exception as exc:  # noqa: BLE001 — fall back to fixed angles
        log.info('Common Room source query planning failed: %s', str(exc)[:200])
    topic = (re.split(r'[,;\n]+', t.interests or t.name)[0].strip() or t.name)[:80]
    if tried:
        return [f'{topic} discussion board', f'{topic} lemmy community', f'{topic} newsletter substack']
    return [f'{topic} forum', f'{topic} community discussion', f'best {topic} blogs'] \
        + ([] if child else [f'{topic} lemmy'])


def _triage(t: CRThread, rows: list[dict]) -> list[dict]:
    if len(rows) <= 3:
        return rows
    schema = {'type': 'object', 'properties': {'pick': {'type': 'array', 'maxItems': MAX_SITES,
              'items': {'type': 'integer', 'minimum': 0, 'maximum': len(rows) - 1}}}, 'required': ['pick']}
    listing = [{'n': i, 'site': r['host'], 'title': r['title'][:150], 'snippet': r['summary'][:200]}
               for i, r in enumerate(rows)]
    try:
        out = ai._call(
            'Pick the search results most likely to be lasting sources of many fresh, on-topic posts for this reader: '
            'forums, communities, active blogs and newsletters, article listings, curated lists of such places. Skip '
            'single news articles, shops, landing pages and off-topic results. Return the chosen n values, best first.',
            '<data>' + ai._esc(_context(t)) + '\n' + ai._esc(json.dumps(listing, ensure_ascii=False)) + '</data>',
            schema, max_tokens=200, role='commonroom_discover', timeout_s=120)
        pick = [i for i in dict.fromkeys(out.get('pick') or []) if isinstance(i, int) and 0 <= i < len(rows)]
        if pick:
            return [rows[i] for i in pick]
    except Exception as exc:  # noqa: BLE001
        log.info('Common Room source triage failed: %s', str(exc)[:200])
    return rows


def _library_fits(t: CRThread, sources: list[CRSource]) -> list[int]:
    if not sources:
        return []
    schema = {'type': 'object', 'properties': {'pick': {'type': 'array', 'maxItems': 8, 'items': {'type': 'integer'}}},
              'required': ['pick']}
    listing = [{'id': x.id, 'platform': x.platform, 'name': describe(x.platform, x.locator, x.label),
                'notes': x.notes[:200]} for x in favour_discussion(sources)[:40]]
    try:
        out = ai._call('These sources are followed by other threads. Pick the ids of the ones likely to have many '
                       'posts for this thread too. Return ids only.',
                       '<data>' + ai._esc(_context(t)) + '\n' + ai._esc(json.dumps(listing, ensure_ascii=False)) + '</data>',
                       schema, max_tokens=150, role='commonroom_discover', timeout_s=120)
        ids = {x.id for x in sources}
        return [i for i in dict.fromkeys(out.get('pick') or []) if i in ids]
    except Exception:  # noqa: BLE001
        return []


# --- Recognising sources ------------------------------------------------------------------------------

def describe(platform: str, locator: str, label: str = '') -> str:
    if platform == 'web':
        try:
            r = web_adapter.recipe_of(locator)
        except ValueError:
            return 'website'
        browser = ' (browser)' if r.get('via') == 'browser' else ''
        return ('site search ' if r.get('url_template') else 'listing ') + web_adapter.host(locator) + browser
    if platform == 'rss':
        return 'feed ' + (urlsplit(locator).hostname or locator)
    name = ADAPTERS[platform].label if platform in ADAPTERS else platform
    shown = label if label and label != locator else locator
    return f'{name} {shown}'


def source_host(platform: str, locator: str) -> str:
    if platform == 'web':
        return web_adapter.host(locator)
    if locator.startswith('https://'):
        return (urlsplit(locator).hostname or '').removeprefix('www.')
    return platform


def known_community(url: str) -> tuple[str, str] | None:
    """Platform communities recognised from an address, before any page is fetched."""
    p = urlsplit(url)
    host = (p.hostname or '').lower().removeprefix('www.').removeprefix('old.')
    path = [x for x in p.path.split('/') if x]
    if host == 'reddit.com' and len(path) >= 2 and path[0].lower() == 'r':
        return 'reddit', f'r/{path[1]}'
    if host.endswith('.substack.com'):
        return 'rss', f'https://{host}/feed'
    if host == 'medium.com' and len(path) >= 2 and path[0] == 'tag':
        return 'rss', f'https://medium.com/feed/tag/{path[1]}'
    if host == 'medium.com' and path and path[0].startswith('@'):
        return 'rss', f'https://medium.com/feed/{path[0]}'
    if host == 'dev.to' and len(path) >= 2 and path[0] == 't':
        return 'devto', path[1]
    if host == 'tildes.net' and path and path[0].startswith('~'):
        return 'tildes', path[0]
    if host.endswith('stackexchange.com') and host.count('.') == 2:
        return 'stackexchange', host.split('.')[0]
    if len(path) >= 2 and path[0] == 'c' and re.fullmatch(r'[A-Za-z0-9_]{2,40}', path[1]) and host:
        return 'lemmy', f'{path[1].lower()}@{host}'
    if len(path) >= 2 and path[0] == 'tags' and re.fullmatch(r'\w{2,60}', path[1]) and host:
        return 'mastodon', f'#{path[1]}@{host}'
    return None


def _discourse(html: bytes) -> bool:
    head = html[:20000].lower()
    return b'content="discourse' in head or b'data-discourse' in head


# --- The expedition -----------------------------------------------------------------------------------

def refusal(platform: str, exc: Exception) -> bool:
    """A whole platform said no: rate limited, or Reddit's anonymous block (it answers 403/404/429)."""
    from ..hub import providers
    from ..hub.social_fetch import Paused
    if isinstance(exc, (Paused, providers.RateLimited)):
        return True
    return platform == 'reddit' and getattr(exc, 'status', None) in (403, 404, 429)


def _fresh_enough(p: dict, now: float) -> bool:
    published = float(p.get('published_at') or 0)
    return not published or published >= now - RECENT_S


def hunt_sources(hid, lease) -> int:
    from ..hub import browser, providers
    started = time.time()

    def say(step, message):
        note(hid, lease, step, message)
        if time.time() - started > HUNT_DEADLINE_S and step not in ('saved', 'done'):
            raise TimeoutError('Source expedition ran out of time.')

    with session() as s:
        hunt = s.get(CRSourceHunt, hid)
        thread = s.get(CRThread, hunt.thread_id)
        s.expunge(thread)
        tid = thread.id
        linked = s.exec(select(CRSource).join(CRThreadSource, CRThreadSource.source_id == CRSource.id)
                        .where(CRThreadSource.thread_id == tid)).all()
        linked_keys = {(x.platform, x.locator) for x in linked}
        known_hosts = sorted({source_host(x.platform, x.locator) for x in linked})
        library = s.exec(select(CRSource).where(CRSource.status == 'ok', CRSource.id.not_in([x.id for x in linked] or [0]),
                                                CRSource.kept > 0)
                         .order_by(CRSource.kept.desc()).limit(40)).all()
        for x in library:
            s.expunge(x)
    if not ai.enabled():
        raise ValueError('The source expedition needs the AI model to be configured.')
    child = bool(thread.child_friendly)
    strict = child or thread.strictness == 'strict'
    threshold = curate.THRESHOLD.get('strict' if child else thread.strictness, 60)
    platforms = [a for a in ADAPTERS.values() if a.key not in (KID_SKIP if child else ())]
    allowed = {a.key for a in platforms}
    saved, trials, tried = 0, 0, []
    refused: set[str] = set()  # platforms that refused us this hunt (rate limit, anonymous block)

    def enough():
        return saved >= TARGET_NEW or trials >= MAX_TRIALS

    def judge(posts: list[dict]) -> tuple[int, int] | None:
        sample = [SimpleNamespace(id=i, title=p.get('title') or '', platform=p.get('platform') or '',
                                  community=p.get('community') or '', body=p.get('body') or '',
                                  link_url=p.get('link_url') or '', url=p.get('url') or '',
                                  comments=int(p.get('comments') or 0))
                  for i, p in enumerate(posts[:SAMPLE])]
        got = curate._judge(thread, '', sample)
        if not got:
            return None
        return sum(1 for v in got.values() if v.score >= threshold), len(got)

    def link(sid: int, fit: int, n: int):
        nonlocal saved
        with session() as s:
            s.connection().exec_driver_sql('BEGIN IMMEDIATE')
            current = s.get(CRSourceHunt, hid)
            if not current or current.lease != lease or not s.get(CRThread, tid):
                raise Stop()
            if not s.exec(select(CRThreadSource).where(CRThreadSource.thread_id == tid,
                                                       CRThreadSource.source_id == sid)).first():
                s.add(CRThreadSource(thread_id=tid, source_id=sid, origin='ai', fit=fit, tried=n))
                t = s.get(CRThread, tid)
                t.next_run = 0
                s.add(t)
                saved += 1
            s.commit()

    def attempt(platform: str, locator: str, row: dict | None = None, quiet: bool = False) -> str:
        """Fetch, sample-judge and follow one source. Returns 'saved', 'few', 'misfit' or 'error'."""
        nonlocal trials
        if platform not in allowed or platform in refused:
            return 'error'
        adapter = ADAPTERS[platform]
        try:
            loc = adapter.parse(locator)
        except ValueError:
            return 'error'
        label = describe(platform, loc)
        if (platform, loc) in linked_keys:
            return 'error'
        linked_keys.add((platform, loc))
        try:
            page = adapter.fetch(loc, adapter.sorts[0], '')
        except (Stop, TimeoutError):
            raise
        except Exception as exc:  # noqa: BLE001
            if refusal(platform, exc):
                refused.add(platform)
                say('rejected', f'{adapter.label} refuses our reads right now; skipping its other suggestions.')
            elif not quiet:
                say('rejected', f'{label}: could not be read ({type(exc).__name__}: {str(exc)[:80]}).')
            tried.append(f'{label}: unreadable')
            return 'error'
        now = time.time()
        posts = [p for p in page.posts if _fresh_enough(p, now) and not (child and p.get('nsfw'))]
        if len(posts) < MIN_POSTS:
            if not quiet:
                say('rejected', f'{label}: only {len(posts)} recent posts.')
            tried.append(f'{label}: too few recent posts')
            return 'few'
        trials += 1
        say('judging', f'{label}: {len(posts)} recent posts; judging a sample.')
        verdict = judge(posts)
        if verdict is None:
            tried.append(f'{label}: judge unavailable')
            say('rejected', f'{label}: the judge did not answer.')
            return 'error'
        fit, n = verdict
        need = max(2, math.ceil(n * (0.5 if strict else 0.4)))
        if n < 3 or fit < need:
            say('rejected', f'{label}: only {fit} of {n} sample posts fit.')
            tried.append(f'{label}: off-topic ({fit}/{n} fit)')
            return 'misfit'
        sid = save_source(platform, loc, tid, (row or {}).get('title', ''))
        store.upsert_posts(page.posts, source_id=sid)
        link(sid, fit, n)
        known_hosts.append(source_host(platform, loc))
        say('saved', f'Following {label} ({fit}/{n} sample posts fit).')
        tried.append(f'{label}: saved')
        return 'saved'

    # 1. Reuse what other threads' expeditions already found.
    picks = _library_fits(thread, [x for x in library if x.platform in allowed])
    if picks:
        say('library', f'Checking {len(picks)} sources other threads follow.')
    for src in [x for x in library if x.id in picks][:6]:
        if enough():
            break
        attempt(src.platform, src.locator)

    # 2. Sources the model can name directly, and subtopics for keyword searches.
    if not enough():
        named, terms = _named(thread, child, [a for a in platforms if a.discoverable or a.key == 'rss'])
        if terms:
            _save_terms(tid, terms)
            say('topics', 'Search terms: ' + ', '.join(terms[:8]) + ('…' if len(terms) > 8 else ''))
        if named:
            say('platforms', f'Trying {len(named)} named sources: '
                + ', '.join(describe(p, loc) for p, loc in named[:6]) + ('…' if len(named) > 6 else ''))
        for platform, locator in named[:MAX_NAMED]:
            if enough():
                break
            attempt(platform, locator)

    # 3. Rounds of web research, each learning from what failed before.
    ok, reason = providers.PROVIDERS['web_search'].availability()
    if not ok:
        say('done', f'Expedition finished: {saved} new source{"s" if saved != 1 else ""}. Web search is not configured.')
        return saved
    seen_hosts, seen_urls = set(known_hosts), set()
    for round_no in range(MAX_ROUNDS):
        if enough() or time.time() - started > HUNT_DEADLINE_S:
            break
        rows = []
        for q in _queries(thread, child, known_hosts, tried if round_no else None):
            say('searching', f'Searching the web (round {round_no + 1}): {q}')
            try:
                hits = providers.web_search('', q, safe=child)
            except Exception as exc:  # noqa: BLE001
                say('rejected', f'Search failed for "{q}": {type(exc).__name__}.')
                continue
            for hit in hits:
                url = str(hit.get('url') or '')
                host = (urlsplit(url).hostname or '').lower().removeprefix('www.')
                community = known_community(url)
                key = community[1].lower() if community else host
                if not url.startswith('https://') or url in seen_urls or key in seen_hosts:
                    continue
                if not community and (not host or any(h in host for h in SKIP_HOSTS)):
                    continue
                seen_urls.add(url)
                seen_hosts.add(key)
                rows.append({'url': url, 'host': host, 'title': hit.get('title') or '', 'summary': hit.get('summary') or ''})
        if not rows:
            say('found', f'Round {round_no + 1}: no new candidate sites.')
            continue
        ranked = _triage(thread, rows)[:MAX_SITES]
        say('found', f'Round {round_no + 1}: {len(rows)} candidate sites; inspecting the {len(ranked)} most promising.')
        for row in ranked:
            if enough():
                break
            inspect_site(row, thread, say, attempt, tried, browser.available())
    if saved < TARGET_NEW and trials >= MAX_TRIALS:
        say('found', f'Judged {trials} sources this time; run Find sources again to keep looking.')
    say('done', f'Expedition finished: {saved} new source{"s" if saved != 1 else ""} for this thread.')
    return saved


def inspect_site(row, thread, say, attempt, tried, use_browser):
    """Turn one search hit into sources: a recognised community, a Discourse forum, the site's feeds, or a
    scraped listing; the isolated browser when the plain page is blocked or built in JavaScript."""
    community = known_community(row['url'])
    if community:
        attempt(*community, row)
        return
    say('inspecting', 'Inspecting ' + row['host'])
    reason, options = '', []
    query = (re.split(r'[,;\n]+', thread.interests or thread.name)[0].strip() or thread.name)[:150]
    try:
        data, final = site_recipes.get_html(row['url'])
        if _discourse(data):
            options.append(('discourse', f'https://{urlsplit(final).hostname}'))
        options += web_adapter.propose(data, final, query)
    except (Stop, TimeoutError):
        raise
    except Exception as exc:  # noqa: BLE001
        reason = f'blocked plain fetch ({type(exc).__name__})'
    for platform, locator in options[:4]:
        if attempt(platform, locator, row, quiet=True) in ('saved', 'misfit'):
            return  # judged on its posts; another recipe for the same site would not change that
    if not use_browser:
        say('rejected', f'{row["host"]}: {reason or "no usable post listing found"}.')
        tried.append(f'{row["host"]}: {reason or "no post listing"}')
        return
    say('inspecting', f'{row["host"]}: {reason or "no post listing in the plain page"}; opening it in the browser.')
    try:
        data, final = site_recipes.get_html(row['url'], 'browser')
        options = [o for o in web_adapter.propose(data, final, query, via='browser') if o[0] == 'web']
    except (Stop, TimeoutError):
        raise
    except Exception as exc:  # noqa: BLE001
        say('rejected', f'{row["host"]}: the browser could not open it either ({str(exc)[:80]}).')
        tried.append(f'{row["host"]}: unreadable even in a browser')
        return
    for platform, locator in options[:3]:
        if attempt(platform, locator, row, quiet=True) in ('saved', 'misfit'):
            return
    say('rejected', f'{row["host"]}: no usable post listing, even in the browser.')
    tried.append(f'{row["host"]}: no post listing')


def save_source(platform: str, locator: str, tid: int, title: str = '') -> int:
    label = describe(platform, locator) if platform in ('web', 'rss') else locator
    if title and platform in ('web', 'rss'):
        label = title[:80]
    sid = store.ensure_source(platform, locator, label)
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        src = s.get(CRSource, sid)
        if not src.created_by_thread:
            src.created_by_thread = tid
            src.notes = (f'Found by the source expedition — {describe(platform, locator)}'
                         + (f' ("{title[:120]}")' if title else ''))[:500]
        if src.status != 'ok':
            src.status, src.failures, src.next_poll = 'ok', 0, 0
        s.add(src)
        s.commit()
    return sid


def _save_terms(tid: int, terms: list[str]):
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        t = s.get(CRThread, tid)
        if t:
            t.search_terms = terms
            s.add(t)
        s.commit()


# --- Health ------------------------------------------------------------------------------------------

PRUNE_HITS = 60          # an AI-followed source that delivered this many posts and none was kept is unfollowed
PRUNE_AFTER_S = 3 * 86400  # ... once it has been followed this long


def prune_links(tid: int) -> int:
    """Turn off expedition-found links that deliver plenty but never anything this thread keeps."""
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        rows = s.exec(select(CRThreadSource, CRSource).join(CRSource, CRSource.id == CRThreadSource.source_id)
                      .where(CRThreadSource.thread_id == tid, CRThreadSource.origin == 'ai',
                             CRThreadSource.enabled == True, CRThreadSource.kept == 0,  # noqa: E712
                             CRThreadSource.created_at < time.time() - PRUNE_AFTER_S,
                             CRSource.hits >= PRUNE_HITS)).all()
        for link, _ in rows:
            link.enabled = False
            s.add(link)
        s.commit()
        return len(rows)


# --- API helpers ---------------------------------------------------------------------------------------

def hunt_json(hunt):
    if not hunt:
        return None
    return {'id': hunt.id, 'status': hunt.status, 'reason': hunt.reason, 'found': hunt.found, 'error': hunt.error,
            'log': list(hunt.log or [])[-20:], 'created_at': hunt.created_at, 'finished_at': hunt.finished_at}


def latest(s, tid):
    return s.exec(select(CRSourceHunt).where(CRSourceHunt.thread_id == tid)
                  .order_by(CRSourceHunt.id.desc()).limit(1)).first()
