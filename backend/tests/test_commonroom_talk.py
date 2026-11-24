"""T-139: The Commons prefers posts with conversations, without Reddit."""
import time
from types import SimpleNamespace

from test_curation import client  # noqa: F401
from test_commonroom import fake_platform, thread  # noqa: F401
from test_commonroom_fun import _feed, _pick
from bt.commonroom import curate, expedition
from bt.commonroom.adapters import ADAPTERS
from bt.commonroom.curate import Verdict, aggregator, capped, keeps, talk_bonus


def _post(**kw):
    base = dict(id=1, title='t', platform='rss', community='', body='', url='https://blog.example/a', link_url='',
                domain='blog.example', author='', comments=0, score=0)
    return SimpleNamespace(**{**base, **kw})


# ---------- curation

def test_replies_give_a_modest_boost_over_the_keep_line():
    assert [talk_bonus(n) for n in (0, 1, 3, 15, 5000)] == [0, 2, 4, 8, 8]
    assert keeps(Verdict(57, ''), 60, True) == (False, False)
    assert keeps(Verdict(57, ''), 60, True, comments=3) == (True, False)
    assert keeps(Verdict(40, ''), 60, True, comments=500) == (False, False)  # a boost, not a free pass


def test_aggregators_and_promo_accounts_are_recognised():
    assert aggregator(_post(url='https://awesome.ecosyste.ms/lists/x', domain='awesome.ecosyste.ms'))
    assert aggregator(_post(link_url='https://www.ossalt.com/alternatives/notion'))
    assert aggregator(_post(url='https://rss.feedspot.com/infiniterss.php?q=site:x', domain='rss.feedspot.com'))
    assert aggregator(_post(link_url='https://mirror.example/infiniterss.php?id=3'))
    assert aggregator(_post(platform='mastodon', author='RadWebHosting@mastodon.social'))
    assert not aggregator(_post())
    assert not aggregator(_post(link_url='https://notossalt.com/x', domain='notossalt.com'))


def test_only_aggregators_nobody_engaged_with_are_capped():
    agg = dict(url='https://www.ossalt.com/a', domain='ossalt.com')
    assert capped(_post(**agg))
    assert not capped(_post(**agg, comments=2))
    assert not capped(_post(**agg, score=12))
    assert not capped(_post())  # an ordinary quiet blog post still reaches the judge


def test_capped_posts_are_rejected_before_the_judge(client, monkeypatch):  # noqa: F811
    from sqlmodel import select
    from bt.commonroom import store
    from bt.commonroom.models import CRCandidate, CRThread
    from bt.db import session
    t = thread(client)
    [quiet, talked, normal] = store.upsert_posts([
        {'platform': 'fake', 'community': 'c/one', 'url': f'https://fake.example/{n}', 'title': n,
         'link_url': link, 'comments': c, 'published_at': time.time() - 60}
        for n, link, c in (('quiet', 'https://ossalt.com/x', 0), ('talked', 'https://ossalt.com/y', 4),
                           ('normal', 'https://blog.example/z', 0))])
    with session() as s:
        for pid in (quiet, talked, normal):
            s.add(CRCandidate(thread_id=t['id'], post_id=pid, status='pending'))
        s.commit()
        thr = s.get(CRThread, t['id'])
    judged = []
    monkeypatch.setattr(curate.ai, 'enabled', lambda: True)
    monkeypatch.setattr(curate, '_judge', lambda th, lessons, batch: judged.extend(p.id for p in batch) or {})
    monkeypatch.setattr(curate, '_settle', lambda *a, **k: None)
    curate.run(thr, '')
    assert quiet not in judged and {talked, normal} <= set(judged)


def test_judge_sees_reply_counts_and_marks_promo_stubs_down(monkeypatch):
    seen = {}
    monkeypatch.setattr(curate.ai, '_call', lambda system, user, schema, **k: seen.update(system=system, user=user)
                        or {'scores': []})
    t = SimpleNamespace(id=1, name='Odd', interests='weird finds', instructions='', avoid='', child_friendly=False)
    curate._judge(t, '', [_post(comments=42), _post(id=2, url='https://blog.example/b')])
    assert '"comments": "42"' in seen['user'] and seen['user'].count('"comments"') == 1
    assert 'SEO' in seen['system'] and 'promo' in seen['system'] and 'listings' in seen['system']


# ---------- the wall

def test_top_view_requires_some_engagement(client):  # noqa: F811
    t = thread(client)
    talked = _pick(t, 'https://fake.example/talked', comments=2)
    _pick(t, 'https://fake.example/silent', comments=0)
    ids = [p['post_id'] for p in _feed(client, mode='top')]
    assert ids == [talked]
    assert len(_feed(client, mode='new')) == 2  # other views still show quiet posts


def test_today_nudges_discussed_posts_ahead_at_equal_merit(client):  # noqa: F811
    t = thread(client)
    _pick(t, 'https://fake.example/quiet', comments=0)
    talked = _pick(t, 'https://fake.example/talked', comments=30)
    assert _feed(client, mode='today')[0]['post_id'] == talked


# ---------- source discovery

def test_hacker_news_and_lobsters_can_be_suggested():
    assert ADAPTERS['hackernews'].discoverable and ADAPTERS['lobsters'].discoverable
    assert ADAPTERS['lobsters'].parse('programming') == 'programming'


def test_subreddits_get_lemmy_twins_and_discussion_comes_first():
    named = [('rss', 'https://blog.example/feed'), ('reddit', 'interestingasfuck'), ('devto', 'python'),
             ('reddit', 'https://www.reddit.com/r/Damnthatsinteresting/'), ('lemmy', 'interestingasfuck@lemmy.world'),
             ('tildes', '~comp')]
    out = expedition.favour_discussion(expedition.with_lemmy_twins(named))
    assert out == [('reddit', 'interestingasfuck'), ('reddit', 'https://www.reddit.com/r/Damnthatsinteresting/'),
                   ('lemmy', 'damnthatsinteresting@lemmy.world'), ('lemmy', 'interestingasfuck@lemmy.world'),
                   ('tildes', '~comp'), ('rss', 'https://blog.example/feed'), ('devto', 'python')]
    assert expedition.with_lemmy_twins([('reddit', 'pics')], lemmy=False) == [('reddit', 'pics')]


def test_named_sources_prompt_favours_discussion_platforms(monkeypatch):
    seen = {}

    def fake(system, user, schema, **k):
        seen.update(system=system, user=user)
        return {'sources': [{'platform': 'rss', 'locator': 'https://b.example/feed'},
                            {'platform': 'reddit', 'locator': 'interestingasfuck'}], 'search_terms': ['odd']}
    monkeypatch.setattr(expedition.ai, '_call', fake)
    t = SimpleNamespace(name='Interesting', interests='surprising things', instructions='', avoid='')
    platforms = [a for a in ADAPTERS.values() if a.discoverable or a.key == 'rss']
    sources, terms = expedition._named(t, False, platforms)
    assert sources == [('reddit', 'interestingasfuck'), ('lemmy', 'interestingasfuck@lemmy.world'),
                       ('rss', 'https://b.example/feed')]
    assert 'Lemmy' in seen['system'] and 'PieFed' in seen['system'] and 'Reddit is' in seen['system']
    listing = seen['user'].split('<data>')[0]
    assert listing.index('- lemmy:') < listing.index('- rss:') and 'hackernews' in listing
