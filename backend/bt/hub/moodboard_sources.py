"""Bounded, attributed Wikimedia Commons image discovery for Mood Board curators.

The Action API supplies the original file's license and description-page identity.
GIF originals are kept because Commons thumbnails can flatten their animation.
"""
import json
import re
from html.parser import HTMLParser
from urllib.parse import urlencode, urlsplit

from . import social_fetch

API = 'https://commons.wikimedia.org/w/api.php'
MAX_RESPONSE_BYTES = 2_000_000
MAX_GIF_BYTES = 5_000_000
MIMES = {'image/jpeg', 'image/png', 'image/webp', 'image/gif'}
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


def search(query: str, limit: int = 30, offset: int = 0) -> list[dict]:
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
