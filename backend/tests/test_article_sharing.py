"""Public capabilities expose only one selected archive and revoke immediately."""
from test_curation import client  # noqa: F401
from test_master_api import add, other_profile
from bt import auth
from bt.db import session
from bt.hub.models import ContentItem, CapturedAsset, ItemState


def test_public_share_without_login_is_minimal_escaped_and_revocable(client, monkeypatch):
    iid = add(title='<script>bad()</script>')
    with session() as s:
        item = s.get(ContentItem, iid)
        item.body = 'Full archived article <img src=x onerror=alert(1)>'
        item.enrichment = {'summary': 'A quick overview', 'private_prompt': 'SECRET_PROMPT'}
        s.add(item)
        s.add(ItemState(profile_id=1, item_id=iid, notes='SECRET_NOTE', tags=['SECRET_TAG']))
        s.add(CapturedAsset(item_id=iid, kind='image', mime='image/png', data=b'picture', size=7))
        s.commit()
    assert client.get(f'/api/hub/items/{iid}/share').json() == {'path': None}
    path = client.post(f'/api/hub/items/{iid}/share').json()['path']
    assert client.post(f'/api/hub/items/{iid}/share').json()['path'] == path
    monkeypatch.setattr(auth, 'PASSWORD', 'secret-test-password')
    assert client.get(f'/api/hub/items/{iid}').status_code == 401
    response = client.get(path)
    assert response.status_code == 200
    assert 'Full archived article &lt;img' in response.text
    assert '&lt;script&gt;' in response.text and '<script>' not in response.text
    assert 'A quick overview' in response.text
    for private in ('SECRET_PROMPT', 'SECRET_NOTE', 'SECRET_TAG', 'profile_id', 'curators'):
        assert private not in response.text
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['referrer-policy'] == 'no-referrer'
    assert 'noindex' in response.headers['x-robots-tag']
    assert client.get(path+'/image').content == b'picture'
    assert client.get('/share/'+'x'*43).status_code == 404
    monkeypatch.setattr(auth, 'PASSWORD', '')
    assert client.delete(f'/api/hub/items/{iid}/share').json() == {'path': None}
    assert client.get(path).status_code == 404
    assert client.get(path+'/image').status_code == 404
    assert client.post(f'/api/hub/items/{iid}/share').json()['path'] != path


def test_other_profiles_cannot_create_discover_or_revoke_share(client):
    iid = add()
    path = client.post(f'/api/hub/items/{iid}/share').json()['path']
    _, headers = other_profile(client)
    for method in (client.get, client.post, client.delete):
        assert method(f'/api/hub/items/{iid}/share', headers=headers).status_code == 404
    assert client.get(path).status_code == 200


def test_same_article_has_independently_revocable_profile_links(client):
    iid = add()
    first = client.post(f'/api/hub/items/{iid}/share').json()['path']
    pid, headers = other_profile(client)
    with session() as s:
        s.add(ItemState(item_id=iid, profile_id=pid))
        s.commit()
    second = client.post(f'/api/hub/items/{iid}/share', headers=headers).json()['path']
    assert second != first
    client.delete(f'/api/hub/items/{iid}/share', headers=headers)
    assert client.get(first).status_code == 200
    assert client.get(second).status_code == 404


def test_deleting_profile_removes_its_public_links(client):
    iid = add()
    path = client.post(f'/api/hub/items/{iid}/share').json()['path']
    from bt.hub.api import delete_profile_data
    with session() as s:
        delete_profile_data(s, 1)
        s.commit()
    assert client.get(path).status_code == 404


def test_share_does_not_serve_active_svg_images(client):
    iid = add()
    with session() as s:
        s.add(CapturedAsset(item_id=iid, kind='image', mime='image/svg+xml', data=b'<svg/>', size=6))
        s.commit()
    path = client.post(f'/api/hub/items/{iid}/share').json()['path']
    assert '<img ' not in client.get(path).text
    assert client.get(path+'/image').status_code == 404


def test_share_owner_can_revoke_after_discovery_is_removed(client):
    from sqlalchemy import delete
    from bt.hub.models import CuratorDiscovery
    iid = add()
    path = client.post(f'/api/hub/items/{iid}/share').json()['path']
    with session() as s:
        s.exec(delete(CuratorDiscovery).where(CuratorDiscovery.item_id == iid))
        s.commit()
    assert client.get(f'/api/hub/items/{iid}').status_code == 404
    assert client.get(path).status_code == 404  # losing source access also disables public delivery
    assert client.get(f'/api/hub/items/{iid}/share').json()['path'] == path
    assert client.delete(f'/api/hub/items/{iid}/share').status_code == 200
    assert client.get(path).status_code == 404
