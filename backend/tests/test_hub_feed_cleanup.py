"""Fresh Hacker News for general news curators, and meme captions without their links."""
import json
import os
import tempfile

os.environ.setdefault('BT_DATA_DIR', tempfile.mkdtemp())

from sqlmodel import SQLModel  # noqa: E402

from bt import db  # noqa: E402
from bt.db import init_db, session  # noqa: E402
from bt.hub import memes, providers  # noqa: E402
from bt.hub.extract import drop_links  # noqa: E402
from bt.hub.models import ContentItem  # noqa: E402


def _hn(monkeypatch):
    asked = []

    def fetch(url, **_kw):
        asked.append(url)
        return json.dumps({'hits': [{'objectID': '1', 'title': 'A story', 'url': 'https://example.org/a',
                                     'created_at_i': 1}]})
    monkeypatch.setattr(providers.curation, 'fetch', fetch)
    return asked


def test_a_general_news_query_reads_the_front_page(monkeypatch):
    asked = _hn(monkeypatch)
    assert providers.hackernews('', 'latest news')[0]['title'] == 'A story'
    providers.hackernews('', 'home assistant')
    assert 'tags=front_page' in asked[0] and 'query=' not in asked[0]
    assert 'search_by_date' in asked[1] and 'query=home+assistant' in asked[1]


def test_links_leave_meme_captions():
    assert drop_links("POV: you're Sam Altman https:// sopuli.xyz/post/54031712") == "POV: you're Sam Altman"
    assert drop_links('RAM joke Source: https://www. reddit.com/r/memes/comments/1x/abc') == 'RAM joke'
    assert drop_links('come and take it, Sam') == 'come and take it, Sam'
    assert drop_links('https://x.example/1') == ''
    assert drop_links('https://x.com node.js rocks') == 'node.js rocks'
    assert drop_links('https://x.com/a 10/10 meme') == '10/10 meme'
    assert drop_links('made by source') == 'made by source'


def test_mastodon_link_spans_leave_the_title_but_mentions_and_tags_stay():
    link = ('<a href="https://sopuli.xyz/post/54031712" rel="nofollow"><span class="invisible">https://</span>'
            '<span class="ellipsis">sopuli.xyz/post/5403</span><span class="invisible">1712</span></a>')
    tag = '<a href="https://m.example/tags/linux" class="mention hashtag" rel="tag">#<span>linux</span></a>'
    status = {'visibility': 'public', 'uri': 'https://m.example/2', 'content': f"<p>POV: you're Sam {link} {tag}</p>",
              'media_attachments': [{'type': 'image', 'url': 'https://m.example/i.png'}], 'account': {'acct': 'a'}}
    assert providers._mastodon_status(status, 'm.example')['title'] == "POV: you're Sam # linux"
    status['content'] = f'<p>RAM joke Source: {link}</p>'
    assert providers._mastodon_status(status, 'm.example')['title'] == 'RAM joke'
    status['content'] = f'<p>Source: {link}</p>'
    status['media_attachments'][0]['description'] = 'a cat'
    assert providers._mastodon_status(status, 'm.example')['title'] == 'a cat'


def test_a_mastodon_link_only_post_falls_back_to_its_image_description():
    status = {'visibility': 'public', 'uri': 'https://m.example/1', 'content': '<p>https://lemmy.example/post/1</p>',
              'media_attachments': [{'type': 'image', 'url': 'https://m.example/i.png', 'preview_url': 'https://m.example/p.png',
                                     'description': 'a cat on a keyboard'}], 'account': {'acct': 'cat'}}
    item = providers._mastodon_status(status, 'm.example')
    assert item['title'] == 'a cat on a keyboard'


def test_stored_meme_titles_are_cleaned_once():
    with db.engine.begin() as conn:
        conn.exec_driver_sql('DROP TABLE IF EXISTS content_fts')
    SQLModel.metadata.drop_all(db.engine)
    init_db()
    with session() as s:
        meme = ContentItem(url='https://m.example/1', type='meme', title='Distrohoppers https:// lemmy.zip/post/1')
        news = ContentItem(url='https://n.example/1', type='news', title='Read https://n.example/1')
        s.add(meme); s.add(news); s.commit()
        assert memes.backfill_titles(s) == 1
        assert memes.backfill_titles(s) == 0
        s.refresh(meme); s.refresh(news)
        assert meme.title == 'Distrohoppers' and news.title.startswith('Read https')
        from sqlmodel import text
        assert s.exec(text("SELECT title FROM content_fts WHERE rowid = :i").bindparams(i=meme.id)).one()[0] == 'Distrohoppers'
