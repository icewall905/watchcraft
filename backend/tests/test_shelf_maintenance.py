"""Durable bounded refill respects controls, leases and read-only status access."""
import time

from test_curation import client  # noqa: F401
from sqlmodel import select
from bt.db import session,save_setting
from bt.hub import shelf, shelf_maintenance as upkeep
from bt.hub.models import Curator,ContentItem,CuratorDiscovery,ItemVerdict
from bt.hub.master import edition

NOW=1_800_000_000.0


def add_curator(s,cid,**kw):
    values=dict(id=cid,profile_id=1,name=f'Interest {cid}',content_types=['news'],
                interval_hours=6,next_run=NOW+3600,last_run=NOW-7200,last_status='ok')
    values.update(kw);c=Curator(**values);s.add(c);return c


def inventory(ids):
    return {'articles':{'needs_refill':True,'interests':[{'id':i,'ready':i,'target':20,'needs_refill':True} for i in ids]}}


def test_refill_is_bounded_and_claim_survives_restarts(client,monkeypatch):
    monkeypatch.setattr(upkeep.time,'time',lambda:NOW)
    with session() as s:
        for cid in range(1,6):add_curator(s,cid)
        s.commit()
    monkeypatch.setattr(shelf,'snapshot',lambda *args:inventory(range(1,6)))
    result=upkeep.maintain(NOW)
    assert result['profiles']['1']['collectors_queued']==[1,2,3]
    assert upkeep.maintain(NOW+1)=={'checked':False}
    with session() as s:
        assert [c.id for c in s.exec(select(Curator).where(Curator.next_run==NOW)).all()]==[1,2,3]
        assert len(upkeep.status(s,1)['collectors_queued'])==3
        assert upkeep.status(s,999)['collectors_queued']==[]


def test_failed_manual_paused_active_and_recent_collectors_are_protected(client,monkeypatch):
    with session() as s:
        add_curator(s,1,last_status='failed');add_curator(s,2,interval_hours=0)
        add_curator(s,3,enabled=False);add_curator(s,4,last_status='running')
        add_curator(s,5,last_run=NOW-10);add_curator(s,6,error='temporarily unavailable')
        s.commit()
    monkeypatch.setattr(shelf,'snapshot',lambda *args:inventory(range(1,7)))
    assert upkeep.maintain(NOW)['profiles']['1']['collectors_queued']==[]
    with session() as s:assert all(c.next_run==NOW+3600 for c in s.exec(select(Curator)).all())


def test_completed_refill_is_not_reported_as_still_running(client):
    with session() as s:
        add_curator(s,1,next_run=time.time()+3600)
        save_setting(upkeep.KEY,{'profiles':{'1':{'checked_at':NOW,'collectors_queued':[1]}}},s=s)
        s.commit()
        assert upkeep.status(s,1)['collectors_queued']==[]


def test_shelf_endpoint_accepts_real_timestamp_and_does_not_schedule_work(client):
    r=client.get('/api/hub/shelf')
    assert r.status_code==200,r.text
    assert r.headers['cache-control']=='private, no-store'
    assert r.json()['videos']['ready']==0
    with session() as s:
        from bt.models import Setting
        assert s.get(Setting,upkeep.KEY) is None


def test_expired_delivered_article_is_replaced_but_promotion_is_preserved(client):
    with session() as s:
        ids=[]
        for n in range(3):
            it=ContentItem(url=f'https://age.test/{n}',type='article',title=f'Article {n}',
                           published_at=NOW-8*86400 if n<2 else NOW-3600)
            s.add(it);s.flush();ids.append(it.id)
            s.add(CuratorDiscovery(profile_id=1,curator_id=1,item_id=it.id))
            s.add(ItemVerdict(profile_id=1,item_id=it.id,tier='keep',final=80,
                              overruled='promoted' if n==1 else ''))
        s.commit()
        assert edition._alive(s,1,ids,NOW)==set(ids[1:])
        offered=edition.desired(s,1,NOW)
        assert {e['item_id'] for es in offered.values() for e in es}==set(ids[1:])


def test_soft_source_rotation_preserves_all_items_and_top_quality():
    rows=[]
    for n in range(8):
        it=ContentItem(id=n+1,url=f'https://rotate.test/{n}',publisher='One publisher',type='news')
        rows.append((90.,NOW,n+1,None,it,None))
    other=ContentItem(id=99,url='https://different.test/',publisher='Another source',type='news')
    rows.append((85.,NOW,99,None,other,None))
    rows.sort(key=lambda r:r[:3],reverse=True)
    varied=edition._varied(rows)
    assert varied[0][0]==90
    assert next(i for i,r in enumerate(varied) if r[2]==99)<=4
    assert {r[2] for r in varied}=={r[2] for r in rows}


def test_manual_change_between_read_and_schedule_update_wins(client,monkeypatch):
    with session() as s:
        add_curator(s,1);s.commit()
    monkeypatch.setattr(shelf,'snapshot',lambda *args:inventory([1]))
    real_update=upkeep.update
    def concurrent_change(model):
        if model is Curator:
            with session() as other:
                c=other.get(Curator,1);c.interval_hours=0;other.add(c);other.commit()
        return real_update(model)
    monkeypatch.setattr(upkeep,'update',concurrent_change)
    assert upkeep.maintain(NOW)['profiles']['1']['collectors_queued']==[]
    with session() as s:assert s.get(Curator,1).next_run==NOW+3600


def test_preparation_backlog_does_not_bypass_discovery_cooldown(client,monkeypatch):
    from types import SimpleNamespace
    from bt.agent.loop import Worker
    from bt.agent import data,planner
    w=Worker.__new__(Worker)
    w.rt=SimpleNamespace(check=lambda:None,take_run_now=lambda:None,focus_category_id=None,yt=SimpleNamespace())
    w.rl=SimpleNamespace(blocker=lambda:False,wait_blocker=lambda **kw:False)
    w._paused_announced=False
    w._research_one=lambda trace:False
    w._timed_ai=lambda event,trace:False
    cat=SimpleNamespace(id=1)
    monkeypatch.setattr(data,'category_needs',lambda *args:[data.Need(cat,0,1,{'found':1})])
    monkeypatch.setattr(planner,'discover',lambda *args: (_ for _ in ()).throw(AssertionError('cooldown bypassed')))
    assert w.iterate() is False
