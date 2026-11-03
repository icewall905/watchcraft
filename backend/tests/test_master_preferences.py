"""Preference memory stays bounded, explainable and isolated to one hub profile."""
import os
import tempfile
import time

os.environ.setdefault('BT_DATA_DIR', tempfile.mkdtemp(prefix='wc-master-prefs-'))

import pytest
from sqlmodel import SQLModel, select

from bt import db
from bt.db import init_db, session
from bt.hub import migrate
from bt.hub.master import pipeline, prefs, score
from bt.hub.models import ContentItem, Curator, CuratorDiscovery, HubLesson, HubPreferenceCore, ItemState, ItemVerdict
from bt.models import Profile


@pytest.fixture(autouse=True)
def fresh_db():
    with db.engine.begin() as conn:
        conn.exec_driver_sql('DROP TABLE IF EXISTS content_fts')
    SQLModel.metadata.drop_all(db.engine)
    init_db()
    yield


def test_evidence_creates_lesson_once_and_rebuilds_core(monkeypatch):
    with session() as s:
        items = [ContentItem(url=f'https://e.test/{n}', type='news', title='Harbour bridge council vote', summary='Council plan', first_seen=100) for n in (1, 2)]
        s.add_all(items); s.flush()
        s.add_all([ItemState(profile_id=1, item_id=item.id, saved=True, saved_at=200, read_at=300) for item in items])
        s.commit()
        monkeypatch.setattr('bt.hub.ai.enabled', lambda: True)
        monkeypatch.setattr('bt.hub.ai._call', lambda *a, **k: {'lessons': [
            {'text': 'More local harbour infrastructure reporting', 'scope': 'news', 'polarity': 1,
             'evidence_ids': [item.id for item in items]}]})
        result = prefs.collect(s, 1, at=1000)
        lesson = s.exec(select(HubLesson).where(HubLesson.profile_id == 1)).one()
        assert result['core_chars'] > 0
        assert lesson.evidence == 2 and lesson.source_item_ids == [item.id for item in items]
        assert lesson.last_evidence_at == 300
        assert s.get(HubPreferenceCore, 1).text.startswith('- More [news]:')
        prefs.collect(s, 1, at=1100)
        s.refresh(lesson)
        assert lesson.evidence == 2  # retrying the same nightly evidence cannot amplify it
        assert lesson.last_evidence_at == 300  # reading old evidence does not refresh its age


def test_paraphrase_citing_the_same_evidence_reuses_existing_lesson(monkeypatch):
    with session() as s:
        items = [ContentItem(url=f'https://e.test/p{n}', type='news', title='Harbour bridge council vote', first_seen=100) for n in (1, 2)]
        s.add_all(items); s.flush()
        s.add_all([ItemState(profile_id=1, item_id=item.id, saved=True, saved_at=200) for item in items])
        s.commit()
        outputs = iter([
            {'lessons': [{'text': 'More local harbour infrastructure reporting', 'scope': 'news', 'polarity': 1, 'evidence_ids': [i.id for i in items]}]},
            {'lessons': [{'text': 'Prefers city transport and bridge coverage', 'scope': 'news', 'polarity': 1, 'evidence_ids': [i.id for i in items]}]},
        ])
        monkeypatch.setattr('bt.hub.ai.enabled', lambda: True)
        monkeypatch.setattr('bt.hub.ai._call', lambda *a, **k: next(outputs))
        prefs.collect(s, 1, at=1000)
        prefs.collect(s, 1, at=1100)
        lessons = s.exec(select(HubLesson).where(HubLesson.profile_id == 1)).all()
        assert len(lessons) == 1
        assert lessons[0].text == 'More local harbour infrastructure reporting'
        assert lessons[0].last_evidence_at == 200


def test_user_edited_lesson_is_never_rewritten(monkeypatch):
    with session() as s:
        items = [ContentItem(url=f'https://e.test/u{n}', type='meme', title='Cats at the office') for n in (1, 2)]
        s.add_all(items); s.flush()
        s.add_all([ItemState(profile_id=1, item_id=item.id, favorite=True) for item in items])
        lesson = HubLesson(profile_id=1, text='More cats at work', scope='meme', polarity=1,
                           user_edited=True, source_item_ids=[item.id for item in items], evidence=2)
        s.add(lesson); s.commit()
        monkeypatch.setattr('bt.hub.ai.enabled', lambda: True)
        monkeypatch.setattr('bt.hub.ai._call', lambda *a, **k: {'lessons': [
            {'text': 'More cats at work', 'scope': 'meme', 'polarity': 1, 'evidence_ids': [item.id for item in items]}]})
        prefs.collect(s, 1, at=time.time())
        s.refresh(lesson)
        assert lesson.text == 'More cats at work' and lesson.evidence == 2


def test_preference_nudge_is_scoped_and_profile_isolated():
    with session() as s:
        news = ContentItem(url='https://e.test/3', type='news', title='Harbour bridge update')
        meme = ContentItem(url='https://e.test/4', type='meme', title='Harbour bridge joke')
        s.add_all([news, meme]); s.flush()
        s.add_all([HubLesson(profile_id=1, text='local harbour infrastructure', scope='news', polarity=1),
                   HubLesson(profile_id=2, text='local harbour infrastructure', scope='news', polarity=-1)])
        s.commit()
        lesson = prefs.nudges(s, 1, at=time.time())
        base, _ = score.relevance(news, 50, score.Taste())
        bumped, why = score.apply_lessons(s, 1, news, base, [], at=time.time(), lessons=lesson)
        wrong_type, _ = score.apply_lessons(s, 1, meme, base, [], at=time.time(), lessons=lesson)
        other = prefs.nudges(s, 2, at=time.time())
        lowered, _ = score.apply_lessons(s, 2, news, base, [], at=time.time(), lessons=other)
        assert bumped > base and 'you prefer "harbour"' in why
        assert wrong_type == base
        assert lowered < base


def test_explicit_hidden_language_unhide_and_rehide_reactivates_lesson(monkeypatch):
    with session() as s:
        p = s.get(Profile, 1)
        p.prefs = {'hidden_meme_languages': ['de'], 'hidden_meme_languages_at': {'de': 100}}
        s.add(p); s.commit()
        monkeypatch.setattr('bt.hub.ai.enabled', lambda: False)
        prefs.collect(s, 1, at=1000)
        lesson = s.exec(select(HubLesson).where(HubLesson.profile_id == 1)).one()
        assert lesson.scope == 'meme' and lesson.polarity == -1 and 'de language' in lesson.text
        p.prefs = {'hidden_meme_languages': []}
        s.add(p); s.commit()
        prefs.collect(s, 1, at=1100)
        s.refresh(lesson)
        assert not lesson.active
        p.prefs = {'hidden_meme_languages': ['de'], 'hidden_meme_languages_at': {'de': 1200}}
        s.add(p); s.commit()
        prefs.collect(s, 1, at=1300)
        s.refresh(lesson)
        assert lesson.active and lesson.last_evidence_at == 1200
        lesson.user_edited = True
        lesson.active = False
        s.add(lesson); s.commit()
        prefs.collect(s, 1, at=1400)
        s.refresh(lesson)
        assert not lesson.active and lesson.last_evidence_at == 1200


def test_unmatched_typed_curator_term_is_preserved_as_explicit_evidence(monkeypatch):
    with session() as s:
        curator = Curator(profile_id=1, name='Meme Tracker', tuned_down=['obscure phrase'], content_types=['meme'])
        s.add(curator); s.flush()
        profile = s.get(Profile, 1)
        profile.prefs = {'curator_feedback_at': {f'{curator.id}:down:obscure phrase': 444}}
        s.add(profile); s.commit(); s.refresh(curator)
        evidence = prefs._evidence(s, 1)
        row = next(r for r in evidence if r.get('feedback_terms') == ['obscure phrase'])
        assert row['id'] < 0 and row['at'] == 444 and row['type'] == 'meme'
        monkeypatch.setattr('bt.hub.ai.enabled', lambda: True)
        monkeypatch.setattr('bt.hub.ai._call', lambda *a, **k: {'lessons': [
            {'text': 'Fewer obscure phrase memes', 'scope': 'meme', 'polarity': -1, 'evidence_ids': [row['id']]}]})
        prefs.collect(s, 1, at=1000)
        lesson = s.exec(select(HubLesson).where(HubLesson.profile_id == 1, HubLesson.text == 'Fewer obscure phrase memes')).one()
        assert lesson.last_evidence_at == 444 and lesson.source_item_ids == [row['id']]


def test_evidence_for_same_item_merges_explicit_provenance():
    with session() as s:
        item = ContentItem(url='https://e.test/matched', type='meme', title='A bridge meme', first_seen=100)
        curator = Curator(profile_id=1, name='Meme Tracker', tuned_down=['bridge'])
        s.add_all([item, curator]); s.flush()
        s.add_all([ItemState(profile_id=1, item_id=item.id, saved=True, saved_at=200),
                   CuratorDiscovery(profile_id=1, curator_id=curator.id, item_id=item.id)])
        s.commit()
        matching = [r for r in prefs._evidence(s, 1) if r['id'] == item.id]
        assert len(matching) == 1
        assert 'positive' in matching[0]['signals'] and 'typed curator feedback' in matching[0]['signals']
        assert matching[0]['feedback_terms'] == ['bridge']


def test_learning_respects_scope_capacity_and_video_busy(monkeypatch):
    with session() as s:
        items = [ContentItem(url=f'https://e.test/c{n}', type='news', title='Harbour bridge', first_seen=100) for n in (1, 2)]
        s.add_all(items); s.flush()
        s.add_all([ItemState(profile_id=1, item_id=item.id, saved=True, saved_at=200) for item in items])
        s.add_all([HubLesson(profile_id=1, text=f'Existing preference {n}', scope='news', polarity=1) for n in range(200)])
        s.commit()
        monkeypatch.setattr('bt.hub.ai.enabled', lambda: True)
        monkeypatch.setattr('bt.hub.master.judge.video_busy', lambda: False)
        monkeypatch.setattr('bt.hub.ai._call', lambda *a, **k: {'lessons': [
            {'text': 'Newly inferred interest', 'scope': 'news', 'polarity': 1, 'evidence_ids': [i.id for i in items]}]})
        prefs.collect(s, 1, at=1000)
        assert len(s.exec(select(HubLesson.id).where(HubLesson.profile_id == 1)).all()) == 200
        s.delete(s.exec(select(HubLesson).where(HubLesson.profile_id == 1).order_by(HubLesson.id.desc())).first())
        s.commit()
        monkeypatch.setattr('bt.hub.master.judge.video_busy', lambda: True)
        monkeypatch.setattr('bt.hub.ai._call', lambda *a, **k: pytest.fail('must yield while video curator is busy'))
        prefs.collect(s, 1, at=1100)
        assert len(s.exec(select(HubLesson.id).where(HubLesson.profile_id == 1)).all()) == 199
        assert s.get(HubPreferenceCore, 1) is not None


def test_overrule_action_timestamp_survives_repeated_pipeline_passes():
    with session() as s:
        action_at = time.time() - 1000
        item = ContentItem(url='https://e.test/overruled', type='news', title='Harbour funding', first_seen=action_at - 100)
        s.add(item); s.flush()
        s.add(CuratorDiscovery(profile_id=1, curator_id=1, item_id=item.id))
        s.add(ItemVerdict(profile_id=1, item_id=item.id, overruled='promoted', overruled_at=action_at,
                          judged_at=action_at, tier='feature', forced=True))
        s.commit()
        pipeline.run(s, 1, now=action_at + 500)
        pipeline.run(s, 1, now=action_at + 800)
        verdict = s.get(ItemVerdict, (1, item.id))
        evidence = next(row for row in prefs._evidence(s, 1) if row['id'] == item.id)
        assert verdict.judged_at == action_at + 800
        assert verdict.overruled_at == action_at
        assert evidence['at'] == action_at


def test_migration_adds_overrule_timestamp_to_existing_local_table():
    with db.engine.begin() as conn:
        conn.exec_driver_sql('ALTER TABLE itemverdict DROP COLUMN overruled_at')
        migrate.migrate(conn)
        columns = {c['name'] for c in __import__('sqlalchemy').inspect(conn).get_columns('itemverdict')}
        assert 'overruled_at' in columns
