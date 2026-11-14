"""Bounded public Lemmy community discovery through the existing hardened fetcher.

No instance-wide crawl, login, remote-community resolution or comment collection.
One site capability request and one page of posts per poll; RSS is an explicit
fallback for unsupported API versions, never for denied/rate-limited requests.
"""
import json
import re
import time
from urllib.parse import quote, unquote, urlencode, urlsplit

from .. import curation
from . import social_fetch
from .extract import TEXT_LIMIT, clean, safe_image, strip_tags, ts


def community_source(source: str) -> tuple[str, str]:
    source = (source or '').strip()
    if source.startswith('!'):
        match = re.fullmatch(r'!([A-Za-z0-9_]+)@([A-Za-z0-9.-]+)', source)
        if not match:
            raise ValueError('Use !community@instance or an HTTPS Lemmy community URL.')
        source = f'https://{match[2]}/c/{match[1]}'
    raw = urlsplit(source)
    if raw.query or raw.fragment:
        raise ValueError('Lemmy community URLs must not include query parameters or fragments.')
    p = urlsplit(curation.canonical(source))
    match = re.fullmatch(r'/c/([A-Za-z0-9_]+(?:@[A-Za-z0-9.-]+)?)/?', unquote(p.path))
    if not match or p.query or p.fragment:
        raise ValueError('Use an HTTPS Lemmy community URL such as https://lemmy.world/c/selfhosted.')
    return f'https://{p.hostname}', match[1]


def normalize_source_url(source: str) -> str:
    base, community = community_source(source)
    return f'{base}/c/{community}'


def _json(url: str) -> dict:
    data = json.loads(social_fetch.fetch(url, kinds=('application/json',)))
    if not isinstance(data, dict):
        raise ValueError('Invalid Lemmy API response.')
    return data


def _unsupported(error: Exception) -> bool:
    # Do not turn access failures, rate limits or transient errors into more calls.
    status = getattr(error, 'status', None)
    return status in (404, 405) if status is not None else bool(re.search(r'HTTP (?:404|405)\b', str(error)))


def _picture(url: str) -> bool:
    """A direct image link (pict-rs uploads and common image files): the meme itself."""
    path = urlsplit(url).path.lower()
    return bool(url) and ('/pictrs/image/' in path or path.endswith(('.jpg', '.jpeg', '.png', '.webp', '.gif')))


def _post(view: dict, instance: str, selected: str, item_type: str) -> dict | None:
    if not isinstance(view, dict):
        return None
    post, community, creator = (view.get(key) for key in ('post', 'community', 'creator'))
    if not all(isinstance(value, dict) for value in (post, community, creator)):
        return None
    if any(obj.get('deleted') or obj.get('removed') or obj.get('local_removed') for obj in (post, community)):
        return None
    if post.get('nsfw') or community.get('nsfw') or str(community.get('visibility', 'public')).lower() != 'public':
        return None
    # An instance response must not silently widen the selected community.
    if str(community.get('name', '')).casefold() != selected.split('@')[0].casefold():
        return None
    ap_id = safe_image(post.get('ap_id') or post.get('activitypub_id') or '')
    title = clean(post.get('name'), 500)
    if not ap_id or not title:
        return None
    body = strip_tags(post.get('body') or '', TEXT_LIMIT)
    article = safe_image(post.get('url') or '')
    if item_type == 'meme' and not _picture(article):
        return None  # a meme community's text and link posts are not memes
    counts = view.get('counts') if isinstance(view.get('counts'), dict) else {}
    community_id = safe_image(community.get('actor_id') or community.get('ap_id') or '')
    origin = urlsplit(community_id).hostname or instance
    expected_origin = selected.split('@', 1)[1] if '@' in selected else instance
    if expected_origin.casefold() != origin.casefold():
        return None
    observations = {k: v for k in ('score', 'upvotes', 'downvotes', 'comments')
                    if isinstance((v := counts.get(k, post.get(k))), (int, float)) and not isinstance(v, bool)}
    return {
        'url': ap_id, 'external_id': ap_id, 'provider': 'lemmy', 'type': item_type,
        'title': title, 'summary': clean(body or post.get('alt_text') or title, 1000), 'body': body,
        'publisher': f"!{community.get('name')}@{origin}", 'author': clean(creator.get('display_name') or creator.get('name'), 200),
        'published_at': ts(post.get('published') or post.get('published_at')),
        'image': safe_image(post.get('thumbnail_url') or '') or (article if item_type == 'meme' and _picture(article) else ''),
        'meta': {'social_post': True, 'platform': 'lemmy', 'instance': instance, 'community': f"!{community.get('name')}@{origin}",
                 'community_url': community_id, 'discussion_url': ap_id, 'article_url': article,
                 'sensitive': False, 'observations': observations,
                 'updated_at': ts(post.get('updated') or post.get('updated_at')),
                 'coverage': 'Selected public community; popularity is an instance observation.',
                 **({'media': {'type': 'image', 'url': article, 'preview_url': article, 'alt': title}}
                    if item_type == 'meme' and _picture(article) else {})},
    }


def lemmy(source: str, q: str = '', config: dict | None = None) -> list[dict]:
    """Read one public community. Curator queries never broaden its boundary."""
    base, community = community_source(source)
    config = config or {}
    sort = config.get('sort', 'Hot')
    if sort not in ('Hot', 'Scaled', 'TopDay', 'New'):
        raise ValueError('Lemmy sort must be Hot, Scaled, TopDay or New.')
    item_type = config.get('item_type', 'news')
    if item_type not in ('news', 'article', 'meme', 'web'):
        raise ValueError('Lemmy item type must be news, article, meme or web.')
    limit = config.get('limit', 20)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 30:
        raise ValueError('Lemmy limit must be between 1 and 30.')
    version = None
    for candidate in (3, 4):
        try:
            site = _json(f'{base}/api/v{candidate}/site')
            if not isinstance(site.get('version'), str):
                raise ValueError('Lemmy site response has no version.')
            version = candidate
            break
        except ValueError as error:
            if not _unsupported(error):
                raise
    if version is None:
        if config.get('allow_rss') is not True:
            raise ValueError('Lemmy API unavailable. Enable RSS fallback only for a trusted public, safe community; RSS cannot verify moderation or NSFW flags.')
        from .providers import parse_feed
        feed = f'{base}/feeds/c/{quote(community, safe="@")}.xml' + '?' + urlencode({'sort': sort})
        out = []
        for item in parse_feed(social_fetch.fetch(feed), feed)[:limit]:
            discussion = safe_image(item.get('url') or '')
            if not discussion or (item_type == 'meme' and not _picture(item.get('image') or '')):
                continue
            item.update(provider='lemmy', type=item_type, external_id=discussion)
            item['meta'].update(platform='lemmy', instance=urlsplit(base).hostname,
                                community=community, discussion_url=discussion, social_post=True,
                                ranking=sort, observed_at=time.time(),
                                coverage='Public community RSS; moderation and NSFW metadata unavailable.',
                                rss_fallback=True)
            out.append(item)
        return out
    params = {'community_name': community, 'sort': sort, 'limit': limit, 'show_nsfw': 'false'}
    if version == 4:
        params['sort'] = 'top' if sort == 'TopDay' else sort.lower()
        if sort == 'TopDay':
            params['time_range_seconds'] = 86400
    response = _json(f'{base}/api/v{version}/post/list?{urlencode(params)}')
    posts = response.get('items' if version == 4 else 'posts')
    if not isinstance(posts, list):
        raise ValueError('Lemmy response has no posts list.')
    out, seen = [], set()
    for view in posts[:limit]:
        item = _post(view, urlsplit(base).hostname, community, item_type)
        if item and item['external_id'] not in seen:
            item['meta'].update(ranking=sort, observed_at=time.time())
            seen.add(item['external_id'])
            out.append(item)
    return out
