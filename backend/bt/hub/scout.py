"""Bounded, deterministic Source Scout. Network access uses the hardened Hub fetcher."""
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from .. import curation
from . import providers
from .extract import clean

MAX_SITES = 3
MAX_FEEDS = 5


class Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.feeds = []
        self.sitemaps = []
        self.title = ''
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'title':
            self.in_title = True
        if tag != 'link':
            return
        rel = (a.get('rel') or '').lower().split()
        kind = (a.get('type') or '').lower().split(';')[0]
        href = a.get('href') or ''
        if 'alternate' in rel and kind in ('application/rss+xml', 'application/atom+xml', 'application/xml', 'text/xml', 'application/feed+json', 'application/json'):
            self.feeds.append((href, clean(a.get('title'), 100)))
        if 'sitemap' in rel:
            self.sitemaps.append(href)

    def handle_endtag(self, tag):
        if tag == 'title': self.in_title = False

    def handle_data(self, data):
        if self.in_title: self.title += data


def safe_url(base, url):
    try:
        return curation.canonical(urljoin(base, url))
    except (ValueError, TypeError):
        return None


def inspect_site(url, emit):
    """Inspect an HTTPS page or feed and validate advertised feeds before suggesting them."""
    url = curation.canonical(url)
    data, final, meta = curation.fetch(url, kinds=curation.HTML_TYPES + curation.FEED_TYPES,
                                       limit=2_000_000, with_metadata=True)
    emit('inspected', 'Inspected a public site.')
    if meta['content_type'] not in curation.HTML_TYPES:
        try:
            items = providers.parse_feed(data, final)
            return [suggestion(final, '', items)], []
        except ValueError:
            items = providers.sitemap(final)
            return [{**suggestion(final, '', items), 'provider': 'sitemap'}], [final]
    parser = Links()
    parser.feed(data.decode('utf-8', errors='replace'))
    candidates = list(parser.feeds)
    # Parse simple HTTP Link headers; all targets are revalidated by fetch().
    for part in re.split(r',(?=\s*<)', meta.get('link', '')):
        target = re.search(r'<([^>]+)>', part)
        if target and re.search(r'rel\s*=\s*["\']?alternate', part, re.I) and re.search(r'(rss|atom|xml|feed\+json)', part, re.I):
            candidates.append((target.group(1), ''))
    # Common paths are attempted only if no advertised feed is present.
    if not candidates:
        candidates = [('/feed', ''), ('/feed.xml', ''), ('/rss.xml', '')]
    found, seen = [], set()
    for href, title in candidates:
        candidate = safe_url(final, href)
        if not candidate or candidate in seen: continue
        seen.add(candidate)
        if len(seen) > MAX_FEEDS: break
        try:
            payload, resolved, _ = curation.fetch(candidate, with_metadata=True)
            items = providers.parse_feed(payload, resolved)
            found.append(suggestion(resolved, title or clean(parser.title, 100), items))
            emit('validated', f'Validated a feed with {len(items)} sample items.')
        except Exception:
            emit('rejected', 'A candidate feed could not be validated.')
    if not found:
        items = providers.parse_listing(data, final)
        if items:
            found.append({**suggestion(final, clean(parser.title, 100), items), 'provider': 'html_listing'})
            emit('validated', f'Tested an article listing with {len(items)} sample links.')
    sitemap_refs = list(parser.sitemaps)
    try:
        robots_url = urljoin(final, '/robots.txt')
        robots = curation.fetch(robots_url, kinds=('text/plain',), limit=128_000).decode('utf-8', errors='replace')
        sitemap_refs.extend(line.split(':', 1)[1].strip() for line in robots.splitlines()
                            if line.lower().startswith('sitemap:'))
    except Exception:
        pass  # Robots is optional; a missing or inaccessible file is not a failed run.
    sitemaps = list(dict.fromkeys(u for href in sitemap_refs if (u := safe_url(final, href))))[:5]
    for candidate in sitemaps:
        try:
            items = providers.sitemap(candidate)
            found.append({**suggestion(candidate, clean(parser.title, 100), items), 'provider': 'sitemap'})
            emit('validated', f'Tested a sitemap with {len(items)} sample URLs.')
        except Exception:
            emit('rejected', 'A candidate sitemap could not be validated.')
    if not found:
        item = providers.extract_page(data, final)
        if item['body']:
            found.append({**suggestion(final, clean(parser.title, 100), [item]), 'provider': 'page_watch'})
            emit('validated', 'Extracted readable page text for change monitoring.')
    return found, sitemaps


def suggestion(url, title, items):
    return {'provider': 'rss', 'url': url, 'name': title or urlsplit(url).hostname or 'Feed',
            'count': len(items), 'sample': [{'title': x.get('title', ''), 'url': x.get('url', '')} for x in items[:5]],
            'validated': True}


def research(query, emit):
    """Return validated feed suggestions; search results and page markup are untrusted."""
    if query.lower().startswith('https://'):
        sites = [query]
    else:
        ok, reason = providers.PROVIDERS['web_search'].availability()
        if not ok: raise ValueError(reason)
        emit('searching', 'Searching for relevant publishers.')
        rows = providers.web_search('', query)
        sites = []
        for row in rows:
            u = safe_url('', row.get('url', ''))
            if u and u not in sites: sites.append(u)
            if len(sites) >= MAX_SITES: break
        emit('found', f'Found {len(sites)} candidate sites.')
    suggestions, sitemap_urls, seen = [], [], set()
    for site in sites[:MAX_SITES]:
        try:
            feeds, sitemaps = inspect_site(site, emit)
            for f in feeds:
                if f['url'] not in seen:
                    seen.add(f['url']); suggestions.append(f)
            sitemap_urls.extend(x for x in sitemaps if x not in sitemap_urls)
        except Exception:
            emit('failed', 'Could not inspect a candidate site.')
    return suggestions[:15], sitemap_urls[:10]



def run_due(limit: int = 4) -> int:
    """Claim up to `limit` due searches and start them in the background; returns how many started."""
    import threading
    import time
    from sqlalchemy import update
    from sqlmodel import select
    from ..access import hub_allowed
    from ..db import session
    from .models import ScoutPlan

    now = time.time()
    with session() as s:
        plans = s.exec(select(ScoutPlan).where(ScoutPlan.enabled == True, ScoutPlan.next_run <= now)
                       .order_by(ScoutPlan.next_run).limit(max(0, min(4, limit)))).all()
    started = 0
    for plan in plans:
        with session() as s:
            if not hub_allowed(s, plan.profile_id):
                row = s.get(ScoutPlan, plan.id)
                if row: row.enabled = False; s.add(row); s.commit()
                continue
            due = now + plan.interval_hours * 3600
            claimed = s.exec(update(ScoutPlan).where(ScoutPlan.id == plan.id, ScoutPlan.enabled == True,
                                                      ScoutPlan.next_run <= now).values(next_run=due))
            s.commit()
            if not claimed.rowcount: continue
        def run_plan(pid=plan.profile_id, query=plan.query, plan_id=plan.id, scheduled=due):
            from fastapi import HTTPException
            from .api import ScoutInput, research_run
            try:
                with session() as s:
                    if not hub_allowed(s, pid): return
                research_run(ScoutInput(query=query), pid, scheduled=True)
            except HTTPException as exc:
                if exc.status_code in (409, 429):
                    with session() as s:
                        s.exec(update(ScoutPlan).where(ScoutPlan.id == plan_id, ScoutPlan.enabled == True,
                                                       ScoutPlan.next_run == scheduled).values(next_run=time.time() + 300))
                        s.commit()
                else:
                    from .models import ScoutRun
                    with session() as s:
                        if hub_allowed(s, pid):
                            s.add(ScoutRun(profile_id=pid, query=query, status='failed', finished_at=time.time(),
                                           error=str(exc.detail)[:300]))
                            s.commit()
            except Exception:
                from .models import ScoutRun
                with session() as s:
                    if hub_allowed(s, pid):
                        s.add(ScoutRun(profile_id=pid, query=query, status='failed', finished_at=time.time(),
                                       error='Scheduled research could not be completed.'))
                        s.commit()
        threading.Thread(target=run_plan, daemon=True, name=f'scout-plan-{plan.id}').start()
        started += 1
    return started
