"""Humor provenance is distinct from general short clips and hashtag spam."""
from types import SimpleNamespace

import pytest

from bt.hub.meme_quality import humor_curator, humor_scope, is_meme_candidate


def item(community='!programmerhumor@lemmy.example', **updates):
    data = dict(type='meme', provider='lemmy', title='Me when the tests pass',
                meta={'community': community, 'media': {'type': 'image', 'url': 'https://cdn.example/a.jpg'}})
    data.update(updates)
    return data


@pytest.mark.parametrize('community', ['r/memes', '!linuxmemes@lemmy.example', '#programminghumor', 'funny'])
def test_visual_humor_communities_need_no_caption_keywords(community):
    assert is_meme_candidate(item(community))
    assert is_meme_candidate(SimpleNamespace(**item(community)))


@pytest.mark.parametrize('community', ['!technology@lemmy.example', '@bbcnews', '#linux', 'funny.example', 'lolcats'])
def test_general_sources_are_not_humor_evidence(community):
    assert not humor_scope(community)
    assert not is_meme_candidate(item(community))


def test_hashtags_scope_a_post_but_do_not_replace_media_or_excuse_promotions():
    tagged = item('#linux', meta={'tags': ['linux', 'memes'], 'media': {'type': 'image', 'url': 'https://cdn.example/a.jpg'}})
    assert is_meme_candidate(tagged)
    assert not is_meme_candidate({**tagged, 'title': 'Comedy tonight! Buy now'})
    assert not is_meme_candidate(item(meta={'tags': ['memes']}))
    assert not is_meme_candidate(item(meta={'tags': ['memes'], 'sensitive': True, 'media': {'type': 'image', 'url': 'https://cdn.example/a.jpg'}}))


def test_video_moments_and_humor_tracker_have_different_intent():
    assert humor_curator(SimpleNamespace(content_types=['meme'], name='Meme Tracker', queries=['linux memes']))
    assert not humor_curator(SimpleNamespace(content_types=['meme'], name='Video Moments', queries=['world news today', 'science explained']))
    assert not humor_curator(SimpleNamespace(content_types=['news'], name='Memes in the news', queries=[]))


def test_event_ads_need_both_event_url_and_promotional_call_to_action():
    assert not is_meme_candidate(item(body='Weitere Informationen: https://www.kabarett-kaarst.de/events/fun'))
    assert is_meme_candidate(item(body='The tickets to my breakdown sold out'))
    assert is_meme_candidate(item(body='A joke about https://example.com/events/fun'))
    assert is_meme_candidate(item(body='Weitere Informationen: this is a joke'))


def test_explicit_caption_hashtags_and_legacy_images():
    assert is_meme_candidate(item('@bbcnews', title='Me when it crashes #memes'))
    assert not is_meme_candidate(item('@bbcnews', title='The latest news about memes'))
    assert is_meme_candidate(item(meta={'community': 'r/memes'}, image='https://cdn.example/old.jpg'))
    assert is_meme_candidate(item(meta={'community': 'r/memes'}), has_media=True)


def test_sql_and_python_eligibility_agree():
    from sqlalchemy import create_engine
    from sqlmodel import Session, select
    from bt.hub.models import ContentItem
    from bt.hub.meme_quality import candidate_clause

    samples = [item(), item('#linux'), item('@bbcnews', title='A crash #memes'),
               item(title='Subscribe to my channel'), item(body='Tickets on sale https://host.test/events/show'),
               item(body='Weitere Informationen: https://www.kabarett-kaarst.de/events/show'),
               item(body='A joke about https://host.test/events/show'),
               item(meta={'community': 'r/memes'}, image='https://cdn.example/legacy.jpg'),
               item(meta={'community': 'r/memes'}), item(meta={'tags': ['linux', 'memes'], 'media': {'url': 'https://cdn.example/1', 'type': 'video'}}),
               item(meta={'community': '!memes@host.test', 'sensitive': True}, image='https://cdn.example/1')]
    engine = create_engine('sqlite://')
    ContentItem.__table__.create(engine)
    with Session(engine) as session:
        rows = [ContentItem(url=f'https://post.test/{n}', **data) for n, data in enumerate(samples)]
        session.add_all(rows)
        session.commit()
        sql_ids = set(session.exec(select(ContentItem.id).where(candidate_clause(ContentItem))).all())
        assert sql_ids == {row.id for row in rows if is_meme_candidate(row)}


def test_me_irl_is_a_meme_community():
    assert is_meme_candidate(item('r/me_irl'))
    assert humor_scope('!me_irl@lemmy.example')


def test_generic_hiding_feedback_does_not_mean_avoid_all_memes():
    from bt.hub import engine
    from bt.hub.master import score
    from bt.hub.models import ContentItem, Curator, HubLesson
    meme = ContentItem(type='meme', title='This meme is funny', url='https://example.test/m')
    assert score.relevance(meme, 60, score.Taste(disliked=['meme','funny','this'])) == (60, [])
    assert engine.tune(Curator(tuned_down=['meme','funny']), meme.model_dump(), 60, '') == (60, '')
    assert engine._taste_nudge(meme.model_dump(), 60, '', [], ['meme','funny']) == (60, '')
    political = ContentItem(type='meme', title='Political meme', url='https://example.test/p')
    assert score.relevance(political, 60, score.Taste(disliked=['political']))[0] < 60
    lessons = [HubLesson(profile_id=1, text='Fewer memes about this', scope='meme', polarity=-1)]
    assert score.apply_lessons(None, 1, meme, 60, [], lessons=lessons) == (60, [])
