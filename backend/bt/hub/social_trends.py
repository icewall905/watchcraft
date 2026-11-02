"""Bounded, anonymous Mastodon trends observations from an explicitly chosen instance.

A trend is an instance's sampled ranking, never verification of a post or article's claims.
All network requests use the shared public-address-only fetcher; no credentials or pagination.
"""
import json
import re
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

from .. import curation
from .extract import clean, safe_image
from . import social_fetch

ENDPOINTS = {'/api/v1/trends/links': 'links', '/api/v1/trends/statuses': 'statuses',
             '/api/v1/trends/tags': 'tags'}
MAX_ITEMS = 20
MAX_TAGS = 3


def validate_config(config: dict | None = None) -> dict:
    if config is not None and not isinstance(config, dict):
        raise ValueError('Mastodon trends configuration must be an object.')
    config = config or {}
    item_type = config.get('item_type', 'news')
    if item_type not in ('news', 'meme', 'article', 'web'):
        raise ValueError('Mastodon trends item type must be news, meme, article or web.')
    sensitive = config.get('include_sensitive', False)
    if not isinstance(sensitive, bool):
        raise ValueError('include_sensitive must be a boolean.')
    languages = config.get('languages', [])
    if (not isinstance(languages, list) or len(languages) > 10
            or any(not isinstance(lang, str) or not re.fullmatch(r'[a-z]{2,3}(?:-[A-Za-z]{2,4})?', lang) for lang in languages)):
        raise ValueError('languages must be a list of at most 10 language codes, such as en or da.')
    return {'item_type': item_type, 'include_sensitive': sensitive,
            'languages': list(dict.fromkeys(lang.lower() for lang in languages))}


def _source(url: str) -> tuple[str, str]:
    p = urlsplit(url)
    if (p.scheme != 'https' or not p.hostname or p.username or p.password or p.query or p.fragment
            or p.path.rstrip('/') not in ENDPOINTS):
        raise ValueError('Choose an explicit HTTPS Mastodon trends endpoint: /api/v1/trends/links, /statuses or /tags.')
    # canonical performs the same URL normalization used for all other public sources.
    normalized = urlsplit(curation.canonical(url))
    return f'https://{normalized.netloc}', ENDPOINTS[p.path.rstrip('/')]


def _read(url: str) -> list:
    data = json.loads(social_fetch.fetch(url, kinds=('application/json',), limit=2_000_000))
    if not isinstance(data, list):
        raise ValueError('Unexpected Mastodon trends response; choose an instance allowing anonymous public trends.')
    return data


def _language_allowed(value, languages: list[str]) -> bool:
    return not languages or (isinstance(value, str) and value.lower() in languages)


def _observation(instance: str, endpoint: str) -> dict:
    return {'platform': 'mastodon', 'instance': instance, 'origin': instance,
            'coverage': 'Sampled public trends from this instance, not the whole fediverse.',
            'evidence': 'Observed instance trend; underlying claims are not verified.',
            'trend_endpoint': endpoint, 'observed_at': datetime.now(timezone.utc).isoformat()}


def mastodon_trends(url: str, q: str = '', config: dict | None = None) -> list[dict]:
    """Provider interface: (source URL, ignored curator query, optional configuration) → items.

    At most four fetches: one trends list and up to three tag timelines, with 20 items each.
    Status identity follows its originating URI, so remote instance-local numeric IDs cannot collide.
    """
    from .providers import _mastodon_status  # registry imports this adapter; avoid an import cycle

    cfg = validate_config(config)
    origin, kind = _source(url)
    instance = urlsplit(origin).hostname
    endpoint = f'{origin}/api/v1/trends/{kind}'
    data = _read(f'{endpoint}?limit={MAX_ITEMS}')
    statuses = []
    if kind == 'tags':
        seen_tags = set()
        for tag in data[:MAX_ITEMS]:
            name = tag.get('name') if isinstance(tag, dict) else None
            if not isinstance(name, str) or not re.fullmatch(r'\w{1,100}', name, re.UNICODE):
                continue
            if name.casefold() in seen_tags:
                continue
            seen_tags.add(name.casefold())
            statuses.extend(_read(f'{origin}/api/v1/timelines/tag/{quote(name, safe="")}?limit={MAX_ITEMS}')[:MAX_ITEMS])
            if len(seen_tags) >= MAX_TAGS:
                break
    elif kind == 'statuses':
        statuses = data[:MAX_ITEMS]
    out, seen = [], set()
    if kind == 'links':
        for link in data[:MAX_ITEMS]:
            if (not isinstance(link, dict) or (link.get('sensitive') and not cfg['include_sensitive'])
                    or not _language_allowed(link.get('language'), cfg['languages'])):
                continue
            target = safe_image(link.get('url'))
            if not target or not target.startswith('https://'):
                continue
            try:
                target = curation.canonical(target)
            except ValueError:
                continue
            if target in seen:
                continue
            title = clean(link.get('title'), 500)
            if not title:
                continue
            seen.add(target)
            out.append({'url': target, 'type': cfg['item_type'], 'provider': 'mastodon_trends',
                        'external_id': f'mstdn-trend-link:{target}', 'title': title,
                        'summary': clean(link.get('description'), 1000), 'body': '',
                        'publisher': clean(link.get('provider_name'), 200),
                        'author': clean(link.get('author_name'), 200),
                        'image': safe_image(link.get('image')),
                        'meta': {**_observation(instance, endpoint), 'trend_kind': kind,
                                 'article_url': target, 'origin': urlsplit(target).hostname, 'social_post': False,
                                 'observations': {'history': link.get('history', [])[:7]
                                     if isinstance(link.get('history'), list) else []}}})
        return out
    for status in statuses:
        if (not isinstance(status, dict) or ((status.get('sensitive') or status.get('spoiler_text')) and not cfg['include_sensitive'])
                or not _language_allowed(status.get('language'), cfg['languages'])):
            continue
        item = _mastodon_status(status, instance)
        if not item:
            continue
        uri = safe_image(status.get('uri')) or item['url']
        try:
            uri = curation.canonical(uri)
        except ValueError:
            continue
        if uri in seen:
            continue
        seen.add(uri)
        item['meta'].setdefault('display_url', item['url'])
        item.update(url=uri, type=cfg['item_type'], provider='mastodon_trends', external_id=f'mstdn:{uri}')
        item['meta'].update(_observation(instance, endpoint), trend_kind=kind, origin_uri=uri,
                            origin=urlsplit(uri).hostname)
        out.append(item)
    return out
