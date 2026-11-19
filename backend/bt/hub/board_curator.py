"""Board source curator: researches the web for the best places to find a board's kind of images and saves
each one as a declarative recipe (board_recipes) in the shared site library, linked to the board.

Runs as a durable MoodSourceHunt job: on board creation and from the board's "Find sources" button.
Search results and page markup are untrusted; the model only plans searches and triages hits, recipes are
derived by fixed heuristics, and a recipe is kept only after it returns images the vision judge accepts.
"""
import asyncio
import hashlib
import json
import logging
import re
import secrets
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, update
from sqlmodel import select

from ..access import boards_allowed, require_boards
from ..db import session
from . import ai, board_recipes, moodboard_sources
from .models import MoodBoard, MoodBoardSource, MoodSite, MoodSourceHunt

log = logging.getLogger(__name__)
router = APIRouter(prefix='/hub/boards', tags=['Mood boards'])

HUNT_DEADLINE_S = 1200
LEASE_S = 900  # renewed by every progress note
MAX_ROUNDS = 3  # web research rounds; later rounds learn from earlier failures
MAX_SITES = 10  # pages inspected per round
MAX_TRIALS = 18  # recipes given a vision trial per hunt
TARGET_NEW = 5  # stop once this many new sources fit
TRIAL_IMAGES = 5
MIN_IMAGES = 6  # a recipe must return at least this many usable candidates
LOG_LIMIT = 60
BROKEN_AFTER = 5  # consecutive failed fetches before a recipe is marked broken
# Sites that cannot be scraped politely or need sign-in; their images arrive through other routes.
SKIP_HOSTS = ('pinterest.', 'facebook.com', 'instagram.com', 'x.com', 'twitter.com', 'tiktok.com', 'youtube.com',
              'youtu.be', 'linkedin.com', 'amazon.', 'ebay.', 'etsy.com', 'wikipedia.org', 'quora.com')


class Stop(Exception):
    pass


# --- Job bookkeeping -----------------------------------------------------------------------------------

def active(s, bid):
    return s.exec(select(MoodSourceHunt).where(MoodSourceHunt.board_id == bid,
                                               MoodSourceHunt.status.in_(['queued', 'running']))).first()


def enqueue(s, board) -> MoodSourceHunt | None:
    """Queue a hunt unless one is waiting or running for this board. Caller commits."""
    if active(s, board.id):
        return None
    hunt = MoodSourceHunt(board_id=board.id, profile_id=board.profile_id,
                          log=[{'at': time.time(), 'step': 'queued', 'message': 'Source research queued.'}])
    s.add(hunt)
    return hunt


def note(hid, lease, step, message):
    with session() as s:
        hunt = s.get(MoodSourceHunt, hid)
        if not hunt or hunt.lease != lease:
            raise Stop()
        hunt.log = (list(hunt.log or []) + [{'at': time.time(), 'step': step, 'message': message[:300]}])[-LOG_LIMIT:]
        hunt.lease_until = time.time() + LEASE_S
        s.add(hunt)
        s.commit()


def finish(hid, lease, status, found, error=''):
    with session() as s:
        hunt = s.get(MoodSourceHunt, hid)
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
        # Stale leases (crash, restart) go back to the queue.
        s.exec(update(MoodSourceHunt).where(MoodSourceHunt.status == 'running', MoodSourceHunt.lease_until < now)
               .values(status='queued', lease=''))
        hunt = s.exec(select(MoodSourceHunt).where(MoodSourceHunt.status == 'queued')
                      .order_by(MoodSourceHunt.id).limit(1)).first()
        if not hunt:
            s.commit()
            return False
        board = s.get(MoodBoard, hunt.board_id)
        if not board or not boards_allowed(s, board.profile_id):
            hunt.status, hunt.error, hunt.finished_at = 'failed', 'Board no longer available.', now
            s.add(hunt)
            s.commit()
            return True
        lease = secrets.token_hex(16)
        hunt.status, hunt.lease, hunt.lease_until, hunt.started_at = 'running', lease, now + LEASE_S, now
        s.add(hunt)
        s.commit()
        hid, board_id = hunt.id, hunt.board_id
    try:
        found = hunt_sources(hid, lease)
        finish(hid, lease, 'complete', found)
    except Stop:
        pass
    except TimeoutError:
        with session() as s:  # Out of time: keep what was linked so far.
            found = len(s.exec(select(MoodBoardSource.id).where(MoodBoardSource.board_id == board_id,
                                                                MoodBoardSource.created_at >= now)).all())
        try:
            note(hid, lease, 'done', f'Time is up; {found} new sources linked. Run Find sources again for more.')
        except Stop:
            return True
        finish(hid, lease, 'complete', found)
    except Exception as exc:  # noqa: BLE001 — any failure ends this hunt, never the scheduler
        log.warning('Board source hunt %s failed: %s: %s', hid, type(exc).__name__, str(exc)[:300])
        message = str(exc)[:300] if isinstance(exc, ValueError) else 'Source research failed.'
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
            log.exception('Board source curator tick failed')
            worked = False
        await asyncio.sleep(2 if worked else 15)


# --- The research itself -------------------------------------------------------------------------------

def _queries(context, instructions, known_hosts, child, tried=None):
    """Web-search queries for sources. Later rounds see what was tried and why it failed, and change tack."""
    schema = {'type': 'object', 'properties': {'queries': {'type': 'array', 'minItems': 1, 'maxItems': 6,
              'items': {'type': 'string', 'maxLength': 120}}}, 'required': ['queries'], 'additionalProperties': False}
    history = ('\nAlready tried (do not repeat these sites or angles; learn from why they failed):\n'
               + '\n'.join(tried[-30:])) if tried else ''
    try:
        out = ai._call(
            'You research where on the web a visual mood board can keep finding great images. Write up to six '
            'varied web-search queries that find SOURCES rather than single pictures: photo galleries, blogs and '
            'magazines with many photos, open-access museum or archive collections, photographer portfolios, '
            'communities (subreddits, forums), GIF and image platforms, and image-rich sites with their own search. '
            'Vary the angle: archive, community, magazine, portfolio, collection, platform. '
            + ('When earlier attempts failed, try clearly different kinds of sites and more specific niche terms. '
               if tried else '')
            + ('This board is for a young child: only family-friendly, educational and museum sources. ' if child else '') +
            'Return queries only.',
            '<data>Board: ' + ai._esc(context) + '\nInstructions: ' + ai._esc(instructions[:800]) +
            '\nAlready used sites: ' + ai._esc(', '.join(known_hosts[:20]) or 'none') + ai._esc(history) + '</data>',
            schema, max_tokens=300, role='board_sources', timeout_s=120)
        phrases = [q.strip()[:120] for q in out.get('queries', []) if isinstance(q, str) and q.strip()]
        if phrases:
            return list(dict.fromkeys(phrases))[:6]
    except Exception as exc:  # noqa: BLE001 — fall back to fixed angles
        log.info('Board source query planning failed: %s', str(exc)[:200])
    topic = re.split(r'[,;\n]+', context.split(' — ', 1)[-1])[0].strip()[:80] or context[:80]
    base = ([f'{topic} photo gallery', f'best {topic} photography blog', f'{topic} image archive collection']
            if not tried else [f'{topic} images collection site', f'{topic} community gallery', f'free {topic} pictures'])
    return base + ([] if child else [f'{topic} subreddit'])


def _platform_ideas(context, instructions, child, networks):
    """Sources the model can name without a web search: subreddits, Commons categories, platform searches."""
    schema = {'type': 'object', 'properties': {
        'subreddits': {'type': 'array', 'maxItems': 8, 'items': {'type': 'string', 'maxLength': 40}},
        'commons_categories': {'type': 'array', 'maxItems': 5, 'items': {'type': 'string', 'maxLength': 120}},
        'platform_queries': {'type': 'array', 'maxItems': 4, 'items': {'type': 'string', 'maxLength': 80}}},
        'required': ['subreddits', 'commons_categories', 'platform_queries'], 'additionalProperties': False}
    try:
        out = ai._call(
            'Name places that keep publishing images for this mood board. subreddits: existing, active subreddit '
            'names (without r/) whose posts are mostly on-topic images or GIFs' + (' (leave empty: child board)' if child else '') +
            '. commons_categories: existing Wikimedia Commons category names with many on-topic files. '
            'platform_queries: short search phrases that work well on image and GIF platforms (' +
            (', '.join(networks) or 'none') + '). Only name things you are confident exist.',
            '<data>Board: ' + ai._esc(context) + '\nInstructions: ' + ai._esc(instructions[:800]) + '</data>',
            schema, max_tokens=400, role='board_sources', timeout_s=120)
    except Exception as exc:  # noqa: BLE001
        log.info('Board source platform ideas failed: %s', str(exc)[:200])
        out = {}
    clean = lambda key, n: [str(v).strip() for v in (out.get(key) or [])[:n] if isinstance(v, str) and str(v).strip()]
    return clean('subreddits', 8), clean('commons_categories', 5), clean('platform_queries', 4)


def _triage(context, rows):
    """Order search hits by how likely each is a lasting, image-rich source. Falls back to search order."""
    if len(rows) <= 3:
        return rows
    schema = {'type': 'object', 'properties': {'pick': {'type': 'array', 'maxItems': MAX_SITES,
              'items': {'type': 'integer', 'minimum': 0, 'maximum': len(rows) - 1}}},
              'required': ['pick'], 'additionalProperties': False}
    listing = [{'n': i, 'site': r['host'], 'title': r['title'][:150], 'snippet': r['summary'][:200]} for i, r in enumerate(rows)]
    try:
        out = ai._call(
            'Pick the search results most likely to be lasting sources of many on-topic images for this board: '
            'galleries, collections, image-heavy blogs or communities, or sites with a searchable image catalogue. '
            'Skip single news articles, shops, listicles that only embed other sites, and off-topic results. '
            'Return the chosen n values, best first.',
            '<data>Board: ' + ai._esc(context) + '\n' + ai._esc(json.dumps(listing, ensure_ascii=False)) + '</data>',
            schema, max_tokens=200, role='board_sources', timeout_s=120)
        pick = [i for i in dict.fromkeys(out.get('pick') or []) if isinstance(i, int) and 0 <= i < len(rows)]
        if pick:
            return [rows[i] for i in pick]
    except Exception as exc:  # noqa: BLE001
        log.info('Board source triage failed: %s', str(exc)[:200])
    return rows


def _library_fits(context, sites):
    """Shared recipes another board's curator already made that suit this board too."""
    if not sites:
        return []
    schema = {'type': 'object', 'properties': {'pick': {'type': 'array', 'maxItems': 6,
              'items': {'type': 'integer'}}}, 'required': ['pick'], 'additionalProperties': False}
    listing = [{'id': x.id, 'name': x.name, 'kind': x.kind, 'notes': x.notes[:200]} for x in sites[:40]]
    try:
        out = ai._call('These image sources were found for other boards. Pick the ids of the ones likely to have '
                       'many images for this board too; a site search fits any topic the site covers. Return ids only.',
                       '<data>Board: ' + ai._esc(context) + '\n' + ai._esc(json.dumps(listing, ensure_ascii=False)) + '</data>',
                       schema, max_tokens=150, role='board_sources', timeout_s=120)
        ids = {x.id for x in sites}
        return [i for i in dict.fromkeys(out.get('pick') or []) if i in ids]
    except Exception:  # noqa: BLE001
        return []


def _skip_host(host):
    return not host or any(h in host for h in SKIP_HOSTS)


def recipe_key(kind, recipe):
    blob = json.dumps(recipe, sort_keys=True)
    return board_recipes.host_of(kind, recipe) + ':' + kind + ':' + hashlib.sha256(blob.encode()).hexdigest()[:16]


def hunt_sources(hid, lease) -> int:
    """Library first, then sources the model can name directly, then rounds of web research that learn
    from each failure, until enough sources fit or time runs out."""
    from . import browser, moodboards as mb
    started = time.time()

    def say(step, message):
        note(hid, lease, step, message)
        if time.time() - started > HUNT_DEADLINE_S and step not in ('saved', 'done'):
            raise TimeoutError('Source research ran out of time.')

    with session() as s:
        hunt = s.get(MoodSourceHunt, hid)
        board = s.get(MoodBoard, hunt.board_id)
        bid = board.id
        context = board.name + ' — ' + board.interests
        instructions = (board.instructions + mb.board_rules(board) + mb.feedback_lessons(s, bid)).strip()
        child = bool(board.child_friendly)
        strict = child or board.strictness != 'balanced'
        media, min_width = board.media, board.min_width
        ai_mode = board.ai_images if board.ai_images in mb.ai_detect.MODES else 'reject'
        interests = [v.strip()[:150] for v in re.split(r'[,;\n]+', board.interests) if v.strip()]
        linked = s.exec(select(MoodBoardSource.site_id).where(MoodBoardSource.board_id == bid)).all()
        linked_sites = [x for x in (s.get(MoodSite, i) for i in linked) if x]
        known_hosts = sorted({x.host for x in linked_sites})
        linked_keys = {x.recipe_key for x in linked_sites}
        library = s.exec(select(MoodSite).where(MoodSite.status == 'ok', MoodSite.id.not_in(linked or [0]))
                         .order_by(MoodSite.created_at.desc()).limit(40)).all()
    if not mb.ai.enabled():
        raise ValueError('Source research needs the AI model to be configured.')
    mb.select_candidates([], context, instructions)  # Fail early when vision is unavailable.
    query = interests[0] if interests else board.name
    kinds = board_recipes.KID_KINDS if child else board_recipes.KINDS
    networks = [n for n in board_recipes.NETWORKS if board_recipes.network_available(n, child)]
    saved, trials, tried = 0, 0, []

    def enough():
        return saved >= TARGET_NEW or trials >= MAX_TRIALS

    def usable(kind, recipe):
        return [i for i in board_recipes.fetch(kind, recipe, query, limit=30, child=child)
                if all(moodboard_sources.valid_candidate(i))]

    def link(site_id, fit, n, params):
        nonlocal saved
        with session() as s:
            s.connection().exec_driver_sql('BEGIN IMMEDIATE')
            current = s.get(MoodSourceHunt, hid)
            if not current or current.lease != lease or not s.get(MoodBoard, bid):
                raise Stop()
            if not s.exec(select(MoodBoardSource).where(MoodBoardSource.board_id == bid,
                                                        MoodBoardSource.site_id == site_id)).first():
                s.add(MoodBoardSource(board_id=bid, site_id=site_id, params=params, fit=fit, tried=n))
                saved += 1
            s.commit()

    def attempt(kind, recipe, row, site=None, quiet_empty=False) -> str:
        """Fetch, sample-judge and save one recipe. Returns 'saved', 'few', 'misfit' or 'error'."""
        nonlocal trials
        label = site.name if site else board_recipes.describe(kind, recipe)
        if not site and recipe_key(kind, recipe) in linked_keys:
            return 'error'
        try:
            items = usable(kind, recipe)
        except (Stop, TimeoutError):
            raise
        except Exception as exc:  # noqa: BLE001
            if not quiet_empty:
                say('rejected', f'{label}: could not be read ({type(exc).__name__}).')
            tried.append(f'{label}: unreadable')
            return 'error'
        if len(items) < MIN_IMAGES:
            if not quiet_empty:
                say('rejected', f'{label}: only {len(items)} usable images.')
            tried.append(f'{label}: too few images')
            return 'few'
        trials += 1
        captured, _ = mb.capture_candidates(items[:TRIAL_IMAGES + 3], media, min_width, ai_mode)
        captured = captured[:TRIAL_IMAGES]
        if len(captured) < 3:
            say('rejected', f'{label}: {len(items)} images found but they could not be downloaded.')
            tried.append(f'{label}: images not downloadable')
            return 'error'
        say('judging', f'{label}: {len(items)} images found; vision is checking a sample.')
        extra = {'ai_mode': ai_mode} if ai_mode != 'reject' else {}
        fit = len(mb.select_candidates(captured, context, instructions, strict=strict, **extra))
        n = len(captured)
        if fit < (3 if strict else 2):
            say('rejected', f'{label}: only {fit} of {n} sample images fit the board.')
            tried.append(f'{label}: off-topic ({fit}/{n} fit)')
            return 'misfit'
        site_id = site.id if site else save_site(kind, recipe, row, bid)
        link(site_id, fit, n, {'query': query})
        say('saved', f'Saved {label} ({fit}/{n} sample images fit).')
        tried.append(f'{label}: saved')
        return 'saved'

    # 1. Reuse what other boards' curators already learned.
    picks = _library_fits(context, [x for x in library if x.kind in kinds])
    if picks:
        say('library', f'Checking {len(picks)} sources from the shared library.')
    for site in [x for x in library if x.id in picks][:4]:
        if enough():
            break
        attempt(site.kind, site.recipe, {}, site=site)
        known_hosts.append(site.host)

    # 2. Sources the model can name directly: subreddits, Commons categories and image/GIF platforms.
    if not enough():
        subs, cats, platform_queries = _platform_ideas(context, instructions, child, networks)
        ideas = []
        for q in (platform_queries or [query])[:2]:
            ideas += [('network_search', {'network': n, 'query': q}) for n in networks]
        ideas += [('subreddit', {'name': x}) for x in subs] if 'subreddit' in kinds else []
        ideas += [('commons_category', {'name': x}) for x in cats]
        ideas = [(k, board_recipes.validate(k, r)) for k, r in ideas if _valid(k, r)]
        if ideas:
            names = ', '.join(board_recipes.describe(k, r) for k, r in ideas[:6])
            say('platforms', f'Trying {len(ideas)} platform sources: {names}' + ('…' if len(ideas) > 6 else ''))
        done_networks = set()
        for kind, recipe in ideas:
            if enough():
                break
            if kind == 'network_search' and recipe['network'] in done_networks:
                continue  # one good search per platform is plenty; the board run varies the words
            result = attempt(kind, recipe, {'title': board_recipes.describe(kind, recipe), 'host': board_recipes.host_of(kind, recipe)})
            if kind == 'network_search' and result == 'saved':
                done_networks.add(recipe['network'])

    # 3. Rounds of web research. Each round sees what failed before and changes tack.
    ok, reason = moodboard_sources_web_ok()
    if not ok:
        if saved:
            say('done', f'Source research finished: {saved} new source{"s" if saved != 1 else ""}. Web search is not configured.')
            return saved
        raise ValueError(reason)
    seen_hosts, seen_urls = set(known_hosts), set()
    for round_no in range(MAX_ROUNDS):
        if enough() or time.time() - started > HUNT_DEADLINE_S:
            break
        queries = _queries(context, instructions, known_hosts, child, tried if round_no else None)
        rows = []
        for q in queries:
            say('searching', f'Searching the web (round {round_no + 1}): {q}')
            try:
                hits = providers_web_search(q, safe=child)
            except Exception as exc:  # noqa: BLE001
                say('rejected', f'Search failed for "{q}": {type(exc).__name__}.')
                continue
            for hit in hits:
                url = str(hit.get('url') or '')
                host = (urlsplit(url).hostname or '').lower().removeprefix('www.')
                network = board_recipes.known_network(url)
                key = network[1].get('name', '').lower() if network else host
                if not url.startswith('https://') or url in seen_urls or (not network and (_skip_host(host) or host in seen_hosts)):
                    continue
                if network and (network[0] not in kinds or key in seen_hosts):
                    continue
                seen_urls.add(url)
                seen_hosts.add(key)
                rows.append({'url': url, 'host': host, 'title': hit.get('title') or '', 'summary': hit.get('summary') or ''})
        if not rows:
            say('found', f'Round {round_no + 1}: no new candidate sites.')
            continue
        ranked = _triage(context, rows)[:MAX_SITES]
        say('found', f'Round {round_no + 1}: {len(rows)} candidate sites; inspecting the {len(ranked)} most promising.')
        for row in ranked:
            if enough():
                break
            inspect_site(row, kinds, query, say, attempt, tried, browser.available())
    if saved < TARGET_NEW and trials >= MAX_TRIALS:
        say('found', f'Tried {trials} sources this time; run Find sources again to keep looking.')
    say('done', f'Source research finished: {saved} new source{"s" if saved != 1 else ""} for this board.')
    return saved


def _valid(kind, recipe):
    try:
        board_recipes.validate(kind, recipe)
        return True
    except ValueError:
        return False


def inspect_site(row, kinds, query, say, attempt, tried, use_browser):
    """Derive recipes from one search hit: plain fetch first, the isolated browser when the site blocks
    plain fetches or builds its gallery in JavaScript."""
    network = board_recipes.known_network(row['url'])
    if network:
        if network[0] in kinds:
            attempt(*network, row)
        return
    say('inspecting', 'Inspecting ' + row['host'])
    reason = ''
    try:
        data, final = board_recipes.get_html(row['url'])
        options = [o for o in board_recipes.propose(data, final, query) if o[0] in kinds]
    except (Stop, TimeoutError):
        raise
    except Exception as exc:  # noqa: BLE001
        options, reason = [], f'blocked plain fetch ({type(exc).__name__})'
    for kind, recipe in options[:3]:
        result = attempt(kind, recipe, row, quiet_empty=True)
        if result in ('saved', 'misfit'):
            return  # the site was judged on its pictures; another recipe for it would not change that
    if not use_browser:
        say('rejected', f'{row["host"]}: {reason or "no usable image listing found"}.')
        tried.append(f'{row["host"]}: {reason or "no image listing"}')
        return
    say('inspecting', f'{row["host"]}: {reason or "no image listing in the plain page"}; opening it in the browser.')
    try:
        data, final = board_recipes.get_html(row['url'], 'browser')
        options = [o for o in board_recipes.propose(data, final, query, via='browser') if o[0] in kinds and o[0] != 'feed']
    except (Stop, TimeoutError):
        raise
    except Exception as exc:  # noqa: BLE001
        say('rejected', f'{row["host"]}: the browser could not open it either ({str(exc)[:80]}).')
        tried.append(f'{row["host"]}: unreadable even in a browser')
        return
    for kind, recipe in options[:3]:
        if attempt(kind, recipe, row, quiet_empty=True) in ('saved', 'misfit'):
            return
    say('rejected', f'{row["host"]}: no usable image listing, even in the browser.')
    tried.append(f'{row["host"]}: no image listing')


def save_site(kind, recipe, row, bid):
    key = recipe_key(kind, recipe)
    with session() as s:
        site = s.exec(select(MoodSite).where(MoodSite.recipe_key == key)).first()
        if not site:
            site = MoodSite(host=board_recipes.host_of(kind, recipe), kind=kind, recipe=recipe, recipe_key=key,
                            name=(board_recipes.describe(kind, recipe) if kind == 'subreddit' else
                                  (row.get('title') or row['host'])[:80]),
                            notes=('Found for "' + row.get('title', '')[:120] + '" — ' + board_recipes.describe(kind, recipe))[:500],
                            created_by_board=bid, last_ok=time.time())
        elif site.status == 'broken':
            site.status, site.failures = 'ok', 0
        s.add(site)
        s.commit()
        return site.id


def moodboard_sources_web_ok():
    from . import providers
    return providers.PROVIDERS['web_search'].availability()


def providers_web_search(q, safe=False):
    from . import providers
    return providers.web_search('', q, safe=safe)


# --- Board runs: fetch from a board's linked sources ---------------------------------------------------

def board_sources(s, bid, child):
    rows = s.exec(select(MoodBoardSource, MoodSite).where(MoodBoardSource.board_id == bid,
                  MoodBoardSource.enabled == True, MoodBoardSource.site_id == MoodSite.id,  # noqa: E712
                  MoodSite.status == 'ok')).all()
    kinds = board_recipes.KID_KINDS if child else board_recipes.KINDS
    rows = [(link, site) for link, site in rows if site.kind != 'network_search' or not child
            or site.recipe.get('network') in board_recipes.KID_NETWORKS]
    return [{'link_id': link.id, 'site_id': site.id, 'kind': site.kind, 'recipe': dict(site.recipe),
             'params': dict(link.params or {}), 'name': site.name} for link, site in rows if site.kind in kinds]


def record_fetch(site_id, ok):
    """Track recipe health; a recipe failing BROKEN_AFTER times in a row is parked as broken."""
    with session() as s:
        site = s.get(MoodSite, site_id)
        if not site:
            return
        if ok:
            site.failures, site.last_ok = 0, time.time()
        else:
            site.failures += 1
            if site.failures >= BROKEN_AFTER:
                site.status = 'broken'
        s.add(site)
        s.commit()


def record_use(counts: dict, kept: dict):
    """Per board link: candidates delivered and pins kept by one board run."""
    if not counts:
        return
    with session() as s:
        for link_id, n in counts.items():
            link = s.get(MoodBoardSource, link_id)
            if link:
                link.hits += n
                link.kept += kept.get(link_id, 0)
                link.last_run = time.time()
                s.add(link)
        s.commit()


# --- API ---------------------------------------------------------------------------------------------

def _board(s, pid, bid, manage=False):
    from .moodboards import owned
    return owned(s, pid, bid, manage=manage)


def hunt_json(hunt):
    if not hunt:
        return None
    return {'id': hunt.id, 'status': hunt.status, 'found': hunt.found, 'error': hunt.error,
            'log': list(hunt.log or [])[-20:], 'created_at': hunt.created_at, 'finished_at': hunt.finished_at}


def sources_json(s, bid):
    rows = s.exec(select(MoodBoardSource, MoodSite).where(MoodBoardSource.board_id == bid,
                  MoodBoardSource.site_id == MoodSite.id).order_by(MoodBoardSource.created_at)).all()
    hunt = s.exec(select(MoodSourceHunt).where(MoodSourceHunt.board_id == bid)
                  .order_by(MoodSourceHunt.id.desc()).limit(1)).first()
    return {'sources': [{'id': link.id, 'name': site.name, 'host': site.host, 'kind': site.kind,
                         'status': site.status, 'enabled': link.enabled, 'added_by': link.added_by,
                         'fit': link.fit, 'tried': link.tried, 'hits': link.hits, 'kept': link.kept,
                         'last_run': link.last_run, 'created_at': link.created_at,
                         'url': (site.recipe.get('url') or site.recipe.get('url_template', '').split('?')[0]
                                 or ('https://www.reddit.com/r/' + site.recipe.get('name', '') if site.kind == 'subreddit' else ''))}
                        for link, site in rows],
            'hunt': hunt_json(hunt)}


@router.get('/{bid}/sources')
def list_sources(bid: int, pid: int = Depends(require_boards)):
    with session() as s:
        _board(s, pid, bid)
        return sources_json(s, bid)


@router.post('/{bid}/find-sources')
def find_sources(bid: int, pid: int = Depends(require_boards)):
    with session() as s:
        s.connection().exec_driver_sql('BEGIN IMMEDIATE')
        board = _board(s, pid, bid)
        if active(s, bid):
            raise HTTPException(409, 'Source research is already running for this board.')
        enqueue(s, board)
        s.commit()
        return sources_json(s, bid)


class SourceEdit(BaseModel):
    enabled: bool


@router.patch('/{bid}/sources/{link_id}')
def edit_source(bid: int, link_id: int, data: SourceEdit, pid: int = Depends(require_boards)):
    with session() as s:
        _board(s, pid, bid)
        link = s.get(MoodBoardSource, link_id)
        if not link or link.board_id != bid:
            raise HTTPException(404, 'Source not found')
        link.enabled = data.enabled
        s.add(link)
        s.commit()
        return sources_json(s, bid)


@router.delete('/{bid}/sources/{link_id}')
def remove_source(bid: int, link_id: int, pid: int = Depends(require_boards)):
    with session() as s:
        _board(s, pid, bid)
        link = s.get(MoodBoardSource, link_id)
        if not link or link.board_id != bid:
            raise HTTPException(404, 'Source not found')
        s.delete(link)
        s.commit()
        return sources_json(s, bid)


def delete_board(s, bids):
    """Inside the board deletion transaction. Shared site recipes stay in the library."""
    s.exec(delete(MoodBoardSource).where(MoodBoardSource.board_id.in_(bids)))
    s.exec(delete(MoodSourceHunt).where(MoodSourceHunt.board_id.in_(bids)))
