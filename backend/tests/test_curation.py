import os
import tempfile
os.environ.setdefault('BT_DATA_DIR', tempfile.mkdtemp(prefix='wc-tests-'))
import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, select
from bt import curation
from bt.db import engine, init_db, session
from bt.main import app

@pytest.fixture
def client():
    SQLModel.metadata.drop_all(engine)
    init_db()
    # No lifespan scheduler during deterministic API tests.
    yield TestClient(app)
    SQLModel.metadata.drop_all(engine)
    init_db()

def test_discovery_save_and_profile_isolation(client, monkeypatch):
    body={'name':'AI', 'provider':'rss', 'source':'https://example.org/feed'}
    c=client.post('/api/curation/curators',json=body).json()
    monkeypatch.setattr(curation,'fetch',lambda url: b'<rss><channel><item><title>Release</title><link>https://example.org/release#one</link></item></channel></rss>')
    curation.run(c['id'])
    items=client.get('/api/curation/discoveries').json()
    assert len(items)==1 and items[0]['url']=='https://example.org/release'
    assert client.post(f"/api/curation/discoveries/{items[0]['id']}/save").json()['saved']
    assert len(client.get('/api/curation/discoveries?saved=true').json())==1
    other=client.post('/api/profiles',json={'name':'Other'}).json()
    h={'X-Profile-Id':str(other['id'])}
    assert client.get('/api/curation/discoveries',headers=h).json()==[]
    assert client.post(f"/api/curation/curators/{c['id']}/run",headers=h).status_code==404
    assert client.post(f"/api/curation/discoveries/{items[0]['id']}/save",headers=h).status_code==404
    client.post(f"/api/curation/curators/{c['id']}/run")
    curation.run(c['id'])
    assert len(client.get('/api/curation/discoveries').json())==1

def test_private_network_and_entity_rejection(monkeypatch):
    monkeypatch.setattr(curation.socket,'getaddrinfo', lambda *a,**k: [(2,1,6,'',('127.0.0.1',443))])
    with pytest.raises(ValueError,match='public'): curation.fetch('https://example.org/')
    c=curation.Curator(profile_id=1,name='feed',source='https://example.org/feed')
    monkeypatch.setattr(curation,'fetch',lambda url: b'<!DOCTYPE rss><rss/>')
    with pytest.raises(ValueError,match='entities'): curation.discover(c)

def test_github_and_failure_isolation(client,monkeypatch):
    monkeypatch.setattr(curation,'fetch',lambda url: b'[{"name":"v1", "html_url":"https://github.com/a/b/releases/tag/v1"}]')
    c=curation.Curator(profile_id=1,name='repo',source='https://github.com/a/b',provider='github')
    assert curation.discover(c)[0][0]=='v1'
    row=client.post('/api/curation/curators',json={'name':'Bad','provider':'rss','source':'https://example.org/feed'}).json()
    monkeypatch.setattr(curation,'discover',lambda c: (_ for _ in ()).throw(ValueError('secret')))
    curation.run(row['id'])
    result=client.get('/api/curation/curators').json()[0]
    assert result['error'] and 'secret' not in result['error'] and result['next_run']>0

def test_pin_protection(client):
    p=client.post('/api/profiles',json={'name':'Private','pin':'1234'}).json()
    assert client.get('/api/curation/curators',headers={'X-Profile-Id':str(p['id'])}).status_code==403

def test_profile_deletion_removes_private_curators(client):
    p=client.post('/api/profiles',json={'name':'Temporary'}).json()
    h={'X-Profile-Id':str(p['id'])}
    client.post('/api/curation/curators',headers=h,json={'name':'Private','provider':'rss','source':'https://example.org/feed'})
    assert client.delete(f"/api/profiles/{p['id']}").status_code==200
    with session() as s:
        assert not s.exec(select(curation.Curator).where(curation.Curator.profile_id==p['id'])).all()
