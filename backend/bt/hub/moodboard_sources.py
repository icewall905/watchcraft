"""Bounded image discovery: web image search plus public social networks.

Web search is configured SearXNG with Wikimedia Commons fallback. Social sources (Reddit,
Bluesky, Mastodon; Flickr/Tumblr with an API key) use public read APIs only and run in
parallel with a per-source deadline. Results are interleaved and deduplicated; each keeps its
original post link, provider attribution and rights text. Captured raster pixels are judged by
the board curator. Instagram, TikTok, Facebook, X and Pinterest search have no public API and
are deliberately not scraped.
"""
import html
import json
import ipaddress
import time
import httpx
import re
from concurrent.futures import ThreadPoolExecutor, wait
from html.parser import HTMLParser
from urllib.parse import quote, urlencode, urlsplit

from .. import curation
from ..config import DEFAULT_SETTINGS
from ..db import get_settings
from . import social_fetch

API = 'https://commons.wikimedia.org/w/api.php'
MAX_RESPONSE_BYTES = 2_000_000
MAX_GIF_BYTES = 5_000_000
MIMES = {'image/jpeg', 'image/png', 'image/webp', 'image/gif'}
STOCK_PREVIEW_HOSTS = ('freepik.com', 'shutterstock.com', 'gettyimages.com', 'istockphoto.com',
                       'dreamstime.com', 'alamy.com', 'stock.adobe.com', 'vecteezy.com',
                       '123rf.com', 'depositphotos.com')
EXPLICIT = re.compile(r'\b(?:nud(?:e|es|ity)|porn(?:ography|ographic)?|eroti(?:c|ca)|sex(?:ual|ually)?|'
                      r'genitals?|genitalia|penis|penises|vagina|vulva|masturbat\w*|'
                      r'fetish(?:ism)?|intercourse|topless|naked)\b', re.I)


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
        if tag in ('br', 'p', 'div'):
            self.parts.append(' ')

    def handle_endtag(self, tag):
        if tag in ('script', 'style') and self.hidden:
            self.hidden -= 1
        self.parts.append(' ')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _text(value, limit=1000):
    parser = _Text()
    parser.feed(str(value or '')[:12000])
    return ' '.join(''.join(parser.parts).split())[:limit]


def _url(value, host, prefix):
    if not isinstance(value, str) or len(value) > 4096:
        return ''
    try:
        parsed = urlsplit(value)
        if (parsed.scheme == 'https' and parsed.hostname == host and parsed.port in (None, 443)
                and not parsed.username and not parsed.password and parsed.path.startswith(prefix)
                and not parsed.fragment and not any(c.isspace() for c in value)):
            # Commons appends API campaign tracking to file URLs. Files are
            # immutable path resources; discard queries rather than retain it.
            return parsed._replace(query='').geturl()
    except ValueError:
        pass
    return ''


def _positive(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def commons_search(query: str, limit: int = 30, offset: int = 0) -> list[dict]:
    """One public API request, at most 30 raster images, with safe plain attribution.

    Errors propagate to the board run so an unavailable source is distinguishable
    from an honest empty result. The shared public fetcher pins public DNS, bounds
    the response, validates redirects, and respects source Retry-After cooldowns.
    """
    if not isinstance(query, str) or not query.strip() or len(query) > 300:
        raise ValueError('Enter an interest or hobby of up to 300 characters.')
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError('The image limit must be positive.')
    if not isinstance(offset, int) or isinstance(offset, bool) or not 0 <= offset <= 10000:
        raise ValueError('The image search offset is out of range.')
    limit = min(limit, 30)
    params = {'action': 'query', 'format': 'json', 'formatversion': '2',
              'generator': 'search', 'gsrsearch': query.strip() + ' filetype:bitmap -nudity -pornography -erotic',
              'gsrnamespace': '6', 'gsrlimit': str(limit), 'gsroffset': str(offset), 'prop': 'imageinfo',
              'iiprop': 'url|mime|size|extmetadata', 'iiurlwidth': '1000',
              'iiextmetadatalanguage': 'en',
              'iiextmetadatafilter': 'Artist|Credit|Attribution|LicenseShortName|UsageTerms|ImageDescription|Categories'}
    raw = social_fetch.fetch(API + '?' + urlencode(params), kinds=('application/json',),
                             limit=MAX_RESPONSE_BYTES)
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get('error'):
        raise ValueError('Wikimedia Commons could not complete the image search. Try again later.')
    pages = payload.get('query', {}).get('pages', [])
    if not isinstance(pages, list):
        raise ValueError('Wikimedia Commons returned an unexpected image response.')
    results, seen = [], set()
    for page in sorted((p for p in pages if isinstance(p, dict)), key=lambda p: _positive(p.get('index')) or 999):
        infos = page.get('imageinfo')
        if not isinstance(infos, list) or not infos or not isinstance(infos[0], dict):
            continue
        info = infos[0]
        mime = info.get('mime')
        if mime not in MIMES:
            continue
        source = _url(info.get('descriptionurl'), 'commons.wikimedia.org', '/wiki/File:')
        original = _url(info.get('url'), 'upload.wikimedia.org', '/wikipedia/commons/')
        if not source or not original or source in seen:
            continue
        gif = mime == 'image/gif'
        if gif and (not _positive(info.get('size')) or info['size'] > MAX_GIF_BYTES):
            continue
        thumbnail = (_url(info.get('thumburl'), 'upload.wikimedia.org', '/wikipedia/commons/thumb/') or
                     _url(info.get('thumburl'), 'thumb.wikimedia.org', '/wikipedia/commons/thumb/'))
        image = original if gif else thumbnail or original
        meta = info.get('extmetadata') or {}
        if not isinstance(meta, dict):
            continue
        def metadata(key):
            field = meta.get(key)
            return _text(field.get('value')) if isinstance(field, dict) else ''
        # Commons is not a family-filtered source. Exclude explicit metadata;
        # this conservative text screen is not visual content classification.
        if EXPLICIT.search(str(page.get('title', '')) + ' ' + metadata('ImageDescription') +
                           ' ' + metadata('Categories').replace('_', ' ')):
            continue
        license_name = metadata('LicenseShortName') or metadata('UsageTerms')
        if not license_name:
            continue  # Never detach an image from its licensing information.
        attribution = metadata('Attribution') or metadata('Artist') or metadata('Credit') or 'Wikimedia Commons contributors'
        use_thumb = image == thumbnail and not gif
        width = _positive(info.get('thumbwidth' if use_thumb else 'width'))
        height = _positive(info.get('thumbheight' if use_thumb else 'height'))
        if not width or not height:
            continue
        title = _text(page.get('title', '').removeprefix('File:'), 300)
        if not title:
            continue
        results.append({'title': title, 'source_url': source, 'image_url': image,
                        'media_type': 'gif' if gif else 'image', 'width': width, 'height': height,
                        'attribution': attribution, 'license': license_name,
                        'description': metadata('ImageDescription')})
        seen.add(source)
        if len(results) >= limit:
            break
    return results


def _public_url(value):
    """Lexical gate only. Actual public DNS/redirect checks happen during local capture."""
    if not isinstance(value, str) or len(value) > 4096 or any(c.isspace() or ord(c) < 32 for c in value):
        return ''
    try:
        parsed = urlsplit(value)
        if parsed.fragment or '\\' in value:
            return ''
        normalized = curation.canonical(value)
        host = parsed.hostname.lower().rstrip('.')
        if ('.' not in host or host in ('localhost', 'localhost.localdomain') or
                host.endswith(('.localhost', '.local', '.internal', '.lan', '.home', '.test', '.invalid', '.onion'))):
            return ''
        try:
            if not ipaddress.ip_address(host).is_global:
                return ''
        except ValueError:
            # Bracketed non-IP hosts, malformed labels and numeric shorthand aren't public names.
            if ':' in host or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', host) or host.replace('.', '').isdigit():
                return ''
        return normalized
    except (ValueError, AttributeError):
        return ''


def valid_candidate(item):
    """Normalize source/image identity; public fetching separately pins and checks DNS."""
    if not isinstance(item, dict):
        return '', ''
    source, image = _public_url(item.get('source_url')), _public_url(item.get('image_url'))
    return (source, image) if source and image else ('', '')


def _validate_search(query, limit, offset):
    if not isinstance(query, str) or not query.strip() or len(query) > 300:
        raise ValueError('Enter an interest or hobby of up to 300 characters.')
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError('The image limit must be positive.')
    if not isinstance(offset, int) or isinstance(offset, bool) or not 0 <= offset <= 10000:
        raise ValueError('The image search offset is out of range.')
    return min(limit, 30)


def searx_search(query, limit=30, offset=0):
    """Configured admin-trusted SearXNG may be on LAN; result media may not."""
    limit = _validate_search(query, limit, offset)
    cfg = get_settings()['search']
    base = str(cfg.get('searxng_url') or '').rstrip('/')
    if cfg.get('provider') != 'searxng' or not base:
        raise ValueError('No image search provider is configured.')
    params = {'q': query.strip(), 'format': 'json', 'categories': 'images',
              'safesearch': 2, 'pageno': offset // 30 + 1}
    raw = bytearray()
    with httpx.stream('GET', base + '/search', params=params, timeout=25, follow_redirects=False,
                      headers={'User-Agent': 'WatchCraft/0.2', 'Accept': 'application/json'}) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes(65536):
            if len(raw) + len(chunk) > MAX_RESPONSE_BYTES:
                raise ValueError('Image search response too large.')
            raw.extend(chunk)
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get('error') or not isinstance(payload.get('results'), list):
        raise ValueError('Image search returned an unexpected response.')
    results, seen = [], set()
    for row in payload['results'][:300]:
        if not isinstance(row, dict):
            continue
        source, image = valid_candidate({'source_url': row.get('url'), 'image_url': row.get('img_src')})
        host = urlsplit(source).hostname or ''
        if any(host == stock or host.endswith('.' + stock) for stock in STOCK_PREVIEW_HOSTS):
            continue
        title, description = _text(row.get('title'), 500), _text(row.get('content'))
        if not source or source in seen or not title or EXPLICIT.search(title + ' ' + description):
            continue
        results.append({'title': title, 'description': description, 'source_url': source, 'image_url': image,
                        'media_type': 'gif' if urlsplit(image).path.lower().endswith('.gif') else 'image',
                        'attribution': 'Source: ' + urlsplit(source).hostname,
                        'license': 'Rights retained by original creators; reuse permission unknown'})
        seen.add(source)
        if len(results) >= limit:
            break
    return results


def web_search(query: str, limit: int = 30, offset: int = 0) -> list[dict]:
    """Configured web image search, with Commons fallback."""
    limit = _validate_search(query, limit, offset)
    cfg = get_settings()['search']
    error = None
    if cfg.get('provider') == 'searxng' and cfg.get('searxng_url'):
        try:
            results = searx_search(query, limit, offset)
            if results:
                return results
        except Exception as exc:
            error = exc
    try:
        fallback = commons_search(query, limit, offset)
    except Exception:
        if error:
            raise ValueError('Image search and Wikimedia Commons are unavailable. Try again later.') from None
        raise
    if error and not fallback:
        raise ValueError('Image search is unavailable and Wikimedia Commons found no matching images. Try again later.') from None
    return fallback


# ---------------------------------------------------------------------------
# Social networks: public read APIs only, image posts only, NSFW-flagged posts skipped.

RIGHTS = 'Rights retained by original creators; reuse permission unknown'
IMAGE_EXT = ('.jpg', '.jpeg', '.png', '.webp', '.gif')
NSFW_LABELS = {'porn', 'sexual', 'nudity', 'graphic-media', 'gore', 'nsfw'}
REDDIT_SORTS = (('relevance', 'year'), ('new', 'all'), ('top', 'month'), ('hot', 'all'), ('top', 'all'),
                ('relevance', 'all'))
_HOST = re.compile(r'^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$')
FLICKR_LICENSES = {'0': 'All rights reserved', '1': 'CC BY-NC-SA 2.0', '2': 'CC BY-NC 2.0', '3': 'CC BY-NC-ND 2.0',
                   '4': 'CC BY 2.0', '5': 'CC BY-SA 2.0', '6': 'CC BY-ND 2.0', '7': 'No known copyright restrictions',
                   '8': 'United States Government Work', '9': 'CC0 1.0', '10': 'Public Domain Mark 1.0'}


def _json(url):
    payload = json.loads(social_fetch.fetch(url, kinds=('application/json',), limit=MAX_RESPONSE_BYTES))
    if not isinstance(payload, dict):
        raise ValueError('Image source returned an unexpected response.')
    return payload


def _dim(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if 0 < number <= 100_000 else 0


def _candidate(provider, title, description, source, image, attribution, license_name=RIGHTS, width=0, height=0):
    """One normalized result, or None when it is unsafe, explicit or not an image."""
    source, image = valid_candidate({'source_url': html.unescape(str(source or '')),
                                     'image_url': html.unescape(str(image or ''))})
    description = _text(description)
    title = _text(title, 300) or description[:120] or provider + ' image'
    if not source or not image or EXPLICIT.search(title + ' ' + description):
        return None
    item = {'title': title, 'description': description, 'source_url': source, 'image_url': image,
            'media_type': 'gif' if urlsplit(image).path.lower().endswith('.gif') else 'image',
            'attribution': _text(attribution, 300), 'license': license_name, 'provider': provider}
    if _dim(width) and _dim(height):
        item.update(width=_dim(width), height=_dim(height))
    return item


def _tags(query):
    """Hashtag variants rotated by page: joined phrase first, then the meaningful single words."""
    words = [w for w in re.findall(r'[^\W_]+', query.lower()) if len(w) > 2]
    joined = ''.join(words)[:60]
    return list(dict.fromkeys(([joined] if joined else []) + [w[:60] for w in words if len(w) > 3]))


def reddit_search(query, limit=30, offset=0):
    """Public Reddit link search; single-image and gallery posts, SFW and non-spoiler only."""
    limit = _validate_search(query, limit, offset)
    sort, period = REDDIT_SORTS[(offset // 30) % len(REDDIT_SORTS)]
    params = {'q': query.strip(), 'type': 'link', 'sort': sort, 't': period, 'limit': '100',
              'include_over_18': 'off', 'raw_json': '1'}
    data = _json('https://www.reddit.com/search.json?' + urlencode(params)).get('data')
    children = data.get('children') if isinstance(data, dict) else None
    if not isinstance(children, list):
        raise ValueError('Reddit returned an unexpected response.')
    results = []
    for child in children[:100]:
        post = child.get('data') if isinstance(child, dict) else None
        if not isinstance(post, dict) or post.get('over_18') is not False or post.get('spoiler') or \
                post.get('is_video') or post.get('removed_by_category'):
            continue
        permalink = post.get('permalink')
        if not isinstance(permalink, str) or not permalink.startswith('/r/'):
            continue
        image, width, height = '', 0, 0
        if post.get('is_gallery'):
            meta = post.get('media_metadata') if isinstance(post.get('media_metadata'), dict) else {}
            order = (post.get('gallery_data') or {}).get('items') if isinstance(post.get('gallery_data'), dict) else None
            for entry in (order if isinstance(order, list) else []):
                media = meta.get(entry.get('media_id')) if isinstance(entry, dict) else None
                biggest = media.get('s') if isinstance(media, dict) and media.get('status') == 'valid' and \
                    media.get('e') in ('Image', 'AnimatedImage') else None
                if isinstance(biggest, dict) and (biggest.get('gif') or biggest.get('u')):
                    image, width, height = biggest.get('gif') or biggest.get('u'), biggest.get('x'), biggest.get('y')
                    break
        else:
            url = str(post.get('url_overridden_by_dest') or post.get('url') or '')
            parts = urlsplit(url)
            if parts.hostname in ('i.redd.it', 'i.imgur.com') and parts.path.lower().endswith(IMAGE_EXT):
                image = url
                preview = (post.get('preview') or {}).get('images') if isinstance(post.get('preview'), dict) else None
                source_size = preview[0].get('source') if isinstance(preview, list) and preview and \
                    isinstance(preview[0], dict) else None
                if isinstance(source_size, dict):
                    width, height = source_size.get('width'), source_size.get('height')
        if not image:
            continue
        author = post.get('author') if isinstance(post.get('author'), str) else ''
        sub = post.get('subreddit_name_prefixed') if isinstance(post.get('subreddit_name_prefixed'), str) else 'Reddit'
        item = _candidate('reddit', post.get('title'), post.get('selftext'), 'https://www.reddit.com' + permalink,
                          image, (f'u/{author} in ' if author else '') + sub + ' (Reddit)', RIGHTS, width, height)
        if item:
            results.append(item)
        if len(results) >= limit:
            break
    return results


def bluesky_search(query, limit=30, offset=0):
    """Public Bluesky AppView post search; image embeds only, moderation-labelled posts skipped."""
    limit = _validate_search(query, limit, offset)
    params = {'q': query.strip(), 'limit': '100', 'sort': 'top' if (offset // 30) % 2 == 0 else 'latest'}
    if offset >= 60:
        params['cursor'] = str((offset // 60) * 100)
    posts = _json('https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts?' + urlencode(params)).get('posts')
    if not isinstance(posts, list):
        raise ValueError('Bluesky returned an unexpected response.')
    results = []
    for post in posts[:100]:
        if not isinstance(post, dict):
            continue
        labels = [l.get('val') for l in post.get('labels') or [] if isinstance(l, dict)]
        author = post.get('author') if isinstance(post.get('author'), dict) else {}
        labels += [l.get('val') for l in author.get('labels') or [] if isinstance(l, dict)]
        if NSFW_LABELS.intersection(labels):
            continue
        embed = post.get('embed') if isinstance(post.get('embed'), dict) else {}
        if embed.get('$type') == 'app.bsky.embed.recordWithMedia#view':
            embed = embed.get('media') if isinstance(embed.get('media'), dict) else {}
        images = embed.get('images') if embed.get('$type') == 'app.bsky.embed.images#view' else None
        uri, handle = str(post.get('uri') or ''), str(author.get('handle') or '')
        rkey = uri.rsplit('/', 1)[-1]
        if not isinstance(images, list) or not images or not isinstance(images[0], dict) or \
                not uri.startswith('at://') or '/app.bsky.feed.post/' not in uri or \
                not re.fullmatch(r'[A-Za-z0-9._:~-]{1,512}', rkey) or not _BSKY_HANDLE.match(handle):
            continue
        record = post.get('record') if isinstance(post.get('record'), dict) else {}
        first = images[0]
        ratio = first.get('aspectRatio') if isinstance(first.get('aspectRatio'), dict) else {}
        text = record.get('text') if isinstance(record.get('text'), str) else ''
        item = _candidate('bluesky', text[:200], ' '.join(filter(None, [text, first.get('alt') if isinstance(first.get('alt'), str) else ''])),
                          f'https://bsky.app/profile/{handle}/post/{rkey}', first.get('fullsize') or first.get('thumb'),
                          '@' + handle + ' (Bluesky)', RIGHTS, ratio.get('width'), ratio.get('height'))
        if item:
            results.append(item)
        if len(results) >= limit:
            break
    return results


_BSKY_HANDLE = re.compile(r'^[a-z0-9][a-z0-9.-]{1,252}\.[a-z]{2,63}$', re.I)


def _mastodon_max_id(page):
    """Snowflake IDs are (ms << 16): later pages step back in time to keep finding new posts."""
    return str(((int(time.time() * 1000) - page * 3 * 86_400_000) << 16)) if page else ''


def mastodon_search(query, limit=30, offset=0, instance='mastodon.social'):
    """Public hashtag timeline on one configured instance; media statuses only, sensitive skipped."""
    limit = _validate_search(query, limit, offset)
    instance = str(instance or '').strip().lower()
    if not _HOST.match(instance):
        raise ValueError('Mastodon instance must be a host name like mastodon.social.')
    tags = _tags(query)
    if not tags:
        return []
    page = offset // 30
    tag = tags[page % len(tags)]
    params = {'only_media': 'true', 'limit': '40'}
    if max_id := _mastodon_max_id(page // len(tags)):
        params['max_id'] = max_id
    statuses = social_fetch.fetch(f'https://{instance}/api/v1/timelines/tag/{quote(tag, safe="")}?' + urlencode(params),
                                  kinds=('application/json',), limit=MAX_RESPONSE_BYTES)
    statuses = json.loads(statuses)
    if not isinstance(statuses, list):
        raise ValueError('Mastodon returned an unexpected response.')
    results = []
    for status in statuses[:40]:
        if not isinstance(status, dict):
            continue
        status = status.get('reblog') if isinstance(status.get('reblog'), dict) else status
        if status.get('sensitive') or status.get('spoiler_text') or status.get('visibility') not in ('public', None):
            continue
        account = status.get('account') if isinstance(status.get('account'), dict) else {}
        media = next((m for m in status.get('media_attachments') or []
                      if isinstance(m, dict) and m.get('type') in ('image', 'gifv')), None)
        if not media or media.get('type') != 'image':
            continue  # gifv is MP4 video; only raster images are captured.
        original = ((media.get('meta') or {}).get('original') or {}) if isinstance(media.get('meta'), dict) else {}
        content = _text(status.get('content'))
        item = _candidate('mastodon', content[:200], ' '.join(filter(None, [content, media.get('description') or ''])),
                          status.get('url') or status.get('uri'), media.get('url'),
                          '@' + str(account.get('acct') or 'unknown') + ' (Mastodon)', RIGHTS,
                          original.get('width') if isinstance(original, dict) else 0,
                          original.get('height') if isinstance(original, dict) else 0)
        if item:
            results.append(item)
        if len(results) >= limit:
            break
    return results


def flickr_search(query, limit=30, offset=0, api_key=''):
    """Flickr photo search (needs an API key); safe search on, license shown per photo."""
    limit = _validate_search(query, limit, offset)
    if not api_key:
        return []
    params = {'method': 'flickr.photos.search', 'api_key': api_key, 'text': query.strip(), 'safe_search': '1',
              'content_types': '0', 'media': 'photos', 'sort': 'relevance', 'per_page': '50',
              'page': str(offset // 30 + 1), 'extras': 'url_l,url_c,owner_name,license,description',
              'format': 'json', 'nojsoncallback': '1'}
    payload = _json('https://api.flickr.com/services/rest/?' + urlencode(params))
    photos = (payload.get('photos') or {}).get('photo') if isinstance(payload.get('photos'), dict) else None
    if payload.get('stat') != 'ok' or not isinstance(photos, list):
        raise ValueError('Flickr could not complete the image search.')
    results = []
    for photo in photos[:50]:
        if not isinstance(photo, dict) or not re.fullmatch(r'\d{1,20}', str(photo.get('id') or '')) or \
                not re.fullmatch(r'[\w@-]{1,64}', str(photo.get('owner') or '')):
            continue
        size = 'l' if photo.get('url_l') else 'c'
        description = photo.get('description', {}).get('_content') if isinstance(photo.get('description'), dict) else ''
        item = _candidate('flickr', photo.get('title'), description,
                          f'https://www.flickr.com/photos/{photo["owner"]}/{photo["id"]}', photo.get('url_' + size),
                          str(photo.get('ownername') or photo['owner']) + ' (Flickr)',
                          FLICKR_LICENSES.get(str(photo.get('license')), RIGHTS),
                          photo.get('width_' + size), photo.get('height_' + size))
        if item:
            results.append(item)
        if len(results) >= limit:
            break
    return results


def tumblr_search(query, limit=30, offset=0, api_key=''):
    """Tumblr tagged photo posts (needs an API consumer key); older pages step back in time."""
    limit = _validate_search(query, limit, offset)
    tags = _tags(query)
    if not api_key or not tags:
        return []
    page = offset // 30
    params = {'tag': tags[page % len(tags)], 'api_key': api_key, 'limit': '20', 'filter': 'text'}
    if step := page // len(tags):
        params['before'] = str(int(time.time()) - step * 7 * 86_400)
    posts = _json('https://api.tumblr.com/v2/tagged?' + urlencode(params)).get('response')
    if not isinstance(posts, list):
        raise ValueError('Tumblr returned an unexpected response.')
    results = []
    for post in posts[:20]:
        if not isinstance(post, dict) or post.get('type') != 'photo' or post.get('is_nsfw') or \
                post.get('classification') == 'nsfw':
            continue
        photos = post.get('photos')
        original = photos[0].get('original_size') if isinstance(photos, list) and photos and \
            isinstance(photos[0], dict) else None
        if not isinstance(original, dict):
            continue
        tags_text = ' '.join(t for t in post.get('tags') or [] if isinstance(t, str))
        item = _candidate('tumblr', post.get('summary'), ' '.join(filter(None, [str(post.get('caption') or ''), tags_text])),
                          post.get('post_url'), original.get('url'), str(post.get('blog_name') or 'unknown') + ' (Tumblr)',
                          RIGHTS, original.get('width'), original.get('height'))
        if item:
            results.append(item)
        if len(results) >= limit:
            break
    return results


def source_settings():
    defaults = DEFAULT_SETTINGS['moodboard_sources']
    cfg = get_settings().get('moodboard_sources')
    return {**defaults, **(cfg if isinstance(cfg, dict) else {})}


def enabled_sources(cfg=None):
    """(name, callable) per enabled provider; key-based providers are skipped silently without a key."""
    cfg = cfg or source_settings()
    sources = []
    if cfg.get('web', True):
        sources.append(('web', web_search))
    if cfg.get('reddit'):
        sources.append(('reddit', reddit_search))
    if cfg.get('bluesky'):
        sources.append(('bluesky', bluesky_search))
    if cfg.get('mastodon') and cfg.get('mastodon_instance'):
        sources.append(('mastodon', lambda q, l, o: mastodon_search(q, l, o, cfg['mastodon_instance'])))
    if cfg.get('flickr_api_key'):
        sources.append(('flickr', lambda q, l, o: flickr_search(q, l, o, cfg['flickr_api_key'])))
    if cfg.get('tumblr_api_key'):
        sources.append(('tumblr', lambda q, l, o: tumblr_search(q, l, o, cfg['tumblr_api_key'])))
    return sources


def _image_key(url):
    parts = urlsplit(url)
    # Reddit previews carry per-request signatures; the path identifies the image.
    return (parts.hostname or '') + parts.path if parts.hostname in ('preview.redd.it', 'i.redd.it') else url


def search(query: str, limit: int = 30, offset: int = 0) -> list[dict]:
    """All enabled sources in parallel, each bounded by a deadline; results interleaved and deduped.

    A slow or failing source is dropped for this call. Only when every source fails is an error raised,
    so the board run can still tell an outage from an honest empty result.
    """
    limit = _validate_search(query, limit, offset)
    cfg = source_settings()
    sources = enabled_sources(cfg)
    if not sources:
        raise ValueError('No Mood Board image sources are enabled.')
    deadline = max(5, min(int(cfg.get('source_timeout_s') or 20), 60))
    pool = ThreadPoolExecutor(max_workers=len(sources), thread_name_prefix='moodboard-source')
    try:
        futures = {pool.submit(fn, query, limit, offset): name for name, fn in sources}
        done, _late = wait(futures, timeout=deadline)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)  # Never block the run on a dead source.
    lists, errors = [], []
    for future, name in futures.items():  # Submission order keeps interleaving deterministic.
        if future not in done:
            errors.append(TimeoutError(name + ' timed out'))
            continue
        try:
            found = future.result()
            if not isinstance(found, list):
                raise ValueError(name + ' returned an unexpected response.')
        except Exception as exc:
            errors.append(exc)
            continue
        lists.append([r for r in found if isinstance(r, dict)])
    if errors and not lists:
        if len(errors) == 1 and isinstance(errors[0], ValueError):
            raise errors[0]  # Keep a lone source's own message and retry metadata.
        raise ValueError('Image sources are unavailable. Try again later.')
    results, seen_sources, seen_images = [], set(), set()
    for row in range(max((len(found) for found in lists), default=0)):
        for found in lists:
            if row >= len(found):
                continue
            item = found[row]
            source, image = valid_candidate(item)
            if not source or not image or source in seen_sources or _image_key(image) in seen_images:
                continue
            seen_sources.add(source)
            seen_images.add(_image_key(image))
            results.append(item)
            if len(results) >= limit:
                return results
    return results
