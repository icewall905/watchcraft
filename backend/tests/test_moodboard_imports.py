import time
import pytest
from sqlmodel import select
from test_curation import client
from test_moodboards import make
from bt.db import session
from bt.hub import moodboard_imports as imp, moodboards
from bt.hub.models import MoodImport, MoodPin, MoodAsset

FEED = b'''<rss><channel><item><title>Heart dress</title><link>https://www.pinterest.com/pin/123/</link><description>&lt;img src="https://i.pinimg.com/236x/ab/cd/ef/test.jpg"&gt;Dress</description></item><item><link>https://evil.example/pin/456/</link><description>&lt;img src="http://127.0.0.1/secret"&gt;</description></item></channel></rss>'''

@pytest.mark.parametrize('url', ['http://www.pinterest.com/u/b/', 'https://pinterest.com@127.0.0.1/u/b/', 'https://www.pinterest.com:444/u/b/', 'https://www.pinterest.com/pin/123/', 'https://pin.it/123', 'https://www.pinterest.com/u/b/extra', 'https://www.pinterest.com/u/feed.rss', 'https://www.pinterest.com.evil/u/b/'])
def test_reject_invalid_links(url):
    with pytest.raises(ValueError): imp.board_url(url)


def test_feed_sanitizes_provider_data(monkeypatch):
    calls=[]
    monkeypatch.setattr(imp.social_fetch,'fetch',lambda url,**kw:calls.append(url) or FEED)
    pins=imp.feed('https://pinterest.com/user/board/?tracking=yes')
    assert calls==['https://www.pinterest.com/user/board.rss']
    assert len(pins)==1 and pins[0]['source_url']=='https://www.pinterest.com/pin/123/'
    for raw in (b'<!DOCTYPE rss [<!ENTITY a "x">]><rss/>','<!DOCTYPE rss><rss/>'.encode('utf-16')):
        monkeypatch.setattr(imp.social_fetch,'fetch',lambda *a,**kw:raw)
        with pytest.raises(ValueError): imp.feed('https://pinterest.com/u/b/')


def test_import_local_dedup_isolation_delete(client,monkeypatch):
    b=make(client,enabled=False);path=f"/api/hub/boards/{b['id']}/pinterest"
    monkeypatch.setattr(imp.social_fetch,'fetch',lambda *a,**kw:FEED)
    monkeypatch.setattr(moodboards,'capture_image',lambda url:(b'local','image/jpeg',50,80))
    assert client.post(path,json={'url':'https://www.pinterest.com/u/b/'}).status_code==200
    assert client.post(path,json={'url':'https://www.pinterest.com/u/b/'}).status_code==409
    assert imp.dispatch_due()
    job=client.get(path).json()['import']
    assert job['status']=='complete' and job['imported']==1 and job['total']==1 and job['skipped']==0
    assert 'lease' not in job and 'processed' not in job
    pin=client.get(f"/api/hub/boards/{b['id']}").json()['pins'][0]
    assert pin['saved'] and client.get(pin['image_url']).content==b'local'
    other=client.post('/api/profiles',json={'name':'Other'}).json();h={'X-Profile-Id':str(other['id'])}
    assert client.get(path,headers=h).status_code==404
    assert client.post(path,headers=h,json={'url':'https://www.pinterest.com/u/b/'}).status_code==404
    client.post(path,json={'url':'https://www.pinterest.com/u/b/'});assert imp.dispatch_due()
    assert client.get(path).json()['import']['skipped']==1
    client.delete(f"/api/hub/boards/{b['id']}")
    with session() as s:
        assert not s.exec(select(MoodImport)).all() and not s.exec(select(MoodAsset)).all()


def test_interrupted_import_recovery_keeps_counters(client,monkeypatch):
    b=make(client,enabled=False);path=f"/api/hub/boards/{b['id']}/pinterest"
    jid=client.post(path,json={'url':'https://pinterest.com/u/b/'}).json()['import']['id']
    with session() as s:
        job=s.get(MoodImport,jid);job.status='importing';job.lease='dead';job.lease_until=time.time()-1
        job.processed=['https://www.pinterest.com/pin/123/'];job.imported=1;job.total=1;s.add(job);s.commit()
    monkeypatch.setattr(imp.social_fetch,'fetch',lambda *a,**kw:FEED)
    monkeypatch.setattr(moodboards,'capture_image',lambda url:pytest.fail('already processed'))
    assert imp.dispatch_due()
    job=client.get(path).json()['import'];assert job['status']=='complete' and job['imported']==1 and job['skipped']==0


def test_deleted_during_capture_does_not_resurrect_data(client,monkeypatch):
    b=make(client,enabled=False);path=f"/api/hub/boards/{b['id']}/pinterest"
    client.post(path,json={'url':'https://pinterest.com/u/b/'})
    monkeypatch.setattr(imp.social_fetch,'fetch',lambda *a,**kw:FEED)
    def capture(url):
        client.delete(f"/api/hub/boards/{b['id']}")
        return b'local','image/jpeg',20,20
    monkeypatch.setattr(moodboards,'capture_image',capture);assert imp.dispatch_due()
    with session() as s:
        assert not s.exec(select(MoodPin)).all() and not s.exec(select(MoodImport)).all()
