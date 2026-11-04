from bt.hub.engine import fresh_candidates
from bt.hub.models import Source


def test_article_feeds_are_screened_alongside_busy_query_sources():
    search = Source(id=1, profile_id=1, provider='hackernews', url='')
    feed = Source(id=2, profile_id=1, provider='rss', url='https://example.org/feed')
    other = Source(id=3, profile_id=1, provider='rss', url='https://other.org/feed')
    def row(src, n):
        return [src, '', {'url': f'https://example.org/{src.id}/{n}'}, 50, '']
    rows = [row(search, n) for n in range(30)] + [row(feed, n) for n in range(3)] + [row(other, 0)]
    chosen = fresh_candidates(rows, {rows[0][2]['url']}, 5)
    assert [r[0].id for r in chosen] == [1, 2, 3, 1, 2]
    assert chosen[0][2]['url'].endswith('/1/1')
    assert len(fresh_candidates(rows, {r[2]['url'] for r in rows}, 10)) == 0


def test_atom_updated_only_entries_keep_their_real_age():
    from bt.hub.providers import parse_feed
    from bt.hub.extract import ts
    feed = b'<feed xmlns="http://www.w3.org/2005/Atom"><title>Updates</title><entry><title>Release</title><link href="https://example.org/release"/><updated>2026-10-01T12:00:00Z</updated></entry></feed>'
    assert parse_feed(feed, 'https://example.org/atom.xml')[0]['published_at'] == ts('2026-10-01T12:00:00Z')
