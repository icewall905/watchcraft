import gzip
import json

import pytest

from bt.commonroom import adapters
from bt.commonroom.adapters import discourse, devto, hackernews, lobsters, stackexchange, tildes
from bt.hub import social_fetch


class R(dict):
    calls: list


@pytest.fixture
def net(monkeypatch):
    routes, calls = R(), []

    def fake(url, **kw):
        calls.append(url)
        for prefix, body in routes.items():
            if url.startswith(prefix):
                return body if isinstance(body, bytes) else json.dumps(body).encode()
        raise AssertionError(f'unexpected fetch {url}')
    monkeypatch.setattr(social_fetch, 'fetch', fake)
    monkeypatch.setattr(stackexchange, '_backoff_until', 0.0)
    monkeypatch.setattr(stackexchange, '_key', lambda: '')
    routes.calls = calls
    return routes


def test_registered():
    for k in ('hackernews', 'lobsters', 'devto', 'tildes', 'stackexchange', 'discourse'):
        assert k in adapters.ADAPTERS


def test_hackernews(monkeypatch):
    urls = []
    hits = {'hits': [{'objectID': '1', 'title': 'Hi', 'url': 'https://e.com/a', 'points': 5, 'num_comments': 2,
                      'author': 'bob', 'created_at_i': 100},
                     {'objectID': '2', 'title': 'Ask HN: x', 'url': None, 'story_text': '<p>body &amp; more</p>',
                      'points': 1, 'num_comments': 0, 'author': 'al', 'created_at_i': 90},
                     {'objectID': 'x', 'title': 'bad'}], 'nbPages': 3}
    item = {'children': [{'id': 10, 'author': 'c', 'text': '<p>top</p>', 'points': 1, 'created_at_i': 5,
                          'children': [{'id': 11, 'author': 'd', 'text': 'reply', 'children': []}]},
                         {'id': 12, 'text': None, 'children': []}]}

    def fake(url, **kw):
        urls.append(url)
        return json.dumps(item if '/items/' in url else hits).encode()
    monkeypatch.setattr(social_fetch, 'fetch', fake)
    pg = hackernews.ADAPTER.fetch('front', 'new', '')
    assert 'search?tags=front_page' in urls[-1] and len(pg.posts) == 2 and pg.cursor == '1'
    p = pg.posts[0]
    assert p['url'] == 'https://news.ycombinator.com/item?id=1' and p['link_url'] == 'https://e.com/a' and p['score'] == 5
    assert pg.posts[1]['body'] == 'body & more' and pg.posts[1]['link_url'] == ''
    hackernews.ADAPTER.fetch('ask', 'new', '1')
    assert 'search_by_date?tags=ask_hn' in urls[-1] and 'page=1' in urls[-1]
    assert hackernews.ADAPTER.fetch('new', 'new', '2').cursor == ''
    hackernews.ADAPTER.search('rust lang', '')
    assert 'query=rust%20lang' in urls[-1]
    cs = hackernews.ADAPTER.comments(p)
    assert len(cs) == 1 and cs[0]['body'] == 'top' and cs[0]['children'][0]['body'] == 'reply'
    with pytest.raises(ValueError):
        hackernews.ADAPTER.parse('bogus')


def test_lobsters(net):
    story = {'short_id': 'abc123', 'title': 'T', 'url': 'https://x.org/p', 'score': 7, 'comment_count': 3,
             'created_at': '2024-01-01T00:00:00.000-06:00', 'submitter_user': 'u',
             'comments_url': 'https://lobste.rs/s/abc123/t', 'tags': ['rust'], 'description': ''}
    net['https://lobste.rs/page/2.json'] = [story]
    net['https://lobste.rs/newest.json'] = [story] * 25
    net['https://lobste.rs/newest/page/3.json'] = []
    net['https://lobste.rs/t/rust.json'] = [story] * 25
    net['https://lobste.rs/s/abc123.json'] = {'comments': [
        {'short_id': 'c1', 'comment': '<p>a</p>', 'score': 2, 'created_at': '2024-01-01T01:00:00Z',
         'depth': 0, 'parent_comment': None, 'commenting_user': {'username': 'z'}},
        {'short_id': 'c2', 'comment': 'b', 'depth': 1, 'parent_comment': 'c1', 'commenting_user': 'y'}]}
    pg = lobsters.ADAPTER.fetch('hottest', 'hot', '2')
    p = pg.posts[0]
    assert pg.cursor == '' and p['external_id'] == 'abc123' and p['community'] == 'rust' and p['published_at'] > 0
    assert lobsters.ADAPTER.fetch('Rust', 'new', '').cursor == ''
    assert lobsters.ADAPTER.fetch('hottest', 'new', '').cursor == '2' and net.calls[-1] == 'https://lobste.rs/newest.json'
    lobsters.ADAPTER.fetch('hottest', 'new', '3')
    assert net.calls[-1] == 'https://lobste.rs/newest/page/3.json'
    cs = lobsters.ADAPTER.comments(p)
    assert len(cs) == 1 and cs[0]['author'] == 'z' and cs[0]['children'][0]['id'] == 'c2'
    with pytest.raises(ValueError):
        lobsters.ADAPTER.parse('bad tag!')


def test_devto(net):
    art = {'id': 5, 'title': 'Hello', 'description': 'desc', 'url': 'https://dev.to/u/hello-1',
           'cover_image': 'https://img/x.png', 'public_reactions_count': 9, 'comments_count': 1,
           'published_timestamp': '2024-02-01T10:00:00Z', 'user': {'username': 'u'}, 'tag_list': ['python', 'ai']}
    net['https://dev.to/api/articles/latest?tag=python'] = [art] * 50
    net['https://dev.to/api/articles?tag=python'] = [art]
    net['https://dev.to/api/comments?a_id=5'] = [
        {'id_code': 'k1', 'body_html': '<p>c</p>', 'user': {'username': 'v'}, 'created_at': '2024-02-02T00:00:00Z',
         'children': [{'id_code': 'k2', 'body_html': 'r', 'user': {'username': 'w'}, 'children': []}]}]
    pg = devto.ADAPTER.fetch('python', 'new', '')
    assert pg.cursor == '2' and pg.posts[0]['media'][0]['url'] == 'https://img/x.png'
    pg = devto.ADAPTER.fetch('python', 'top_week', '')
    assert net.calls[-1].endswith('&top=7') and pg.cursor == '' and pg.posts[0]['score'] == 9
    cs = devto.ADAPTER.comments(pg.posts[0])
    assert cs[0]['body'] == 'c' and cs[0]['children'][0]['author'] == 'w'
    with pytest.raises(ValueError):
        devto.ADAPTER.parse('a b')


RSS = b'''<?xml version="1.0"?><rss xmlns:dc="http://purl.org/dc/elements/1.1/"><channel>
<item><title>Link topic</title><link>https://ext.com/a</link><comments>https://tildes.net/~comp/abc1/link_topic</comments>
<pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate><dc:creator>zed</dc:creator><description>&lt;p&gt;hello&lt;/p&gt;</description></item>
<item><title>Text topic</title><link>https://tildes.net/~comp/abc2/text</link><guid>https://tildes.net/~comp/abc2/text</guid></item>
<item><title>Evil</title><link>https://evil.com/~comp/abc3/x</link></item></channel></rss>'''

HTML = '''<ol class="comment-tree"><li><article id="comment-q1" class="comment"><div class="comment-itself">
<a class="link-user">@ann</a><time datetime="2024-01-01T00:00:00Z"></time><div class="comment-text"><p>top</p></div></div>
<ol class="comment-tree"><li><article id="comment-q2" class="comment"><div class="comment-itself"><a class="link-user">bob</a>
<div class="comment-text"><p>reply</p></div></div></article></li></ol></article></li></ol>'''


def test_tildes(net):
    net['https://tildes.net/~comp/topics.rss'] = RSS
    net['https://tildes.net/topics.rss'] = RSS
    pg = tildes.ADAPTER.fetch('comp', 'new', '')
    assert pg.cursor == '' and [p['external_id'] for p in pg.posts] == ['abc1', 'abc2']
    a, b = pg.posts
    assert a['link_url'] == 'https://ext.com/a' and a['url'] == 'https://tildes.net/~comp/abc1/link_topic'
    assert a['author'] == 'zed' and a['body'] == 'hello' and a['community'] == '~comp' and a['published_at'] > 0
    assert b['link_url'] == ''
    assert tildes.ADAPTER.parse('https://tildes.net/~comp/abc') == '~comp' and tildes.ADAPTER.parse('~') == '~'
    assert tildes.ADAPTER.fetch('~', 'new', '').posts
    net['https://tildes.net/~comp/abc1/'] = HTML.encode()
    cs = tildes.ADAPTER.comments(a)
    assert cs[0]['id'] == 'q1' and cs[0]['author'] == 'ann' and cs[0]['body'] == 'top'
    assert cs[0]['children'][0]['body'] == 'reply' and cs[0]['children'][0]['author'] == 'bob'
    net['https://tildes.net/~comp/abc1/'] = b'<html>nothing</html>'
    assert tildes.ADAPTER.comments(a) == []
    with pytest.raises(ValueError):
        tildes.ADAPTER.parse('https://evil.com/~comp')
    net['https://tildes.net/~x/topics.rss'] = b'<!DOCTYPE x [<!ENTITY a "b">]><rss/>'
    with pytest.raises(ValueError):
        tildes.ADAPTER.fetch('x', 'new', '')


def test_stackexchange(net):
    q = {'question_id': 42, 'title': 'How &amp; why?', 'link': 'https://superuser.com/questions/42/x',
         'score': 3, 'answer_count': 2, 'creation_date': 1700000000, 'body': '<p>b</p>',
         'owner': {'display_name': 'Al &amp; Co'}, 'tags': ['linux']}
    net['https://api.stackexchange.com/2.3/questions?'] = {'items': [q], 'has_more': True}
    net['https://api.stackexchange.com/2.3/search/advanced?'] = {'items': [q], 'has_more': False}
    net['https://api.stackexchange.com/2.3/questions/42/answers?'] = {'items': [
        {'answer_id': 9, 'body': '<p>ans</p>', 'score': 4, 'owner': {'display_name': 'x'}, 'creation_date': 5}]}
    pg = stackexchange.ADAPTER.fetch('superuser', 'top_week', '2')
    assert 'sort=week' in net.calls[-1] and 'page=2' in net.calls[-1] and pg.cursor == '3'
    p = pg.posts[0]
    assert p['title'] == 'How & why?' and p['author'] == 'Al & Co' and p['comments'] == 2 and p['community'] == 'superuser'
    s = stackexchange.ADAPTER.search('wifi drop', '')
    assert 'site=stackoverflow' in net.calls[-1] and s.cursor == ''
    cs = stackexchange.ADAPTER.comments(p)
    assert cs[0]['body'] == 'ans' and cs[0]['score'] == 4
    with pytest.raises(ValueError):
        stackexchange.ADAPTER.parse('Bad Site!')


def test_stackexchange_key_gzip_backoff(net, monkeypatch):
    monkeypatch.setattr(stackexchange, '_key', lambda: 'K1')
    net['https://api.stackexchange.com/2.3/questions?'] = gzip.compress(json.dumps({'items': [], 'has_more': False}).encode())
    assert stackexchange.ADAPTER.fetch('askubuntu', 'new', '').posts == []
    assert '&key=K1' in net.calls[-1]
    net['https://api.stackexchange.com/2.3/questions?'] = {'items': [], 'backoff': 5}
    assert stackexchange.ADAPTER.fetch('askubuntu', 'new', '').posts == []
    n = len(net.calls)
    with pytest.raises(ValueError, match='back off'):
        stackexchange.ADAPTER.fetch('askubuntu', 'new', '')
    assert len(net.calls) == n  # no request while backing off


def test_discourse(net):
    base = 'https://forum.example.com'
    topic = {'id': 7, 'slug': 'hello-world', 'title': 'Hello', 'excerpt': 'ex', 'like_count': 4, 'posts_count': 3,
             'created_at': '2024-03-01T00:00:00.000Z', 'image_url': 'https://forum.example.com/i.png'}
    net[f'{base}/latest.json'] = {'topic_list': {'topics': [topic], 'more_topics_url': '/latest?page=1'}}
    net[f'{base}/top.json'] = {'topic_list': {'topics': [topic]}}
    net[f'{base}/c/general/4.json'] = {'topic_list': {'topics': [topic]}}
    net[f'{base}/t/7.json'] = {'post_stream': {'posts': [
        {'id': 1, 'post_number': 1, 'cooked': '<p>op</p>', 'username': 'o'},
        {'id': 2, 'post_number': 2, 'cooked': '<p>r1</p>', 'username': 'a', 'created_at': '2024-03-02T00:00:00Z',
         'actions_summary': [{'id': 2, 'count': 3}]},
        {'id': 3, 'post_number': 3, 'cooked': 'r2', 'username': 'b', 'reply_to_post_number': 2},
        {'id': 4, 'post_number': 4, 'cooked': 'r3', 'username': 'c', 'reply_to_post_number': 1}]}}
    pg = discourse.ADAPTER.fetch(base + '/some/path', 'new', '')
    p = pg.posts[0]
    assert pg.cursor == '1' and p['url'] == f'{base}/t/hello-world/7' and p['comments'] == 2 and p['score'] == 4
    assert p['media'][0]['url'] == 'https://forum.example.com/i.png' and p['body'] == 'ex'
    assert discourse.ADAPTER.fetch(base, 'top_week', '').cursor == '' and 'period=weekly' in net.calls[-1]
    assert discourse.ADAPTER.parse(base + '/c/general/4') == base + '/c/general/4'
    discourse.ADAPTER.fetch(base + '/c/general/4', 'new', '')
    assert net.calls[-1] == f'{base}/c/general/4.json?order=created&page=0'
    cs = discourse.ADAPTER.comments(p)
    assert [c['body'] for c in cs] == ['r1', 'r3'] and cs[0]['score'] == 3 and cs[0]['children'][0]['body'] == 'r2'
    for bad in ('http://forum.example.com', 'https://127.0.0.1', 'https://u:p@forum.example.com', 'forum.example.com'):
        with pytest.raises(ValueError):
            discourse.ADAPTER.parse(bad)
