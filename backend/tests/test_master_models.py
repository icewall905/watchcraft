"""Master curator storage: verdicts, stories, editions and learned taste, and their profile cleanup."""
import os
import tempfile

os.environ.setdefault('BT_DATA_DIR', tempfile.mkdtemp(prefix='wc-master-'))

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import SQLModel, select

from bt import db
from bt.db import init_db, session
from bt.hub.api import delete_profile_data
from bt.hub.models import ContentItem, Edition, HubLesson, HubPreferenceCore, ItemVerdict, Story, StoryItem


@pytest.fixture(autouse=True)
def fresh_db():
    with db.engine.begin() as conn:
        conn.exec_driver_sql('DROP TABLE IF EXISTS content_fts')
    SQLModel.metadata.drop_all(db.engine)
    init_db()
    yield


def test_verdicts_stories_and_editions_round_trip():
    with session() as s:
        item = ContentItem(url='https://news.example/a', type='article', title='A thing happened')
        s.add(item); s.flush()
        story = Story(kind='news', title='A thing happened', lead_item_id=item.id)
        s.add(story); s.flush()
        s.add(StoryItem(item_id=item.id, story_id=story.id))
        s.add(ItemVerdict(profile_id=1, item_id=item.id, quality=80, relevance=70, final=75, tier='feature',
                          reasons=['trusted source']))
        s.add(Edition(profile_id=1, day='2026-11-03', sections=[{'key': 'stories', 'story_ids': [story.id]}]))
        s.commit()
        v = s.get(ItemVerdict, (1, item.id))
        assert (v.tier, v.reasons, v.overruled) == ('feature', ['trusted source'], '')
        assert s.exec(select(Edition)).one().sections[0]['story_ids'] == [story.id]


def test_one_edition_per_profile_and_day():
    with session() as s:
        s.add(Edition(profile_id=1, day='2026-11-03'))
        s.commit()
        s.add(Edition(profile_id=1, day='2026-11-03'))
        with pytest.raises(IntegrityError):
            s.commit()


def test_deleting_a_profile_removes_its_curator_state_but_not_shared_stories():
    with session() as s:
        item = ContentItem(url='https://news.example/b', type='article', title='B')
        s.add(item); s.flush()
        story = Story(title='B', lead_item_id=item.id)
        s.add(story); s.flush()
        s.add(StoryItem(item_id=item.id, story_id=story.id))
        for pid in (1, 2):
            s.add(ItemVerdict(profile_id=pid, item_id=item.id))
            s.add(Edition(profile_id=pid, day='2026-11-03'))
            s.add(HubLesson(profile_id=pid, text='Fewer reaction memes'))
            s.add(HubPreferenceCore(profile_id=pid, text='Likes long reads'))
        s.commit()
        delete_profile_data(s, 2)
        s.commit()
        for model in (ItemVerdict, Edition, HubLesson, HubPreferenceCore):
            assert [r.profile_id for r in s.exec(select(model)).all()] == [1]
        assert s.exec(select(Story)).one().title == 'B'
        assert s.exec(select(StoryItem)).one().item_id == item.id
