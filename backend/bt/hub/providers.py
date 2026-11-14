"""Provider registry. Each adapter turns a Source (+ curator queries) into normalized item dicts.

Feed providers read a URL and ignore queries; query providers run each query. All public
fetches go through curation.fetch (HTTPS, public IPs only, bounded). The configured SearXNG
endpoint is admin-trusted configuration and may live on the LAN."""
import json
import re
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Callable
from html import unescape as unescape_html
from urllib.parse import quote, unquote, urlencode, urljoin, urlsplit


import httpx

from .. import curation
from ..db import get_settings
from . import watchers
from .extract import CVE_RE, TEXT_LIMIT, classify, clean, drop_links, extract_page, safe_image, strip_tags, ts

TYPES = [
    ('video', 'Videos', '▶'), ('news', 'News', '📰'), ('article', 'Articles', '📄'), ('web', 'Web resources', '🌐'),
    ('recipe', 'Recipes', '🍳'), ('pattern', 'Craft patterns', '🧶'), ('advisory', 'Security advisories', '🛡'),
    ('project', 'Software projects', '📦'), ('document', 'Documents & papers', '📚'),
    ('person', 'People', '👤'), ('organization', 'Organizations', '🏢'), ('domain', 'Domains', '🔗'),
    ('meme', 'Memes', '😂'),
]
TYPE_KEYS = {t[0] for t in TYPES}


@dataclass
class Provider:
    key: str
    label: str
    mode: str  # feed | query | url
    description: str
    fetch: Callable[[str, str], list[dict]]  # (source url, query) -> items
    capabilities: list = field(default_factory=list)
    needs_url: bool = False
    default_type: str = 'web'
    configurable: bool = False

    def availability(self) -> tuple[bool, str]:
        if self.key == 'web_search':
            cfg = get_settings()['search']
            if cfg['provider'] == 'searxng' and cfg['searxng_url']:
                return True, ''
            if cfg['provider'] == 'brave' and cfg['brave_api_key']:
                return True, ''
            return False, 'Configure SearXNG or Brave under Settings → Search & sources.'
        return True, ''

    def run(self, url: str, q: str = '', config: dict | None = None) -> list[dict]:
        """Fetch with the source's watcher rules when this provider supports them."""
        return self.fetch(url, q, config or {}) if self.configurable or self.key in watchers.CONFIGURABLE else self.fetch(url, q)

    def info(self) -> dict:
        ok, reason = self.availability()
        return {'key': self.key, 'label': self.label, 'mode': self.mode, 'needs_url': self.needs_url,
                'capabilities': self.capabilities, 'available': ok, 'reason': reason, 'description': self.description}


def _xml(data: bytes):
    if b'<!DOCTYPE' in data[:2000].upper() or b'<!ENTITY' in data.upper():
        raise ValueError('XML entities are not allowed.')
    return ET.fromstring(data)


def _local(tag: str) -> str:
    return tag.split('}')[-1]


_IMG_TAG = re.compile(r'<img\b([^>]*)>', re.I)
_IMG_SRC = re.compile(r'\bsrc\s*=\s*["\']([^"\']+)["\']', re.I)
_TINY_IMG = re.compile(r'\b(?:width|height)\s*=\s*["\']?1["\']?(?!\d)', re.I)


def _html_image(html: str, base: str) -> str:
    """First real <img> in a feed's HTML body. Most news feeds carry the picture only here."""
    for tag in _IMG_TAG.finditer(html or ''):
        attrs = tag.group(1)
        if _TINY_IMG.search(attrs):
            continue  # a tracking pixel, not a picture
        src = _IMG_SRC.search(attrs)
        if not src:
            continue
        ref = src.group(1).strip()
        if ref.startswith('//'):
            ref = 'https:' + ref
        image = safe_image(urljoin(base, ref))
        if image:
            return image
    return ''


def _media_image(el) -> str:
    """A media:thumbnail or media:content element that actually points at an image."""
    url = el.get('url', '')
    if not url.startswith('https://'):
        return ''
    kind = (el.get('type') or '').lower()
    medium = (el.get('medium') or '').lower()
    if (kind and not kind.startswith('image/')) or (medium and medium != 'image'):
        return ''
    return safe_image(url)


def parse_feed(data: bytes, url: str) -> list[dict]:
    if data.lstrip().startswith(b'{'):
        feed = json.loads(data)
        if (not isinstance(feed, dict) or feed.get('version') not in ('https://jsonfeed.org/version/1', 'https://jsonfeed.org/version/1.1')
                or not isinstance(feed.get('title'), str) or not isinstance(feed.get('items'), list)):
            raise ValueError('Not a valid JSON Feed.')
        out = []
        for item in feed['items'][:50]:
            if not isinstance(item, dict) or not isinstance(item.get('id'), str):
                raise ValueError('Invalid JSON Feed item.')
            text_content = item.get('content_text') if isinstance(item.get('content_text'), str) else ''
            html_content = item.get('content_html') if isinstance(item.get('content_html'), str) else ''
            if not isinstance(item.get('content_text'), str) and not isinstance(item.get('content_html'), str):
                raise ValueError('JSON Feed items need text or HTML content.')
            content = text_content or strip_tags(html_content)
            link = item.get('url') or item.get('external_url') or ''
            title = clean(item.get('title') or content, 500)
            if not isinstance(link, str) or not title or not content: continue
            author = item.get('authors') or feed.get('authors') or []
            if not isinstance(author, list): author = []
            image = safe_image(item['image']) if isinstance(item.get('image'), str) else ''
            if not image and isinstance(item.get('banner_image'), str):
                image = safe_image(item['banner_image'])
            if not image:
                image = _html_image(html_content, url)
            out.append({'url': urljoin(url, link), 'title': title, 'type': 'news', 'provider': 'rss',
                        'publisher': clean(feed['title'], 200), 'summary': clean(item.get('summary') or content, 1000),
                        'published_at': ts(item.get('date_published')), 'image': image,
                        'author': ', '.join(clean(a.get('name'), 100) for a in author[:3] if isinstance(a, dict) and a.get('name')),
                        'external_id': clean(item['id'], 500), 'meta': {'updated_at': ts(item.get('date_modified'))}})
        return out
    root = _xml(data)
    root_kind = _local(root.tag).lower()
    if root_kind not in ('rss', 'rdf', 'feed'):
        raise ValueError('Not an RSS or Atom feed.')
    if root_kind in ('rss', 'rdf') and not any(_local(c.tag).lower() == 'channel' for c in root):
        raise ValueError('RSS feed is missing its channel.')
    channel = clean(next((''.join(c.itertext()) for c in root.iter() if _local(c.tag) == 'title'), ''), 200)
    youtube = (urlsplit(url).hostname or '').endswith('youtube.com')
    out = []
    for entry in root.iter():
        if _local(entry.tag) not in ('item', 'entry'):
            continue
        f: dict = {'meta': {}, 'publisher': channel, 'provider': 'rss'}
        for c in entry:
            tag, text = _local(c.tag), ''.join(c.itertext())
            if tag == 'title': f['title'] = clean(text, 500)
            elif tag == 'link' and c.get('rel', 'alternate') == 'alternate': f['url'] = (c.get('href') or c.text or '').strip()
            elif tag in ('thumbnail', 'content') and (media := _media_image(c)):
                if not f.get('image'): f['image'] = media
            elif tag in ('description', 'summary', 'content'):
                if not f.get('summary'): f['summary'] = strip_tags(text)
                if not f.get('image'): f['image'] = _html_image(text, url)
            elif tag in ('pubDate', 'published', 'date') and not f.get('published_at'): f['published_at'] = ts(text)
            elif tag == 'updated': f['meta']['updated_at'] = ts(text)
            elif tag in ('author', 'creator'): f['author'] = clean(text, 200)
            elif tag == 'videoId': f['meta']['video_id'] = text.strip()
            elif tag == 'group':
                for g in c.iter():
                    if _local(g.tag) in ('thumbnail', 'content') and (media := _media_image(g)):
                        if not f.get('image'): f['image'] = media
                    if _local(g.tag) == 'description':
                        inner = ''.join(g.itertext())
                        if not f.get('summary'): f['summary'] = clean(inner, 1000)
                        if not f.get('image'): f['image'] = _html_image(inner, url)
            elif tag == 'enclosure' and (c.get('type') or '').startswith('image/'): f['image'] = safe_image(c.get('url', ''))
        if not f.get('title') or not f.get('url'):
            continue
        f['published_at'] = f.get('published_at') or f['meta'].get('updated_at')
        f['url'] = urljoin(url, f['url'])
        f['type'] = 'video' if youtube or f['meta'].get('video_id') else 'news'
        out.append(f)
        if len(out) >= 50:
            break
    return out


def _mastodon_tags(q: str) -> list[str]:
    """A query becomes up to three hashtag candidates; nothing is invented, only reshaped."""
    words = [w for w in re.split(r'[^a-z0-9_]+', q.lower()) if len(w) >= 2][:6]
    if not words:
        return []
    joined = ''.join(words)
    out = [joined] if len(joined) >= 3 else []
    if len(words) > 1 and any(w in MEME_WORDS for w in words):
        # "linux memes" means #linuxmemes or #memes, never all of #linux: that tag is mostly news and chat.
        return (out + [w for w in words if w in MEME_WORDS and w not in out])[:3]
    out += [w for w in words if w not in out]
    return out[:3]


MEME_WORDS = {'meme', 'memes', 'humor', 'humour', 'funny', 'jokes', 'joke', 'shitpost', 'shitposting', 'comics', 'lol'}


_PLAIN_LINK = re.compile(r'<a\b(?![^>]*class="[^"]*(?:mention|hashtag))[^>]*>.*?</a>', re.I | re.S)


def _mastodon_status(s: dict, instance: str) -> dict | None:
    """One public status → item dict. Replies and boosts are skipped; engagement counts are stored as observed."""
    if not isinstance(s, dict) or s.get('visibility') != 'public' or s.get('in_reply_to_id') or s.get('reblog'):
        return None
    display_url = s.get('url')
    url = s.get('uri') or display_url
    if not isinstance(url, str) or not url.startswith('https://'):
        return None
    media = [m for m in (s.get('media_attachments') or []) if isinstance(m, dict)]
    text = strip_tags(s.get('content') or '', TEXT_LIMIT)
    if not text and not media:
        return None
    account = s.get('account') if isinstance(s.get('account'), dict) else {}
    tags = [str(t.get('name')) for t in (s.get('tags') or []) if isinstance(t, dict) and t.get('name')]
    # Prefer playable media, keeping its poster distinct from the video bytes.
    playable = [m for m in media if m.get('type') in ('video', 'gifv') and safe_image(m.get('url'))]
    images = [m for m in media if m.get('type') == 'image' and safe_image(m.get('url'))]
    first = (playable or images or [{}])[0]
    preview = safe_image(first.get('preview_url'))
    if first.get('type') == 'image':
        preview = preview or safe_image(first.get('url'))
    # Mastodon splits a link into invisible and ellipsis spans, which come out as separate words:
    # drop plain links (not mentions or hashtags) from the markup before taking the caption.
    caption = strip_tags(_PLAIN_LINK.sub(' https://link ', s.get('content') or ''))
    title = clean(drop_links(caption.split('\n')[0]) if caption else '', 500) or clean(first.get('description'), 500) or f'{instance} post'
    community = f'#{tags[0]}' if tags else str(account.get('acct') or account.get('username') or instance)
    return {
        'url': url, 'type': 'meme', 'provider': 'mastodon', 'publisher': urlsplit(url).hostname or instance,
        'author': clean(f"{account.get('display_name') or account.get('username') or ''} ({account.get('acct') or ''})", 200),
        'title': title, 'summary': clean(text, 1000), 'body': text, 'image': preview,
        'published_at': ts(s.get('created_at')), 'external_id': f"mstdn:{s.get('uri') or url}",
        'meta': {'platform': 'mastodon', 'social_post': True, 'origin_uri': s.get('uri') or url, 'display_url': display_url, 'instance': instance, 'community': community[:200], 'tags': tags[:8],
                 'language': str(s.get('language') or '')[:8], 'sensitive': bool(s.get('sensitive')),
                 'spoiler': clean(s.get('spoiler_text'), 300),
                 'media': {'type': str(first.get('type') or ''), 'mime': str(first.get('mime_type') or ''),
                           'url': safe_image(first.get('url')), 'preview_url': safe_image(first.get('preview_url')),
                           'alt': clean(first.get('description'), 300)} if media else {},
                 'observations': {'replies': s.get('replies_count'), 'reblogs': s.get('reblogs_count'),
                                  'favourites': s.get('favourites_count')}},
    }


def mastodon(url: str, q: str = '') -> list[dict]:
    p = urlsplit(curation.canonical(url))
    instance = p.hostname
    path = p.path.rstrip('/')
    m = re.fullmatch(r'/(?:tags|tag|api/v1/timelines/tag)/([^/]+)', path)
    if path not in ('', '/api/v1/timelines/public') and not m:
        raise ValueError('Use an HTTPS Mastodon instance URL or a hashtag URL such as https://mastodon.world/tags/memes.')
    # An explicit hashtag source is authoritative; curator keywords must not broaden it.
    tags = [unquote(m.group(1))] if m else _mastodon_tags(q)
    if any(not re.fullmatch(r'[^\W]+', tag, re.UNICODE) for tag in tags):
        raise ValueError('Use a Mastodon hashtag containing letters, numbers or underscores.')
    endpoints = [f'https://{instance}/api/v1/timelines/tag/{quote(t, safe="")}?limit=30' for t in tags[:3]] \
        or [f'https://{instance}/api/v1/timelines/public?limit=30']
    out, seen = [], set()
    for endpoint in endpoints:
        try:
            data = json.loads(curation.fetch(endpoint, kinds=('application/json',)))
        except ValueError as exc:
            if 'HTTP 401' in str(exc) or 'HTTP 403' in str(exc):
                raise ValueError('This Mastodon instance requires login for public timelines. Choose an instance that allows anonymous public or hashtag timelines.') from exc
            raise
        if isinstance(data, dict) and data.get('error'):
            raise ValueError('Mastodon rejected this public timeline request. The instance may require login; choose an instance allowing anonymous timelines.')
        if not isinstance(data, list):
            raise ValueError('Unexpected Mastodon response.')
        for s in data[:40]:
            item = _mastodon_status(s, instance)
            if not item or item['external_id'] in seen:
                continue
            seen.add(item['external_id'])
            out.append(item)
    return out


def devto(_url: str, q: str) -> list[dict]:
    """Developer articles from dev.to. The first query word is used as a tag when it is a usable one."""
    tag = re.sub(r'[^a-z0-9]', '', (q.split() or [''])[0].lower())
    params = {'per_page': 25, 'top': 7}
    if tag:
        params['tag'] = tag
    data = json.loads(curation.fetch('https://dev.to/api/articles?' + urlencode(params),
                                     kinds=('application/json',)))
    if not isinstance(data, list):
        raise ValueError('Unexpected dev.to response.')
    return [{'url': a['url'], 'title': clean(a.get('title'), 500), 'type': 'article', 'provider': 'devto',
             'publisher': 'dev.to', 'author': clean((a.get('user') or {}).get('name'), 100),
             'summary': clean(a.get('description'), 1000), 'image': safe_image(a.get('cover_image')),
             'published_at': ts(a.get('published_at')), 'external_id': f"devto:{a.get('id')}",
             'meta': {'tags': (a.get('tag_list') or [])[:8], 'reactions': a.get('positive_reactions_count'),
                      'comments': a.get('comments_count')}}
            for a in data[:25] if isinstance(a, dict) and a.get('url') and a.get('title')]


def lobsters(url: str, _q: str = '') -> list[dict]:
    """Lobste.rs stories. The source URL picks the list: hottest.json or newest.json."""
    data = json.loads(curation.fetch(url, kinds=('application/json',)))
    if not isinstance(data, list):
        raise ValueError('Unexpected Lobste.rs response.')
    return [{'url': s.get('url') or s.get('comments_url'), 'title': clean(s.get('title'), 500), 'type': 'news',
             'provider': 'lobsters', 'publisher': 'Lobste.rs', 'author': clean(s.get('submitter_user'), 100),
             'summary': clean(s.get('description_plain') or s.get('description'), 1000),
             'published_at': ts(s.get('created_at')), 'external_id': f"lobsters:{s.get('short_id')}",
             'meta': {'tags': (s.get('tags') or [])[:8], 'score': s.get('score'),
                      'comments': s.get('comment_count'), 'discussion': s.get('comments_url')}}
            for s in data[:40] if isinstance(s, dict) and s.get('title') and (s.get('url') or s.get('comments_url'))]


REDDIT_SUB = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_]{1,20}$')
REDDIT_SORTS = {'hot': '', 'new': 'new/', 'rising': 'rising/', 'top': 'top/'}
_REDDIT_LINK = re.compile(r'<a href="([^"]+)">\[link\]</a>')
REDDIT_HOSTS = ('reddit.com', 'redd.it', 'redditmedia.com')


# Reddit answers anonymous feed reads in quick succession with 429. Every subreddit source (news and
# memes, all curators) goes through one pace (60 s), and after a 429 Reddit rests until the window passes.
REDDIT_GAP_S = 60.0
REDDIT_BLOCK_S = 600
_reddit_lock = threading.Lock()
_reddit_last = 0.0
_reddit_blocked_until = 0.0


REDDIT_MAX_WAIT_S = 600  # a read further back in the queue than this fails fast and retries next run


def _reddit_get(feed: str) -> bytes:
    """One paced Reddit read. Each caller reserves the next free slot, then sleeps outside the lock."""
    global _reddit_last, _reddit_blocked_until
    with _reddit_lock:
        now = time.monotonic()
        if now < _reddit_blocked_until:
            raise RateLimited(f'Reddit asked us to slow down; trying again in {int(_reddit_blocked_until - now)}s.')
        slot = max(now, _reddit_last + REDDIT_GAP_S)
        if slot - now > REDDIT_MAX_WAIT_S:
            raise RateLimited(f'Reddit is busy with other sources; trying again in {int(slot - now)}s.')
        _reddit_last = slot
    if slot > now:
        time.sleep(slot - now)
    try:
        return curation.fetch(feed, headers={'User-Agent': 'WatchCraft/1.0 (self-hosted news reader)'})
    except curation.FetchError as e:
        if e.status == 429:
            hint = int(e.retry_after) if e.retry_after.isdigit() else 0
            with _reddit_lock:
                _reddit_blocked_until = max(_reddit_blocked_until, time.monotonic() + min(max(REDDIT_BLOCK_S, hint), 3600))
            raise RateLimited('Reddit asked us to slow down; trying again later.') from e
        raise


def reddit_feed(source: str) -> str:
    """r/news, news, or a reddit.com/r/<sub>[/hot|new|rising|top][?t=day] URL -> its public RSS feed."""
    raw = source.strip()
    if '://' not in raw:
        raw = 'https://www.reddit.com/r/' + raw.removeprefix('/').removeprefix('r/')
    parts = urlsplit(raw)
    host = (parts.hostname or '').lower()
    if host != 'reddit.com' and not host.endswith('.reddit.com'):
        raise ValueError('Use a subreddit like r/worldnews or https://www.reddit.com/r/worldnews/top/?t=day.')
    path = [p for p in parts.path.split('/') if p]
    if len(path) < 2 or path[0].lower() != 'r' or not REDDIT_SUB.match(path[1]):
        raise ValueError('Use a subreddit like r/worldnews.')
    sort = path[2].lower() if len(path) > 2 and path[2].lower() in REDDIT_SORTS else 'top'
    window = re.search(r'(?:^|&)t=(hour|day|week)(?:&|$)', parts.query or '')
    query = f'?t={window.group(1) if window else "day"}&limit=50' if sort == 'top' else '?limit=50'
    return f'https://www.reddit.com/r/{path[1]}/{REDDIT_SORTS[sort]}.rss{query}'


def reddit(url: str, _q: str = '') -> list[dict]:
    """A subreddit's public RSS (top of the day by default), resolved to the articles it links.

    Self posts and reddit-hosted media are skipped: News wants the reporting, with the thread kept
    as the discussion link. Votes are popularity, never verification."""
    feed = reddit_feed(url)
    sub = feed.split('/r/', 1)[1].split('/', 1)[0]
    root = _xml(_reddit_get(feed))
    out = []
    for entry in root.iter():
        if _local(entry.tag) != 'entry':
            continue
        title = thread = html = ''
        published = None
        image = ''
        for c in entry:
            tag = _local(c.tag)
            if tag == 'title': title = clean(''.join(c.itertext()), 500)
            elif tag == 'link': thread = (c.get('href') or '').strip()
            elif tag == 'content': html = ''.join(c.itertext())
            elif tag == 'published': published = ts(''.join(c.itertext())) or published
            elif tag == 'updated' and not published: published = ts(''.join(c.itertext()))
            elif tag == 'thumbnail' and not image: image = safe_image(c.get('url') or '')
        m = _REDDIT_LINK.search(html)
        link = unescape_html(m.group(1)) if m else ''
        host = (urlsplit(link).hostname or '').lower()
        if not title or not link.startswith('https://') or any(host == h or host.endswith('.' + h) for h in REDDIT_HOSTS):
            continue
        out.append({'url': link, 'title': title, 'type': 'news', 'provider': 'subreddit',
                    'publisher': host.removeprefix('www.'), 'published_at': published, 'image': image,
                    'summary': '', 'external_id': link,
                    'meta': {'platform': 'reddit', 'community': f'r/{sub}', 'discussion': thread,
                             'discussion_url': thread, 'observed_at': time.time()}})
        if len(out) >= 50:
            break
    return out


REDDIT_IMAGE_HOSTS = ('i.redd.it', 'i.imgur.com')
_IMAGE_PATH = re.compile(r'\.(?:jpe?g|png|webp|gif)$', re.I)


def reddit_memes(url: str, _q: str = '') -> list[dict]:
    """Image posts from a meme subreddit (top of the day by default) as memes, picture first.

    Only direct images (i.redd.it, i.imgur.com) are kept: galleries, videos, text and link posts are
    skipped. The thread is the item; votes are popularity only."""
    feed = reddit_feed(url)
    sub = feed.split('/r/', 1)[1].split('/', 1)[0]
    root = _xml(_reddit_get(feed))
    out = []
    for entry in root.iter():
        if _local(entry.tag) != 'entry':
            continue
        title = thread = html = author = ''
        published = None
        for c in entry:
            tag = _local(c.tag)
            if tag == 'title': title = clean(''.join(c.itertext()), 500)
            elif tag == 'link': thread = (c.get('href') or '').strip()
            elif tag == 'content': html = ''.join(c.itertext())
            elif tag == 'published': published = ts(''.join(c.itertext())) or published
            elif tag == 'author': author = clean(''.join(c.itertext()), 100)
        m = _REDDIT_LINK.search(html)
        link = unescape_html(m.group(1)) if m else ''
        parts = urlsplit(link)
        if (not title or not thread.startswith('https://www.reddit.com/') or parts.scheme != 'https'
                or (parts.hostname or '').lower() not in REDDIT_IMAGE_HOSTS or not _IMAGE_PATH.search(parts.path)):
            continue
        image = safe_image(link)
        # Reddit's feed carries no over_18 flag: posts that call themselves NSFW or spoilers are skipped.
        if not image or re.search(r'\b(nsfw|nsfl|spoiler)\b', title, re.I):
            continue
        out.append({'url': thread, 'title': title, 'type': 'meme', 'provider': 'reddit_memes',
                    'publisher': f'r/{sub}', 'author': author.removeprefix('/u/'), 'published_at': published,
                    'image': image, 'summary': '', 'external_id': f'reddit:{thread}',
                    'meta': {'platform': 'reddit', 'social_post': True, 'community': f'r/{sub}',
                             'discussion_url': thread, 'observed_at': time.time(),
                             'media': {'type': 'image', 'url': image, 'preview_url': image, 'alt': title}}})
        if len(out) >= 50:
            break
    return out


def bing_news(_url: str, q: str = '') -> list[dict]:
    """Bing News search RSS for one curator query: current reporting from many outlets.

    Licensed for personal, non-commercial RSS readers. Links are click-tracking redirects whose
    `url` parameter is the article itself; only https article links are kept."""
    from urllib.parse import parse_qs
    if not q.strip():
        return []
    feed = 'https://www.bing.com/news/search?' + urlencode({'q': q.strip()[:200], 'format': 'rss', 'setlang': 'en'})
    out = []
    for item in parse_feed(curation.fetch(feed), feed):
        link = item.get('url') or ''
        target = (parse_qs(urlsplit(link).query).get('url') or [''])[0] if (urlsplit(link).hostname or '').lower().removeprefix('www.') == 'bing.com' else link
        host = (urlsplit(target).hostname or '').lower()
        if not target.startswith('https://') or not host or host.endswith('bing.com'):
            continue
        item.update(url=target, provider='bing_news', type='news', external_id=target,
                    publisher=item.get('publisher') if item.get('publisher') and 'Bing' not in item['publisher'] else host.removeprefix('www.'))
        out.append(item)
    return out


BSKY_API = 'https://public.api.bsky.app/xrpc/'
_BSKY_ID = re.compile(r'^(did:plc:[a-z2-7]{24}|did:web:[a-z0-9.-]+|[a-z0-9][a-z0-9.-]{1,252}\.[a-z]{2,})$', re.I)


def _bsky(method: str, **params) -> dict:
    data = json.loads(curation.fetch(BSKY_API + method + '?' + urlencode(params), kinds=('application/json',)))
    if not isinstance(data, dict):
        raise ValueError('Unexpected Bluesky response.')
    return data


def bluesky_source(url: str) -> tuple[str, str, str]:
    """bsky.app/profile/<handle> -> ('author', actor, '') ; .../profile/<actor>/feed/<rkey> -> ('feed', actor, rkey)."""
    parts = urlsplit(url.strip() if '://' in url else 'https://bsky.app/profile/' + url.strip().lstrip('@'))
    path = [p for p in parts.path.split('/') if p]
    if (parts.hostname or '').lower() != 'bsky.app' or len(path) < 2 or path[0] != 'profile' or not _BSKY_ID.match(path[1]):
        raise ValueError('Use a Bluesky account (https://bsky.app/profile/reuters.com) or a custom feed URL.')
    if len(path) >= 4 and path[2] == 'feed' and re.fullmatch(r'[A-Za-z0-9._~-]{1,64}', path[3]):
        return 'feed', path[1], path[3]
    return 'author', path[1], ''


def bluesky(url: str, _q: str = '') -> list[dict]:
    """Links shared by one Bluesky account or custom feed, resolved to the articles they point at."""
    kind, actor, rkey = bluesky_source(url)
    if kind == 'feed':
        did = actor if actor.startswith('did:') else _bsky('app.bsky.actor.getProfile', actor=actor).get('did', '')
        if not str(did).startswith('did:'):
            raise ValueError('Bluesky account not found.')
        data = _bsky('app.bsky.feed.getFeed', feed=f'at://{did}/app.bsky.feed.generator/{rkey}', limit=100)
    else:
        data = _bsky('app.bsky.feed.getAuthorFeed', actor=actor, limit=100, filter='posts_no_replies')
    out, seen = [], set()
    for row in data.get('feed') or []:
        post = (row or {}).get('post') or {}
        if any((l or {}).get('val') in ('porn', 'sexual', 'nudity', 'graphic-media') for l in post.get('labels') or []):
            continue
        embed = post.get('embed') or {}
        ext = embed.get('external') or (embed.get('media') or {}).get('external') or {}
        link = str(ext.get('uri') or '')
        host = (urlsplit(link).hostname or '').lower()
        if not link.startswith('https://') or host.endswith('bsky.app') or link in seen or not ext.get('title'):
            continue
        seen.add(link)
        author = post.get('author') or {}
        rkey_post = str(post.get('uri') or '').rsplit('/', 1)[-1]
        thread = f"https://bsky.app/profile/{author.get('handle', '')}/post/{rkey_post}" if author.get('handle') else ''
        out.append({'url': link, 'title': clean(ext.get('title'), 500), 'type': 'news', 'provider': 'bluesky',
                    'publisher': host.removeprefix('www.'), 'summary': clean(ext.get('description'), 1000),
                    'image': safe_image(str(ext.get('thumb') or '')),
                    'published_at': ts((post.get('record') or {}).get('createdAt') or post.get('indexedAt')),
                    'external_id': link,
                    'meta': {'platform': 'bluesky', 'shared_by': author.get('handle', ''), 'discussion_url': thread,
                             'likes': post.get('likeCount'), 'reposts': post.get('repostCount')}})
        if len(out) >= 60:
            break
    return out


TIKTOK_HANDLE = re.compile(r'^[A-Za-z0-9_.]{2,24}$')
TIKTOK_MAX_SECONDS = 180  # the local clip limit (capture.VIDEO_MAX_SECONDS)


def tiktok_handle(source: str) -> str:
    """@bbcnews, bbcnews or https://www.tiktok.com/@bbcnews -> 'bbcnews'."""
    raw = source.strip()
    if '://' in raw:
        parts = urlsplit(raw)
        host = (parts.hostname or '').lower()
        path = [p for p in parts.path.split('/') if p]
        if host not in ('tiktok.com', 'www.tiktok.com', 'm.tiktok.com') or not path or not path[0].startswith('@'):
            raise ValueError('Use a TikTok account like https://www.tiktok.com/@bbcnews.')
        raw = path[0]
    handle = raw.removeprefix('@')
    if not TIKTOK_HANDLE.match(handle):
        raise ValueError('Use a TikTok account like https://www.tiktok.com/@bbcnews.')
    return handle


def tiktok(url: str, _q: str = '') -> list[dict]:
    """Recent clips from one public TikTok account, listed by yt-dlp (no login, no cookies).

    Clips become Moments (type meme); capture downloads each one locally through yt-dlp so it plays
    without TikTok. Hashtag and search pages need a logged-in app session and are not supported."""
    import yt_dlp
    handle = tiktok_handle(url)
    opts = {'quiet': True, 'no_warnings': True, 'extract_flat': True, 'playlistend': 30, 'skip_download': True,
            'socket_timeout': 20}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f'https://www.tiktok.com/@{handle}', download=False) or {}
    except Exception as e:  # noqa: BLE001 — yt-dlp errors carry no secrets; keep them short
        raise ValueError(f'TikTok account could not be read: {str(e)[:160]}') from e
    out = []
    for e in info.get('entries') or []:
        vid = str((e or {}).get('id') or '')
        if not re.fullmatch(r'\d{8,25}', vid):
            continue
        duration = e.get('duration')
        if isinstance(duration, (int, float)) and duration > TIKTOK_MAX_SECONDS:
            continue
        link = f'https://www.tiktok.com/@{handle}/video/{vid}'
        caption = clean(e.get('title') or e.get('description') or '', 500) or f'@{handle} on TikTok'
        thumbs = [t.get('url') for t in e.get('thumbnails') or [] if isinstance(t, dict) and t.get('url')]
        out.append({'url': link, 'title': caption, 'type': 'meme', 'provider': 'tiktok',
                    'publisher': f'@{handle}', 'author': clean(e.get('uploader') or handle, 100),
                    'summary': clean(e.get('description') or '', 1000),
                    'image': safe_image(thumbs[-1]) if thumbs else '',
                    'published_at': float(e['timestamp']) if isinstance(e.get('timestamp'), (int, float)) else None,
                    'external_id': f'tiktok:{vid}',
                    'meta': {'platform': 'tiktok', 'social_post': True, 'community': f'@{handle}', 'discussion_url': link,
                             'media': {'type': 'video', 'via': 'yt-dlp', 'url': link},
                             'duration': duration, 'views': e.get('view_count'), 'likes': e.get('like_count')}})
    return out


YT_SHORTS_PER_QUERY = 10  # each clip is downloaded (<=25 MB), so a query brings a handful, not a page


def youtube_shorts(_url: str, q: str = '') -> list[dict]:
    """YouTube Shorts for one curator query, through the app's YouTube client (cookies, proxy, PO tokens).

    Like TikTok clips they become Moments (type meme) and capture saves each one locally. Longer videos stay
    with the video categories, which download and play them as full videos."""
    from .. import ytdl
    if not q.strip():
        return []
    try:
        found = ytdl.search_shorts(q.strip()[:200], YT_SHORTS_PER_QUERY, 'worker')
    except Exception as e:  # noqa: BLE001 — YtBlocked / YtError carry no secrets; keep them short
        raise ValueError(f'YouTube Shorts search failed: {str(e)[:160]}') from e
    out = []
    for e in found:
        vid = e.get('id') or ''
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', vid) or e.get('is_live'):
            continue
        # Shorts search gives id, title and views only; length and channel are checked when the clip is saved.
        link = f'https://www.youtube.com/shorts/{vid}'
        published = e.get('published_at')
        out.append({'url': link, 'title': clean(e.get('title') or '', 500) or 'YouTube Short', 'type': 'meme',
                    'provider': 'youtube_shorts', 'publisher': clean(e.get('channel_name') or 'YouTube', 100),
                    'author': clean(e.get('channel_name') or '', 100), 'summary': '',
                    'image': f'https://i.ytimg.com/vi/{vid}/hqdefault.jpg',
                    'published_at': published.timestamp() if published else None,
                    'external_id': f'youtube:{vid}',
                    'meta': {'platform': 'youtube', 'social_post': True, 'community': 'YouTube Shorts',
                             'discussion_url': link, 'video_id': vid,
                             'media': {'type': 'video', 'via': 'yt-dlp', 'url': link},
                             'duration': e.get('duration_s') or None, 'views': e.get('view_count') or None}})
    return out


def rss(url: str, _q: str = '') -> list[dict]:
    return parse_feed(curation.fetch(url), url)


class RateLimited(ValueError):
    """A host asked us to stop; the rest of this source's queries would fail too."""


# GitHub's unauthenticated search API allows ten requests a minute per address. Several
# curators share one source here, so a run can ask for thirty searches at once: pace them
# at half the limit, and once GitHub says stop, stop until it says the window has reset.
GITHUB_SEARCH_GAP_S = 12.0
_github_lock = threading.Lock()
_github_last = 0.0
_github_blocked_until = 0.0


def _github_pace() -> None:
    global _github_last
    with _github_lock:
        now = time.monotonic()
        if now < _github_blocked_until:
            raise RateLimited(f'GitHub rate limit reached: 10 searches a minute without a token. '
                              f'It resets in {int(_github_blocked_until - now)}s.')
        wait = GITHUB_SEARCH_GAP_S - (now - _github_last)
        if wait > 0:
            time.sleep(wait)
        _github_last = time.monotonic()


def _github_block(seconds: int) -> None:
    global _github_blocked_until
    with _github_lock:
        _github_blocked_until = max(_github_blocked_until, time.monotonic() + max(0, seconds))


def _github_limited(exc: Exception, budget: str) -> ValueError | None:
    """Turn GitHub's quota 403 into something a person can act on.

    Only a refusal that names a reset is a quota answer; any other 403 keeps its
    plain HTTP error rather than being mislabelled as a rate limit.
    """
    if not isinstance(exc, curation.FetchError) or exc.status not in (403, 429):
        return None
    wait = int(float(exc.retry_after or 0))
    if wait <= 0:
        return None
    _github_block(wait)
    return RateLimited(f'GitHub rate limit reached: {budget}. It resets in {wait}s.')


def github_releases(url: str, _q: str = '') -> list[dict]:
    p = urlsplit(url)
    parts = p.path.strip('/').split('/')
    if p.hostname != 'github.com' or len(parts) != 2 or any(not x or not all(c.isalnum() or c in '-_.' for c in x) for x in parts):
        raise ValueError('Use https://github.com/owner/repository.')
    try:
        data = json.loads(curation.fetch(f'https://api.github.com/repos/{parts[0]}/{parts[1]}/releases?per_page=20'))
    except curation.FetchError as e:
        raise _github_limited(e, '60 requests an hour without a token') or e
    if not isinstance(data, list):
        raise ValueError('Unexpected GitHub response.')
    repo = f'{parts[0]}/{parts[1]}'
    return [{'url': r['html_url'], 'title': f"{repo} {r.get('name') or r['tag_name']}", 'type': 'project',
             'summary': clean(r.get('body'), 1000), 'published_at': ts(r.get('published_at')), 'publisher': 'GitHub',
             'author': clean((r.get('author') or {}).get('login'), 100), 'external_id': f"gh-release:{r.get('id')}",
             'meta': {'repository': repo, 'version': r.get('tag_name', ''), 'prerelease': bool(r.get('prerelease')),
                      'security': 'security' in (r.get('body') or '').lower() or bool(CVE_RE.search(r.get('body') or ''))}}
            for r in data[:20] if r.get('html_url')]


def github_search(_url: str, q: str) -> list[dict]:
    _github_pace()
    try:
        data = json.loads(curation.fetch('https://api.github.com/search/repositories?'
                                         + urlencode({'q': q, 'sort': 'updated', 'per_page': 15})))
    except curation.FetchError as e:
        raise _github_limited(e, '10 searches a minute without a token') or e
    return [{'url': r['html_url'], 'title': r['full_name'], 'type': 'project', 'summary': clean(r.get('description'), 1000),
             'publisher': 'GitHub', 'author': clean((r.get('owner') or {}).get('login'), 100),
             'published_at': ts(r.get('created_at')), 'external_id': f"gh-repo:{r.get('id')}",
             'meta': {'stars': r.get('stargazers_count'), 'language': r.get('language') or '', 'license': ((r.get('license') or {}).get('spdx_id') or ''),
                      'pushed_at': ts(r.get('pushed_at')), 'topics': (r.get('topics') or [])[:10]}}
            for r in (data.get('items') or [])[:15] if r.get('html_url')]


# A search for the words "latest news" finds the few stories titled that way, often days old.
# A general news query means "what is on Hacker News now": the front page.
GENERIC_NEWS = {'', 'news', 'latest', 'latest news', 'top news', 'top stories', 'breaking news', 'headlines', 'current events'}


def hackernews(_url: str, q: str) -> list[dict]:
    if q.strip().lower() in GENERIC_NEWS:
        url = 'https://hn.algolia.com/api/v1/search?' + urlencode({'tags': 'front_page', 'hitsPerPage': 30})
    else:
        url = 'https://hn.algolia.com/api/v1/search_by_date?' + urlencode({'query': q, 'tags': 'story', 'hitsPerPage': 20})
    data = json.loads(curation.fetch(url))
    out = []
    for h in data.get('hits') or []:
        discussion = f"https://news.ycombinator.com/item?id={h.get('objectID')}"
        url = h.get('url') or discussion
        out.append({'url': url, 'title': clean(h.get('title'), 500), 'type': 'news', 'publisher': urlsplit(url).hostname or 'Hacker News',
                    'author': clean(h.get('author'), 100), 'published_at': h.get('created_at_i'), 'external_id': f"hn:{h.get('objectID')}",
                    'meta': {'points': h.get('points'), 'comments': h.get('num_comments'), 'discussion_url': discussion}})
    return [o for o in out if o['title']]


def arxiv(_url: str, q: str) -> list[dict]:
    root = _xml(curation.fetch('https://export.arxiv.org/api/query?' + urlencode({'search_query': f'all:{q}', 'sortBy': 'submittedDate', 'max_results': 15})))
    out = []
    for e in root.iter():
        if _local(e.tag) != 'entry':
            continue
        f = {'type': 'document', 'publisher': 'arXiv', 'meta': {'authors': []}}
        for c in e:
            tag = _local(c.tag)
            if tag == 'title': f['title'] = clean(''.join(c.itertext()), 500)
            elif tag == 'id': f['url'] = (c.text or '').strip().replace('http://', 'https://')
            elif tag == 'summary': f['summary'] = clean(''.join(c.itertext()), 1500)
            elif tag == 'published': f['published_at'] = ts(c.text)
            elif tag == 'author': f['meta']['authors'].append(clean(''.join(c.itertext()), 100))
            elif tag == 'link' and c.get('title') == 'pdf': f['meta']['pdf_url'] = (c.get('href') or '').replace('http://', 'https://')
        f['author'] = ', '.join(f['meta']['authors'][:3])
        if f.get('title') and f.get('url'):
            out.append(f)
    return out


def _severity(score) -> str:
    if score is None: return ''
    return 'critical' if score >= 9 else 'high' if score >= 7 else 'medium' if score >= 4 else 'low' if score > 0 else 'none'


def nvd(_url: str, q: str) -> list[dict]:
    cve = CVE_RE.search(q)
    params = {'cveId': cve.group(0).upper()} if cve else {'keywordSearch': q, 'resultsPerPage': 20}
    data = json.loads(curation.fetch('https://services.nvd.nist.gov/rest/json/cves/2.0?' + urlencode(params), limit=8_000_000))
    out = []
    for v in data.get('vulnerabilities') or []:
        c = v.get('cve') or {}
        cid = c.get('id', '')
        desc = next((d.get('value') for d in c.get('descriptions', []) if d.get('lang') == 'en'), '')
        score, vector = None, ''
        for key in ('cvssMetricV40', 'cvssMetricV31', 'cvssMetricV30', 'cvssMetricV2'):
            for metric in (c.get('metrics') or {}).get(key, []):
                if metric.get('type') == 'Primary' or score is None:
                    data_ = metric.get('cvssData') or {}
                    score, vector = data_.get('baseScore'), data_.get('vectorString', '')
            if score is not None:
                break
        products = sorted({':'.join(m.get('criteria', '').split(':')[3:5]) for conf in c.get('configurations', [])
                           for node in conf.get('nodes', []) for m in node.get('cpeMatch', []) if m.get('vulnerable')})[:20]
        out.append({'url': f'https://nvd.nist.gov/vuln/detail/{cid}', 'title': f'{cid}: {clean(desc, 160)}', 'type': 'advisory',
                    'summary': clean(desc, 2000), 'publisher': 'NVD', 'published_at': ts((c.get('published') or '') + 'Z'),
                    'external_id': cid,
                    'meta': {'cve_id': cid, 'cvss_score': score, 'cvss_vector': vector, 'severity': _severity(score),
                             'updated_at': ts((c.get('lastModified') or '') + 'Z'), 'products': products,
                             'known_exploited': bool(c.get('cisaExploitAdd')), 'status': c.get('vulnStatus', ''),
                             'references': [r.get('url') for r in c.get('references', [])[:15] if str(r.get('url', '')).startswith('https://')],
                             'source': 'NVD (authoritative score)'}})
    return out


KEV_URL = 'https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json'


def cisa_kev(_url: str, _q: str = '') -> list[dict]:
    data = json.loads(curation.fetch(KEV_URL, limit=12_000_000))
    rows = sorted(data.get('vulnerabilities') or [], key=lambda r: r.get('dateAdded', ''), reverse=True)[:200]
    return [{'url': f"https://nvd.nist.gov/vuln/detail/{r['cveID']}", 'title': f"{r['cveID']}: {r.get('vendorProject', '')} {r.get('product', '')} — {r.get('vulnerabilityName', '')}",
             'type': 'advisory', 'summary': clean(r.get('shortDescription'), 2000), 'publisher': 'CISA KEV',
             'published_at': ts((r.get('dateAdded') or '') + 'T00:00:00Z'), 'external_id': r['cveID'],
             'meta': {'cve_id': r['cveID'], 'vendor': r.get('vendorProject', ''), 'product': r.get('product', ''),
                      'known_exploited': True, 'severity': 'high', 'required_action': clean(r.get('requiredAction'), 500),
                      'due_date': r.get('dueDate', ''), 'ransomware': r.get('knownRansomwareCampaignUse', ''),
                      'source': 'CISA Known Exploited Vulnerabilities (confirmed exploitation, not applicability)'}}
            for r in rows if r.get('cveID')]


def wikipedia(_url: str, q: str) -> list[dict]:
    data = json.loads(curation.fetch('https://en.wikipedia.org/w/api.php?' + urlencode(
        {'action': 'query', 'list': 'search', 'srsearch': q, 'format': 'json', 'srlimit': 8})))
    return [{'url': 'https://en.wikipedia.org/wiki/' + quote(r['title'].replace(' ', '_')), 'title': r['title'], 'type': 'web',
             'summary': strip_tags(r.get('snippet', '')), 'publisher': 'Wikipedia', 'meta': {'wordcount': r.get('wordcount')}}
            for r in (data.get('query') or {}).get('search', [])]


def rdap(_url: str, q: str) -> list[dict]:
    domain = q.strip().lower().removeprefix('https://').removeprefix('http://').split('/')[0]
    if '.' not in domain or not all(c.isalnum() or c in '-.' for c in domain):
        return []  # rdap only answers domain-shaped queries
    data = json.loads(curation.fetch(f'https://rdap.org/domain/{domain}', kinds=curation.FEED_TYPES + ('application/rdap+json',)))
    events = {e.get('eventAction'): e.get('eventDate') for e in data.get('events', [])}
    registrar = next((_vcard_name(e) for e in data.get('entities', []) if 'registrar' in e.get('roles', [])), '')
    return [{'url': f'https://rdap.org/domain/{domain}', 'title': f'{domain} — domain registration', 'type': 'domain',
             'publisher': 'RDAP', 'external_id': f'rdap:{domain}',
             'summary': f"Registrar: {registrar or 'unknown'}. Registered {events.get('registration', '?')[:10]}, expires {events.get('expiration', '?')[:10]}.",
             'meta': {'domain': domain, 'registrar': registrar, 'status': data.get('status', []),
                      'registered': events.get('registration', ''), 'expires': events.get('expiration', ''),
                      'nameservers': [n.get('ldhName', '') for n in data.get('nameservers', [])][:8]}}]


def _vcard_name(entity) -> str:
    for row in (entity.get('vcardArray') or [None, []])[1]:
        if row and row[0] == 'fn':
            return clean(row[3], 200)
    return ''


def page(url: str, _q: str = '') -> list[dict]:
    item = extract_page(curation.fetch(url, kinds=curation.HTML_TYPES, limit=3_000_000), curation.canonical(url))
    item['provider'] = 'page'
    return [item]


def page_watch(url: str, _q: str = '', config: dict | None = None) -> list[dict]:
    data, final, _ = curation.fetch(url, kinds=curation.HTML_TYPES, limit=3_000_000, with_metadata=True)
    item = watchers.page(data, final, config)
    if not item['body']:
        raise ValueError('No readable page text was extracted.')
    item['provider'] = 'page_watch'
    return [item]


def parse_listing(data: bytes, url: str, config: dict | None = None) -> list[dict]:
    return watchers.listing(data, url, config)


def html_listing(url: str, _q: str = '', config: dict | None = None) -> list[dict]:
    data, final, _ = curation.fetch(url, kinds=curation.HTML_TYPES, limit=3_000_000, with_metadata=True)
    items = parse_listing(data, final, config)
    if not items:
        raise ValueError('No article links were found in the listing.')
    return items


def sitemap(url: str, _q: str = '', config: dict | None = None) -> list[dict]:
    """Every same-host URL in a sitemap, following nested indexes within fixed bounds."""
    return watchers.crawl_sitemap(url, config)


def web_search(_url: str, q: str) -> list[dict]:
    cfg = get_settings()['search']
    if cfg['provider'] == 'searxng' and cfg['searxng_url']:
        base = cfg['searxng_url'].rstrip('/')
        # Trusted admin config (often LAN): no redirect following, bounded response.
        r = httpx.get(f'{base}/search', params={'q': q, 'format': 'json'}, timeout=20, follow_redirects=False,
                      headers={'User-Agent': 'WatchCraft/0.2'})
        r.raise_for_status()
        if len(r.content) > 3_000_000:
            raise ValueError('Search response too large.')
        rows = [(x.get('url'), x.get('title'), x.get('content'), x.get('publishedDate'), x.get('thumbnail') or x.get('img_src'))
                for x in r.json().get('results', [])[:20]]
    elif cfg['provider'] == 'brave' and cfg['brave_api_key']:
        data = json.loads(curation.fetch('https://api.search.brave.com/res/v1/web/search?' + urlencode({'q': q, 'count': 20}),
                                         headers={'X-Subscription-Token': cfg['brave_api_key']}))
        rows = [(x.get('url'), x.get('title'), x.get('description'), x.get('page_age'), (x.get('thumbnail') or {}).get('src'))
                for x in (data.get('web') or {}).get('results', [])[:20]]
    else:
        raise ValueError('No web search provider is configured.')
    out = []
    for url, title, snippet, date, img in rows:
        if not url or not title or not str(url).startswith(('https://', 'http://')):
            continue
        out.append({'url': url, 'title': clean(title, 500), 'summary': strip_tags(snippet or ''), 'published_at': ts(date),
                    'image': safe_image(img or ''), 'publisher': urlsplit(url).hostname or '', 'type': classify({'url': url, 'title': title, 'summary': snippet or ''})})
    return out


from .lemmy import lemmy
from .social_trends import mastodon_trends


PROVIDERS = {p.key: p for p in [
    Provider('rss', 'RSS / Atom / JSON feed', 'feed', 'Public RSS, Atom or JSON feeds, including YouTube channel feeds.', rss,
             ['feed', 'update-monitoring'], needs_url=True, default_type='news'),
    Provider('github_releases', 'GitHub releases', 'feed', 'Releases of one public repository.', github_releases,
             ['feed', 'update-monitoring', 'structured-metadata'], needs_url=True, default_type='project'),
    Provider('github_search', 'GitHub repositories', 'query', 'Search public repositories.', github_search,
             ['keyword-search', 'structured-metadata', 'rate-limited'], default_type='project'),
    Provider('hackernews', 'Hacker News', 'query', 'Stories via the Algolia HN API.', hackernews,
             ['keyword-search', 'pagination'], default_type='news'),
    Provider('arxiv', 'arXiv papers', 'query', 'Research preprints.', arxiv,
             ['keyword-search', 'structured-metadata', 'full-text'], default_type='document'),
    Provider('nvd', 'NVD CVE database', 'query', 'Authoritative CVE records and CVSS scores.', nvd,
             ['keyword-search', 'structured-metadata', 'rate-limited'], default_type='advisory'),
    Provider('cisa_kev', 'CISA Known Exploited Vulnerabilities', 'feed', 'Confirmed in-the-wild exploitation; filtered by your keywords.', cisa_kev,
             ['feed', 'structured-metadata'], default_type='advisory'),
    Provider('wikipedia', 'Wikipedia', 'query', 'Encyclopaedic background for research.', wikipedia,
             ['keyword-search'], default_type='web'),
    Provider('web_search', 'Web search', 'query', 'SearXNG (self-hosted) or Brave Search API.', web_search,
             ['keyword-search', 'requires-configuration'], default_type='web'),
    Provider('rdap', 'Domain registration (RDAP)', 'query', 'Registrar, dates and nameservers for a domain.', rdap,
             ['structured-metadata'], default_type='domain'),
    Provider('html_listing', 'Article listing', 'feed', 'New article links on a public listing page, using editable selector rules.', html_listing,
             ['update-monitoring', 'article-links'], needs_url=True, default_type='news'),
    Provider('sitemap', 'XML sitemap', 'feed', 'New and updated URLs from a public XML sitemap, including nested indexes.', sitemap,
             ['update-monitoring', 'structured-metadata'], needs_url=True, default_type='web'),
    Provider('page', 'Web page', 'url', 'Extract one page (OpenGraph, JSON-LD recipes/articles).', page,
             ['url-extraction', 'structured-metadata'], needs_url=True, default_type='web'),
    Provider('page_watch', 'Page change watcher', 'feed', 'Track text changes on one public page, optionally limited to a content area.', page_watch,
             ['update-monitoring', 'page-changes'], needs_url=True, default_type='web'),
    Provider('lemmy', 'Lemmy communities', 'feed', 'One public community per source. Use https://lemmy.world/c/selfhosted or !selfhosted@lemmy.world. Configure ranking, content type and optional RSS fallback in source settings.', lemmy,
             ['feed', 'structured-metadata', 'community-scoped'], needs_url=True, default_type='news', configurable=True),
    Provider('mastodon_trends', 'Mastodon trends', 'feed', 'Sampled instance trends. Use /api/v1/trends/links, /statuses or /tags on a public HTTPS instance. Popularity is not independent verification. Sensitive posts excluded by default.', mastodon_trends,
             ['feed', 'structured-metadata', 'instance-trends'], needs_url=True, default_type='news', configurable=True),
    Provider('mastodon', 'Mastodon communities', 'query', 'Public posts from an HTTPS instance or /tags/hashtag URL. Instance queries become hashtags; explicit hashtag URLs stay scoped. Requires anonymous timeline access.', mastodon,
             ['keyword-search', 'update-monitoring', 'structured-metadata'], needs_url=True, default_type='meme'),
    Provider('devto', 'dev.to articles', 'query', 'Developer articles from dev.to. The first query word is used as a tag when it is a usable one.', devto,
             ['keyword-search', 'structured-metadata'], default_type='article'),
    Provider('subreddit', 'Reddit subreddit', 'feed', 'Articles linked from one subreddit, top of the day by default. Use r/worldnews or https://www.reddit.com/r/worldnews/new/. Self posts are skipped; votes are popularity, not verification.', reddit,
             ['feed', 'community-scoped'], needs_url=True, default_type='news'),
    Provider('reddit_memes', 'Reddit memes', 'feed', 'Image posts from a meme subreddit, top of the day by default (r/memes, r/ProgrammerHumor, …/hot/). Galleries, videos and text posts are skipped.', reddit_memes,
             ['feed', 'community-scoped'], needs_url=True, default_type='meme'),
    Provider('bing_news', 'Bing News search', 'query', 'Current reporting from many outlets for each curator query (Bing News RSS, personal use).', bing_news,
             ['keyword-search', 'feed'], default_type='news'),
    Provider('bluesky', 'Bluesky account or feed', 'feed', 'Articles linked by one Bluesky account (https://bsky.app/profile/reuters.com) or custom feed (…/feed/<name>). Posts without a link are skipped.', bluesky,
             ['feed', 'structured-metadata'], needs_url=True, default_type='news'),
    Provider('tiktok', 'TikTok account', 'feed', 'Recent clips (up to 3 minutes) from one public account, saved locally so they play here. Use https://www.tiktok.com/@bbcnews. Hashtags and search need a login and are not supported.', tiktok,
             ['feed', 'community-scoped'], needs_url=True, default_type='meme'),
    Provider('youtube_shorts', 'YouTube Shorts search', 'query', 'Shorts for each curator query, saved locally so they play here as Moments (up to 3 minutes). Longer videos belong in a video category.', youtube_shorts,
             ['keyword-search', 'community-scoped'], default_type='meme'),
    Provider('lobsters', 'Lobste.rs stories', 'feed', 'Stories from the Lobste.rs technology community. Use https://lobste.rs/hottest.json or /newest.json.', lobsters,
             ['feed', 'structured-metadata'], needs_url=True, default_type='news'),
]}
