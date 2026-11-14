import json
from urllib.parse import parse_qs, urlsplit

import pytest

from bt.hub import moodboard_sources as sources


def page(**changes):
    info = {'url': 'https://upload.wikimedia.org/wikipedia/commons/a/ab/Garden.jpg',
            'thumburl': 'https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Garden.jpg/1000px-Garden.jpg',
            'descriptionurl': 'https://commons.wikimedia.org/wiki/File:Garden.jpg',
            'mime': 'image/jpeg', 'size': 12345, 'width': 2000, 'height': 1200,
            'thumbwidth': 1000, 'thumbheight': 600,
            'extmetadata': {'Artist': {'value': '<a>A &amp; B</a><script>bad()</script>'},
                            'LicenseShortName': {'value': 'CC BY-SA 4.0'}}}
    info.update(changes)
    return {'index': 1, 'title': 'File:Garden.jpg', 'imageinfo': [info]}


def response(monkeypatch, pages):
    calls = []
    def fetch(url, **kwargs):
        calls.append((url, kwargs))
        return json.dumps({'query': {'pages': pages}}).encode()
    monkeypatch.setattr(sources.social_fetch, 'fetch', fetch)
    return calls


def test_bounded_attributed_public_search(monkeypatch):
    calls = response(monkeypatch, [page(), page()])
    result = sources.search('Japanese garden', 200, offset=30)
    assert len(calls) == 1
    params = parse_qs(urlsplit(calls[0][0]).query)
    assert params['gsrlimit'] == ['30'] and params['gsroffset'] == ['30']
    assert params['gsrnamespace'] == ['6']
    assert params['gsrsearch'] == ['Japanese garden filetype:bitmap -nudity -pornography -erotic']
    assert calls[0][1] == {'kinds': ('application/json',), 'limit': 2_000_000}
    assert len(result) == 1
    assert result[0]['attribution'] == 'A & B'
    assert result[0]['license'] == 'CC BY-SA 4.0'
    assert result[0]['width'] == 1000 and result[0]['height'] == 600
    assert result[0]['media_type'] == 'image' and '/thumb/' in result[0]['image_url']


def test_gif_original_preserves_animation(monkeypatch):
    response(monkeypatch, [page(mime='image/gif', url='https://upload.wikimedia.org/wikipedia/commons/a/ab/Garden.gif')])
    result = sources.search('cat animation')[0]
    assert result['media_type'] == 'gif' and result['image_url'].endswith('.gif')
    assert result['width'] == 2000


@pytest.mark.parametrize('changes', [
    {'mime': 'image/svg+xml'}, {'mime': 'video/webm'},
    {'url': 'https://upload.wikimedia.org.evil.example/wikipedia/commons/a.jpg'},
    {'url': 'http://upload.wikimedia.org/wikipedia/commons/a.jpg'},
    {'url': 'https://user@upload.wikimedia.org/wikipedia/commons/a.jpg'},
    {'url': 'https://upload.wikimedia.org:8443/wikipedia/commons/a.jpg'},
    {'url': 'https://upload.wikimedia.org/other/a.jpg'},
    {'descriptionurl': 'https://commons.wikimedia.org/wiki/Special:Redirect/file/a.jpg'},
    {'descriptionurl': 'https://evil.example/wiki/File:a.jpg'},
    {'extmetadata': {}}, {'width': 0, 'thumbwidth': 0},
    {'mime': 'image/gif', 'size': 5_000_001}, {'mime': 'image/gif', 'size': 0},
])
def test_excludes_unsafe_non_image_unlicensed(monkeypatch, changes):
    response(monkeypatch, [page(**changes)])
    assert sources.search('gardens') == []


def test_relevance_and_limit(monkeypatch):
    later = page(descriptionurl='https://commons.wikimedia.org/wiki/File:Other.jpg')
    later['index'] = 2
    response(monkeypatch, [later, page()])
    assert sources.search('gardens', 1)[0]['source_url'].endswith('Garden.jpg')


@pytest.mark.parametrize('query,limit,offset', [('', 1, 0), ('x'*301, 1, 0), ('garden', 0, 0),
    ('garden', True, 0), ('garden', 1, -1), ('garden', 1, 10001)])
def test_invalid_input_no_fetch(monkeypatch, query, limit, offset):
    calls = response(monkeypatch, [])
    with pytest.raises(ValueError):
        sources.search(query, limit, offset)
    assert calls == []


def test_empty_and_failure_distinct(monkeypatch):
    response(monkeypatch, [])
    assert sources.search('unusual hobby') == []
    monkeypatch.setattr(sources.social_fetch, 'fetch', lambda *a, **k: b'{"error":{"code":"maxlag"}}')
    with pytest.raises(ValueError, match='could not complete'):
        sources.search('gardens')


def test_rate_limit_propagates(monkeypatch):
    from bt.curation import FetchError
    def fail(*args, **kwargs):
        raise FetchError(429, '120')
    monkeypatch.setattr(sources.social_fetch, 'fetch', fail)
    with pytest.raises(FetchError) as exc:
        sources.search('gardens')
    assert exc.value.retry_after == '120'


@pytest.mark.parametrize('field,value', [('ImageDescription', 'Erotic portrait'),
    ('Categories', 'Gardens|Nude_people'), ('ImageDescription', '<p>Explicit sexual image</p>')])
def test_excludes_explicit_metadata(monkeypatch, field, value):
    item = page()
    item['imageinfo'][0]['extmetadata'][field] = {'value': value}
    response(monkeypatch, [item])
    assert sources.search('art') == []


def test_excludes_explicit_title(monkeypatch):
    item = page()
    item['title'] = 'File:Naked portrait.jpg'
    response(monkeypatch, [item])
    assert sources.search('art') == []


def test_current_commons_thumbnail_host_and_tracking_queries(monkeypatch):
    response(monkeypatch, [page(
        url='https://upload.wikimedia.org/wikipedia/commons/a/ab/Garden.jpg?utm_content=original',
        thumburl='https://thumb.wikimedia.org/wikipedia/commons/thumb/a/ab/Garden.jpg/1280px-Garden.jpg?utm_campaign=imageinfo')])
    result = sources.search('gardens')[0]
    assert result['image_url'] == 'https://thumb.wikimedia.org/wikipedia/commons/thumb/a/ab/Garden.jpg/1280px-Garden.jpg'
