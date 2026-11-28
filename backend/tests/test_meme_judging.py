"""Meme entertainment decisions must not inherit the magazine's article rubric."""
import os
import tempfile

os.environ.setdefault('BT_DATA_DIR', tempfile.mkdtemp(prefix='wc-meme-judging-'))

import pytest
from sqlmodel import SQLModel

from bt import db
from bt.db import init_db, session
from bt.hub.master import MEME_PASS_VERSION, PASS_VERSION, judge, pipeline
from bt.models import Profile
from bt.hub.models import ContentItem, Curator, CuratorDiscovery, HubPreferenceCore, ItemState, ItemVerdict

NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def fresh_db():
    with db.engine.begin() as conn:
        conn.exec_driver_sql('DROP TABLE IF EXISTS content_fts')
    SQLModel.metadata.drop_all(db.engine)
    init_db()


def add_item(s, kind, suffix='', **verdict):
    item = ContentItem(type=kind, url=f'https://example.org/{kind}/{suffix}', title=f'{kind} {suffix}',
                       first_seen=NOW - 60, published_at=NOW - 60,
                       summary='A concrete caption explaining the joke and its everyday situation.',
                       image='/api/hub/media/test.jpg', meta={'community': 'memes', 'media': {'alt': 'cat at a desk'}})
    s.add(item)
    s.flush()
    s.add(CuratorDiscovery(profile_id=1, curator_id=1, item_id=item.id, score=70))
    s.add(ItemVerdict(profile_id=1, item_id=item.id, quality=70, relevance=70, final=70, **verdict))
    return item


def test_mixed_batches_use_meme_only_interests_and_rubric(monkeypatch):
    calls = []
    monkeypatch.setattr(judge.ai, 'enabled', lambda: True)
    monkeypatch.setattr(judge, 'worker_busy', lambda: False)
    monkeypatch.setattr(judge, 'video_busy', lambda: False)
    def call(system, prompt, schema, **kw):
        calls.append((system, prompt))
        return {'verdicts': [{'n': 1, 'quality': 80, 'relevance': 80, 'reason': 'A clear joke.'}]}
    monkeypatch.setattr(judge.ai, '_call', call)
    with session() as s:
        s.add(Curator(profile_id=1, name='Funny', instructions='Enjoy silly everyday humor', content_types=['meme']))
        s.add(Curator(profile_id=1, name='Technical research', instructions='Demand technical insight', content_types=['article']))
        s.add(HubPreferenceCore(profile_id=1, text='Technical substance above everything'))
        meme, article, other = [add_item(s, kind) for kind in ('meme', 'article', 'project')]
        s.commit()
        assert judge.judge(s, 1, NOW)['judged'] == 3
        meme_call = next(c for c in calls if c[0] == judge.MEME_SYSTEM)
        assert 'Enjoy silly everyday humor' in meme_call[1]
        assert 'Technical research' not in meme_call[1]
        assert 'Technical substance' not in meme_call[1]
        assert 'image description: cat at a desk' in meme_call[1]
        assert 'community: memes' in meme_call[1]
        assert len(calls) == 3
        assert s.get(ItemVerdict, (1, meme.id)).pass_version == MEME_PASS_VERSION
        assert s.get(ItemVerdict, (1, article.id)).pass_version == PASS_VERSION
        assert s.get(ItemVerdict, (1, other.id)).pass_version == PASS_VERSION


def test_new_rubric_retries_only_old_meme_decisions(monkeypatch):
    monkeypatch.setattr(pipeline.cluster, 'analyze_memes', lambda *a: ({}, set(), set()))
    with session() as s:
        meme = add_item(s, 'meme', judged_by='llm', pass_version=PASS_VERSION)
        article = add_item(s, 'article', judged_by='llm', pass_version=PASS_VERSION)
        current = add_item(s, 'meme', 'current', judged_by='llm', pass_version=MEME_PASS_VERSION)
        s.commit()
        pipeline.run(s, 1, NOW)
        assert s.get(ItemVerdict, (1, meme.id)).judged_by == 'rules'
        assert s.get(ItemVerdict, (1, meme.id)).pass_version == MEME_PASS_VERSION
        assert s.get(ItemVerdict, (1, article.id)).judged_by == 'llm'
        assert s.get(ItemVerdict, (1, current.id)).judged_by == 'llm'
        assert meme.id in [item.id for _, item in judge.candidates(s, 1, NOW, 100, band=False)]


def test_old_meme_scores_reset_without_removing_user_overrides(monkeypatch):
    monkeypatch.setattr(pipeline.cluster, 'analyze_memes', lambda *a: ({}, set(), set()))
    with session() as s:
        promoted = add_item(s, 'meme', 'promoted', judged_by='llm', pass_version=PASS_VERSION, overruled='promoted')
        dismissed = add_item(s, 'meme', 'dismissed', judged_by='llm', pass_version=PASS_VERSION)
        s.add(ItemState(profile_id=1, item_id=dismissed.id, dismissed=True))
        s.commit()
        pipeline.run(s, 1, NOW)
        pv, dv = s.get(ItemVerdict, (1, promoted.id)), s.get(ItemVerdict, (1, dismissed.id))
        assert pv.forced and pv.tier == 'feature' and pv.overruled == 'promoted'
        assert dv.forced and dv.tier == 'reject'
        assert judge.candidates(s, 1, NOW, 100, band=False) == []


def test_unseen_joke_remains_pending_when_editor_is_also_uncertain(monkeypatch):
    monkeypatch.setattr(judge.ai, 'enabled', lambda: True)
    monkeypatch.setattr(judge, 'worker_busy', lambda: False)
    monkeypatch.setattr(judge, 'video_busy', lambda: False)
    monkeypatch.setattr(judge.ai, '_call', lambda *a, **kw: {'verdicts': [
        {'n': 1, 'quality': 50, 'relevance': 50, 'uncertain': True, 'reason': 'Cannot see the image.'}]})
    with session() as s:
        meme = add_item(s, 'meme')
        meme.title, meme.summary, meme.meta = 'me_irl', '', {'community': 'me_irl'}
        s.add(meme)
        s.commit()
        out = judge.judge(s, 1, NOW)
        assert out['judged'] == 0 and out['skipped'] == 1
        assert s.get(ItemVerdict, (1, meme.id)).judged_by == 'rules'
        assert judge.candidates(s, 1, NOW, 10)


def test_meme_context_excludes_news_clips_and_respects_scoped_feedback():
    with session() as s:
        s.add(Curator(profile_id=1, name='Video Moments', content_types=['meme'],
                      queries=['AI news', 'linux tips'], instructions='Technical reporting',
                      tuned_up=['security'], tuned_down=['kittens']))
        s.add(Curator(profile_id=1, name='Memes', content_types=['meme'],
                      tuned_up=['cats', 'meme', 'HTTP'], tuned_down=['crypto', 'funny', 'https'], exclusions=['ads']))
        s.add(Curator(profile_id=2, name='Memes', content_types=['meme'], tuned_up=['other household']))
        profile = s.get(Profile, 1)
        profile.prefs = {**(profile.prefs or {}), 'hidden_meme_languages': ['de']}
        s.add(profile)
        s.commit()
        context = judge.meme_reader(s, 1)
        assert 'Video Moments' not in context and 'Technical reporting' not in context
        assert 'security' not in context and 'kittens' not in context
        assert 'other household' not in context
        assert 'more like: cats; less like: crypto' in context
        assert 'HTTP' not in context and 'https' not in context and 'funny' not in context
        assert 'more like: cats, meme' not in context
        assert 'avoid: ads' in context and 'Hidden meme languages: de' in context


def test_scout_rank_isolates_memes_and_keeps_unseen_image_relevance_neutral(monkeypatch):
    from bt.hub import ai, article_preferences
    calls = []
    monkeypatch.setattr(article_preferences, 'context', lambda s, pid: 'Prefer deeply researched engineering articles')
    def call(system, prompt, schema, **kw):
        calls.append((system, prompt, schema))
        if 'You screen meme candidates' in system:
            return {'scores': [{'n': 1, 'score': 5, 'uncertain': True, 'reason': 'The joke is in an unseen image.'},
                               {'n': 2, 'score': 0, 'uncertain': False, 'reason': 'An explicit advertisement.'}]}
        return {'scores': [{'n': 1, 'score': 81 if 'Article' in prompt else 72, 'reason': 'Relevant evidence.'}]}
    monkeypatch.setattr(ai, '_call', call)
    result = ai.rank(Curator(profile_id=1, name='Memes and things', instructions='Fun memes', exclusions=['ads']), [
        {'type': 'meme', 'title': 'me_irl', 'meta': {'community': 'me_irl', 'media': {'alt': 'Cat staring at an empty bowl'}}},
        {'type': 'project', 'title': 'Code project'},
        {'type': 'news', 'title': 'Article'},
        {'type': 'meme', 'title': 'Buy now', 'summary': 'An advertisement for shoes', 'meta': {'community': 'memes'}},
    ])
    assert [score for score, _ in result] == [50, 72, 81, 0]
    assert len(calls) == 3
    article_call, meme_call, ordinary_call = calls
    assert 'deeply researched engineering' in article_call[1]
    assert 'deeply researched engineering' not in meme_call[1] + ordinary_call[1]
    assert 'community: me_irl' in meme_call[1]
    assert 'image description: Cat staring at an empty bowl' in meme_call[1]
    assert 'me_irl' not in article_call[1] + ordinary_call[1]
    assert 'neutral score of 50' in meme_call[0]
    assert 'uncertain' in meme_call[2]['properties']['scores']['items']['properties']
