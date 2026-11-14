"""Master curator P1: deterministic rules, folding and tiering keep junk off the front page."""
import os
import tempfile
import time

os.environ.setdefault('BT_DATA_DIR', tempfile.mkdtemp(prefix='wc-master-curator-'))

import pytest
from sqlmodel import SQLModel, select

from bt import db
from bt.db import init_db, session
from bt.hub.master import cluster, pipeline, rules, score
from bt.hub.models import (CaptureJob, ContentItem, CuratorDiscovery, ItemState, ItemVerdict, MemeExample, MemeTrend,
                           MemeTrendState, Story, StoryItem, Topic)
from bt.models import Profile

NOW = 1_800_000_000.0
_n = iter(range(1, 10_000))


@pytest.fixture(autouse=True)
def fresh_db():
    with db.engine.begin() as conn:
        conn.exec_driver_sql('DROP TABLE IF EXISTS content_fts')
    SQLModel.metadata.drop_all(db.engine)
    init_db()
    yield


def item(s, type='news', title='Council approves new harbour bridge plan', summary=None, pid=1, scout=60.0,
         age=3600.0, **kw):
    n = next(_n)
    it = ContentItem(url=f'https://example.org/{n}', type=type, title=title,
                     summary=summary if summary is not None else 'The city council voted on Tuesday to fund the long-debated bridge across the harbour.',
                     first_seen=NOW - age, published_at=NOW - age, **kw)
    s.add(it); s.flush()
    if pid:
        s.add(CuratorDiscovery(curator_id=1, item_id=it.id, profile_id=pid, score=scout))
    return it


def meme(s, title='when the build passes on the first try', reactions=40, **kw):
    meta = {'media': {'type': 'image', 'url': 'https://img.example/x.png', 'alt': 'a cat celebrating'},
            'observations': {'favourites': reactions, 'reblogs': 0}, 'tags': ['programming'], 'language': 'en'}
    meta.update(kw.pop('meta', {}))
    kw.setdefault('image', 'https://img.example/x.png')
    return item(s, type='meme', title=title, summary=title, meta=meta, **kw)


# ---------- rules

def test_hard_gates_reject_what_the_profile_ruled_out():
    with session() as s:
        s.add(Topic(profile_id=1, name='crypto', kind='news', muted=True, keywords=['bitcoin']))
        s.add(Profile(id=2, name='Kid', role='child'))
        s.commit()
        adult, kid = rules.load(s, 1), rules.load(s, 2)
        assert rules.check(item(s, title='Bitcoin hits a new high again'), None, adult, NOW).reasons == ['muted topic "bitcoin"']
        spicy = meme(s, meta={'sensitive': True})
        assert rules.check(spicy, None, kid, NOW).reject
        assert not rules.check(spicy, None, adult, NOW).reject
        assert rules.check(meme(s), MemeExample(item_id=0, dup_of=5), adult, NOW).reasons == ['duplicate']


def test_hidden_languages_and_hidden_meme_trends_are_rejected():
    with session() as s:
        p = s.get(Profile, 1); p.prefs = {'hidden_meme_languages': ['de']}; s.add(p)
        t = MemeTrend(target_kind='format', target_key='7'); c = MemeTrend(target_kind='community', target_key='#cursed')
        s.add(t); s.add(c); s.flush()
        s.add(MemeTrendState(profile_id=1, trend_id=t.id, hidden=True))
        s.add(MemeTrendState(profile_id=1, trend_id=c.id, hidden=True))
        s.commit()
        r = rules.load(s, 1)
        assert rules.check(meme(s), MemeExample(item_id=0, language='de'), r, NOW).reject
        assert rules.check(meme(s), MemeExample(item_id=0, format_id=7), r, NOW).reject
        assert rules.check(meme(s), MemeExample(item_id=0, community='#cursed'), r, NOW).reject
        assert not rules.check(meme(s), MemeExample(item_id=0, language='en', format_id=8), r, NOW).reject


def test_quality_separates_junk_from_good_items():
    with session() as s:
        r = rules.ProfileRules()
        good_news = rules.check(item(s, image='https://img.example/a.jpg'), None, r, NOW)
        bait = rules.check(item(s, title="You won't believe what this mayor did", summary=''), None, r, NOW)
        good_meme = rules.check(meme(s, reactions=150), None, r, NOW)
        junk_meme = rules.check(meme(s, title='mastodon.social post', reactions=0, image='',
                                     meta={'media': {}, 'tags': list('abcdefgh')}), None, r, NOW)
        assert good_news.quality >= 65 and good_meme.quality >= 75
        assert bait.quality < 40 and 'clickbait headline' in bait.reasons
        assert junk_meme.quality <= 10
        assert {'no image', 'no reactions', 'hashtag spam', 'no real title'} <= set(junk_meme.reasons)


def test_old_news_is_set_aside_but_old_projects_are_not():
    with session() as s:
        r = rules.ProfileRules()
        assert rules.check(item(s, age=5 * 86400), None, r, NOW).set_aside
        assert not rules.check(item(s, type='project', age=60 * 86400), None, r, NOW).set_aside


# ---------- folding

def test_coverage_of_one_event_becomes_one_story():
    with session() as s:
        a = item(s, title='Council approves harbour bridge funding', publisher='Daily A')
        b = item(s, title='Harbour bridge funding approved by city council', publisher='Paper B', age=1800)
        c = item(s, title='Harbour bridge: council approves funding plan', publisher='Radio C', age=900)
        other = item(s, title='Local bakery wins national bread award', publisher='Daily A')
        chain = item(s, title='Bridge tournament results announced', publisher='Daily A')
        got = cluster.assign_stories(s, [a, b, c, other, chain])
        assert got[a.id] == got[b.id] == got[c.id]
        assert len({got[a.id], got[other.id], got[chain.id]}) == 3
        assert s.get(Story, got[a.id]).heat == 3
        # Running again changes nothing.
        assert cluster.assign_stories(s, [a, b, c]) == {a.id: got[a.id], b.id: got[a.id], c.id: got[a.id]}
        assert len(s.exec(select(StoryItem)).all()) == 5


def test_memes_wait_for_capture_before_their_duplicate_check():
    with session() as s:
        m = meme(s, age=60)
        s.add(CaptureJob(profile_id=1, item_id=m.id, status='queued'))
        s.flush()
        examples, waiting, uncaptured = cluster.analyze_memes(s, [m], NOW)
        assert waiting == {m.id} and not examples and not uncaptured
        job = s.exec(select(CaptureJob)).one(); job.status = 'failed'; s.add(job); s.flush()
        examples, waiting, uncaptured = cluster.analyze_memes(s, [m], NOW)
        assert uncaptured == {m.id} and not waiting and not examples
        # A text-only meme needs no capture and gets its caption identity straight away.
        t = meme(s, title='nobody: me at 3am', image='', meta={'media': {}})
        twin = meme(s, title='nobody: me at 3am', image='', meta={'media': {}})
        examples, _, _ = cluster.analyze_memes(s, [t, twin], NOW)
        assert examples[twin.id].dup_of == t.id


# ---------- tiering

def _cand(s, final, type='meme', publisher='', story=None, age=3600.0, title=None):
    it = item(s, type=type, publisher=publisher, age=age, pid=0, title=title or f'distinct headline {next(_n)}')
    return score.Candidate(it, final, final, final, [], story_id=story)


def test_tiers_respect_daily_limits_sources_and_stories():
    with session() as s:
        memes_ = [_cand(s, 90 - i, publisher=f'm{i}') for i in range(10)]
        same_source = [_cand(s, 80, type='article', publisher='Loud Daily') for _ in range(3)]
        story = [_cand(s, 85, type='news', publisher=f'n{i}', story=99) for i in range(3)]
        weak = _cand(s, 30, type='news')
        old = _cand(s, 90, type='article', age=3 * 86400)
        everything = memes_ + same_source + story + [weak, old]
        score.tier(everything, NOW)
        tiers = [c.tier for c in memes_]
        assert tiers.count('feature') == 8 and tiers[-2:] == ['keep', 'keep']
        assert [c.tier for c in same_source].count('feature') == 2
        assert [c.tier for c in story].count('feature') == 1
        assert 'folded into its story' in story[1].reasons
        assert weak.tier == 'set_aside' and old.tier == 'keep'


def test_front_pages_and_hashtag_titles_stay_off_the_front_page():
    with session() as s:
        r = rules.ProfileRules()
        def web(title, url):
            it = item(s, type='web', title=title)
            it.url = url
            return it
        front = [rules.check(web(t, u), None, r, NOW) for t, u in (
            ('Latest news from around the world | The Guardian', 'https://www.theguardian.com/world'),
            ('Latest News: Breaking Headlines and Video on Today\'s Top Stories | NBC News',
             'https://www.nbcnews.com/latest-stories'),
            ('The Local Denmark - News and practical guides in English', 'https://www.thelocal.dk/'))]
        site = item(s, type='web', title='Home Assistant')
        site.url = 'https://www.home-assistant.io/'
        launch = item(s, title='Show HN: an open-source dashboard')
        launch.url = 'https://dashboard.example/'
        tagged = meme(s, title='# scrum # dev # coding # programming # programminghumor', reactions=40)
        assert all(f.set_aside and 'a front page, not a story' in f.reasons for f in front)
        assert rules.check(site, None, r, NOW).set_aside
        assert not rules.check(launch, None, r, NOW).set_aside
        assert 'hashtag spam' in rules.check(tagged, None, r, NOW).reasons
        stories = [item(s, title=t) for t in (
            'Latest Windows update breaks printers for millions', 'BREAKING NEWS: 7.1 earthquake strikes off Japan',
            'Ukraine war latest news: Russia strikes Kyiv', 'Top stories of 2025: what AI changed')]
        nourl = item(s, type='web', title='Notes on a quiet morning')
        nourl.url = ''
        wp = item(s, type='web', title='A post about bridges')
        wp.url = 'https://blog.example/?p=123'
        article = item(s, type='web', title='Latest Windows update breaks printers')
        article.url = 'https://example.com/tech/latest-windows-update-breaks-printers'
        dated = item(s, type='web', title='Latest news on the bridge vote | City Paper')
        dated.url = 'https://paper.example/2026/11/03/bridge-vote'
        section = item(s, type='web', title='Latest News from Denmark')
        section.url = 'https://www.thelocal.dk/category/denmark-news'
        short = item(s, type='web', title='Latest Windows update breaks printers')
        short.url = 'https://example.com/tech/latest-windows-update'
        assert not any(rules.check(x, None, r, NOW).set_aside for x in (article, dated, short))
        assert rules.check(section, None, r, NOW).set_aside
        ranked = meme(s, title='#1 vs #2 vs #3 vs #4 ranking')
        assert not any(rules.check(x, None, r, NOW).set_aside for x in stories + [nourl, wp])
        assert 'hashtag spam' not in rules.check(ranked, None, r, NOW).reasons


def test_one_post_shared_to_two_communities_is_featured_once():
    with session() as s:
        a = _cand(s, 70, publisher='lemmy.a', title='come and take it, Sam https:// a.example/post/1')
        b = _cand(s, 69, publisher='lemmy.b', title='Come and take it, Sam https:// b.example/post/2')
        score.tier([a, b], NOW)
        assert (a.tier, b.tier) == ('feature', 'keep') and 'same as a featured item' in b.reasons
        rules_ = [_cand(s, 68, publisher=f'lemmy.{i}', title='Rule') for i in range(2)]
        score.tier(rules_, NOW)
        assert [c.tier for c in rules_] == ['feature', 'feature']


# ---------- the pass

def test_pass_stores_verdicts_and_honours_the_user():
    with session() as s:
        good = item(s, image='https://img.example/a.jpg', scout=80)
        junk = meme(s, title='x.example post', reactions=0, image='', meta={'media': {}})
        gone = item(s, title='Dismissed story about tax forms')
        rescued = item(s, title='Weak but wanted', summary='', scout=10)
        other = item(s, title='Someone else entirely', pid=2)
        s.add(ItemState(profile_id=1, item_id=gone.id, dismissed=True))
        s.add(ItemVerdict(profile_id=1, item_id=rescued.id, tier='set_aside', overruled='promoted'))
        s.commit()
        counts = pipeline.run(s, 1, NOW)
        v = {x.item_id: x for x in s.exec(select(ItemVerdict).where(ItemVerdict.profile_id == 1)).all()}
        assert other.id not in v
        assert v[good.id].tier == 'feature' and v[good.id].judged_by == 'rules'
        assert v[junk.id].tier == 'set_aside'
        assert v[gone.id].tier == 'reject' and 'dismissed' in v[gone.id].reasons
        assert v[rescued.id].tier == 'feature' and v[rescued.id].overruled == 'promoted'
        assert counts['feature'] == 2 and counts['reject'] == 1
        # A second pass is stable.
        assert pipeline.run(s, 1, NOW) == counts


def test_a_models_scores_survive_the_rules_pass():
    with session() as s:
        it = item(s, scout=60)
        s.add(ItemVerdict(profile_id=1, item_id=it.id, quality=20, relevance=20, judged_by='llm', pass_version=pipeline.PASS_VERSION))
        s.commit()
        pipeline.run(s, 1, NOW)
        v = s.get(ItemVerdict, (1, it.id))
        assert (v.quality, v.relevance, v.judged_by, v.tier) == (20, 20, 'llm', 'set_aside')


# ---------- review regressions

def test_muted_terms_match_whole_words_only():
    with session() as s:
        s.add(Topic(profile_id=1, name='ai', kind='news', muted=True))
        s.commit()
        r = rules.load(s, 1)
        assert not rules.check(item(s, title='Spain says rain is coming'), None, r, NOW).reject
        assert rules.check(item(s, title='New AI rules for schools'), None, r, NOW).reject


def test_engagement_reads_net_votes_and_ignores_lifetime_stars():
    assert rules.engagement({'observations': {'score': 12, 'upvotes': 14, 'downvotes': 2}}) == 12
    assert rules.engagement({'observations': {'favourites': 3, 'reblogs': 2, 'replies': 9}}) == 5
    assert rules.engagement({'stars': 50000}) is None
    assert rules.engagement({'points': 120}) == 120


def test_an_old_lead_does_not_hide_fresh_coverage_and_promotions_use_up_room():
    with session() as s:
        old = _cand(s, 80, type='news', publisher='a', story=7, age=30 * 3600)
        fresh = _cand(s, 72, type='news', publisher='b', story=7)
        score.tier([old, fresh], NOW)
        assert (old.tier, fresh.tier) == ('keep', 'feature')

        promoted = [_cand(s, 20, publisher=f'p{i}') for i in range(8)]
        for c in promoted:
            c.tier, c.forced = 'feature', True
        best = _cand(s, 95, publisher='q')
        score.tier(promoted + [best], NOW)
        assert best.tier == 'keep' and 'daily limit' in best.reasons


def test_featured_items_leaving_the_window_are_taken_off_the_front_page():
    with session() as s:
        it = item(s, age=9 * 86400)
        s.add(ItemVerdict(profile_id=1, item_id=it.id, tier='feature'))
        s.commit()
        assert pipeline.run(s, 1, NOW)['aged_out'] == 1
        assert s.get(ItemVerdict, (1, it.id)).tier == 'keep'


def test_a_first_pass_over_many_distinct_headlines_stays_fast():
    words = ['alpha', 'bravo', 'charlie', 'delta', 'echo', 'foxtrot', 'golf', 'hotel', 'india', 'juliet', 'kilo', 'lima']
    with session() as s:
        items = [item(s, title=f'{words[i % 12]} {words[i // 12 % 12]} report {i} number{i} case{i}', pid=0, age=i * 60)
                 for i in range(1500)]
        start = time.perf_counter()
        cluster.assign_stories(s, items)
        assert time.perf_counter() - start < 8


def test_delivered_stories_do_not_consume_new_days_feature_quota():
    from bt.hub.master import edition
    from bt.hub.models import Edition
    with session() as s:
        old = item(s, title='Earlier high quality model release', publisher='same publisher', scout=100)
        new = item(s, title='Fresh distinct security tooling announcement', publisher='same publisher', scout=100)
        s.add(Edition(profile_id=1, day=edition.today(NOW - 86400), shown_item_ids=[old.id]))
        s.commit()
        pipeline.run(s, 1, NOW)
        assert s.get(ItemVerdict, (1, old.id)).tier == 'keep'
        assert s.get(ItemVerdict, (1, new.id)).tier == 'feature'


def test_master_pass_reserves_reading_space_when_memes_fill_the_recent_pool(monkeypatch):
    monkeypatch.setattr(pipeline, 'MAX_ITEMS', 6)
    with session() as s:
        article = item(s, age=7200)
        reading = item(s, type='web', age=7100)
        foreign = item(s, pid=2, age=1)
        video = item(s, type='video', age=1)
        memes = [meme(s, age=100 + n) for n in range(9)]
        s.commit()
        selected = pipeline.visible_recent(s, 1, NOW)
        ids = {row.id for row in selected}
        assert len(selected) == 6
        assert {article.id, reading.id} <= ids
        assert foreign.id not in ids and video.id not in ids
        assert len(ids.intersection({row.id for row in memes})) == 4
        assert [row.first_seen for row in selected] == sorted((row.first_seen for row in selected), reverse=True)


def test_master_pass_lends_unused_family_space_to_the_newest_items(monkeypatch):
    monkeypatch.setattr(pipeline, 'MAX_ITEMS', 6)
    with session() as s:
        articles = [item(s, age=100 + n) for n in range(9)]
        s.commit()
        assert [row.id for row in pipeline.visible_recent(s, 1, NOW)] == [row.id for row in articles[:6]]
