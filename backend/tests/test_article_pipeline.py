"""Article controls use the shared feedback pipeline and scoped selection memory."""
from test_curation import client, make_curator  # noqa: F401
from bt.db import session
from bt.models import Feedback, Lesson
from bt.hub.models import ContentItem, CuratorDiscovery, ItemState, ItemVerdict
from bt.hub import article_preferences, ai
from bt.hub.master import judge
from sqlmodel import select


def article(client, kind='news'):
    curator = make_curator(client, [{'provider': 'rss', 'url': 'https://example.org/feed'}])
    with session() as s:
        item = ContentItem(url='https://example.org/article', title='A useful article', type=kind,
                           summary='A substantial report on local engineering')
        s.add(item);s.flush()
        s.add(CuratorDiscovery(curator_id=curator['id'],profile_id=1,item_id=item.id,score=80))
        s.add(ItemVerdict(profile_id=1,item_id=item.id,tier='keep',final=70))
        s.commit();return item.id


def test_article_feedback_is_durable_and_undo_restores_the_article(client,monkeypatch):
    monkeypatch.setattr(article_preferences,'changed',lambda pid:None)
    iid=article(client)
    result=client.post(f'/api/hub/items/{iid}/feedback',json={'rating':1,'chips':['Useful'],'text':'More practical engineering'}).json()
    assert result['state']['rating']==1
    assert result['feedback']['item_id']==iid and result['feedback']['content_type']=='article'
    assert result['feedback']['video_id']=='' and result['feedback']['processed'] is False
    assert client.get('/api/feedback').json()==[]
    assert len(client.get('/api/feedback?content_type=article').json())==1
    down=client.post(f'/api/hub/items/{iid}/feedback',json={'rating':-1}).json()
    assert down['state']['dismissed'] and down['state']['rating']==-1
    undo=client.post(f'/api/hub/items/{iid}/feedback',json={'rating':0}).json()
    assert undo['state']['rating']==0 and not undo['state']['dismissed']
    assert client.post(f'/api/hub/items/{iid}/feedback',json={'rating':999}).status_code==422
    assert client.post('/api/hub/items/999999/feedback',json={'rating':1}).status_code==404
    other=client.post('/api/profiles',json={'name':'Other'}).json()
    assert client.post(f'/api/hub/items/{iid}/feedback',json={'rating':1},headers={'X-Profile-Id':str(other['id'])}).status_code==404


def test_shared_article_rules_enter_article_ranking_only(client,monkeypatch):
    iid=article(client)
    with session() as s:
        s.add(Lesson(profile_id=1,content_type='article',text='Prefer deeply researched engineering articles.'))
        s.add(Lesson(profile_id=1,content_type='video',text='Prefer quiet stop motion videos.'))
        s.commit()
        context=article_preferences.context(s,1)
        assert 'deeply researched engineering' in context and 'stop motion' not in context
        assert 'deeply researched engineering' not in judge.reader(s,1,include_articles=False)
    calls=[]
    def rank_call(system,user,*args,**kwargs):
        calls.append(user)
        return {'scores':[{'n':1,'score':80,'reason':'Good reporting'}]}
    monkeypatch.setattr(ai,'_call',rank_call)
    from bt.hub.models import Curator
    ai.rank(Curator(profile_id=1,name='Mixed source'),[{'type':'news','title':'Article'},{'type':'project','title':'Code project'}])
    assert len(calls)==2
    assert 'deeply researched engineering' in calls[0]
    assert 'deeply researched engineering' not in calls[1]


def test_master_judge_batches_article_memory_separately(client,monkeypatch):
    article(client)
    with session() as s:
        item=ContentItem(url='https://example.org/code',title='A code project',type='project')
        s.add(item);s.flush();s.add(CuratorDiscovery(curator_id=1,profile_id=1,item_id=item.id,score=80))
        s.add(ItemVerdict(profile_id=1,item_id=item.id,tier='keep',final=72))
        s.add(Lesson(profile_id=1,content_type='article',text='Prefer thoroughly sourced articles.'));s.commit()
    calls=[]
    def score(system,user,*args,**kwargs):
        calls.append(user);return {'verdicts':[{'n':1,'quality':90,'relevance':90,'reason':'Useful evidence'}]}
    monkeypatch.setattr(ai,'enabled',lambda:True);monkeypatch.setattr(ai,'_call',score)
    monkeypatch.setattr(judge,'worker_busy',lambda:False);monkeypatch.setattr(judge,'video_busy',lambda:False)
    with session() as s:
        result=judge.judge(s,1)
    assert result['judged']==2
    assert len(calls)==2
    assert sum('thoroughly sourced articles' in call for call in calls)==1


def test_durable_article_votes_do_not_also_train_the_legacy_hub_learner(client, monkeypatch):
    monkeypatch.setattr(article_preferences,'changed',lambda pid:None)
    iid=article(client)
    client.post(f'/api/hub/items/{iid}/feedback',json={'rating':1,'text':'Prefer practical reports'})
    from bt.hub.master import prefs
    with session() as s:
        assert iid not in {row['id'] for row in prefs._evidence(s,1)}
        assert s.exec(select(Feedback).where(Feedback.content_type=='article')).one().processed is False


def test_article_votes_do_not_nudge_other_media_and_override_saved_status(client):
    from bt.hub import engine
    from bt.hub.master import score
    with session() as s:
        for n in range(3):
            item=ContentItem(url=f'https://example.org/scoped/{n}',title=f'Engineering article {n}',type='news',publisher='Journal')
            s.add(item);s.flush()
            s.add(ItemState(profile_id=1,item_id=item.id,saved=True,rating=-1,dismissed=True))
            s.add(Feedback(profile_id=1,video_id='',content_type='article',item_id=item.id,rating=-1))
        s.commit()
        assert 'engineering' in engine.taste(s,1,article=True)[1]
        assert 'engineering' not in engine.taste(s,1)[0]+engine.taste(s,1)[1]
        assert score.load_taste(s,1,article=True).publishers['Journal']<0
        assert 'Journal' not in score.load_taste(s,1).publishers
