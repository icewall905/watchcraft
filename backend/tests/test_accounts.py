"""Accounts, multi-login sessions, invites, device codes, admin and settings split (docs/ACCOUNTS.md)."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, select

from bt import accounts as A
from bt import accounts_cli, config
from bt.db import engine, init_db, session
from bt.models import Account, AccountSession, Profile, now

PW = 'correct horse battery'


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(config, 'AUTH_MODE', 'accounts')
    A._failures.clear()
    SQLModel.metadata.drop_all(engine)
    init_db()
    from bt.main import app
    yield app
    # Leave a legacy open-mode household behind for test modules that reuse the database.
    monkeypatch.setattr(config, 'AUTH_MODE', 'open')
    SQLModel.metadata.drop_all(engine)
    init_db()


def client(app):
    return TestClient(app)


def bootstrap(c, username='mika'):
    r = c.post('/api/auth/bootstrap', json={'username': username, 'password': PW})
    assert r.status_code == 200, r.text
    return r.json()['public_id']


def invite(c, username=None, role='user'):
    r = c.post('/api/admin/accounts', json={'username': username, 'role': role})
    assert r.status_code == 200, r.text
    return r.json()['link'].rsplit('/', 1)[1]


def register(c, token, username=None):
    r = c.post('/api/auth/register', json={'token': token, 'password': PW, 'username': username})
    assert r.status_code == 200, r.text
    return r.json()['public_id']


def test_fresh_install_bootstrap_then_closed(app):
    c = client(app)
    st = c.get('/api/auth/status').json()
    assert st['mode'] == 'accounts' and st['bootstrap'] and st['account'] is None
    assert c.get('/api/profiles').status_code == 401
    bootstrap(c)
    st = c.get('/api/auth/status').json()
    assert st['account']['username'] == 'mika' and st['account']['role'] == 'admin' and not st['bootstrap']
    assert [p['name'] for p in c.get('/api/profiles').json()] == ['Mika']
    other = client(app)
    assert other.post('/api/auth/bootstrap', json={'username': 'eve', 'password': PW}).status_code == 403


def test_existing_household_waits_for_assignment(app):
    with session() as s:
        s.add(Profile(name='Old family', role='admin'))
        s.commit()
    c = client(app)
    assert not c.get('/api/auth/status').json()['bootstrap']
    assert c.post('/api/auth/bootstrap', json={'username': 'x1', 'password': PW}).status_code == 403
    assert accounts_cli.main(['invite', 'ice', '--admin', '--base', 'https://hub.example']) == 0
    with session() as s:
        acc = s.exec(select(Account).where(Account.username == 'ice')).one()
        token = A.create_invite(s, acc, 'register')
    register(c, token)
    unowned = c.get('/api/admin/profiles/unowned').json()
    assert [p['name'] for p in unowned] == ['Old family']
    me = c.get('/api/auth/status').json()['account']['id']
    r = c.post(f"/api/admin/profiles/{unowned[0]['id']}/assign", json={'account_id': me})
    assert r.status_code == 200 and r.json()['role'] == 'adult'
    assert 'Old family' in [p['name'] for p in c.get('/api/profiles').json()]
    # Owned profiles can't be moved by an admin.
    assert c.post(f"/api/admin/profiles/{unowned[0]['id']}/assign", json={'account_id': me}).status_code == 400


def test_two_accounts_on_one_client_switch_and_isolation(app):
    c = client(app)
    mika = bootstrap(c)
    token = invite(c, 'daughter')
    ice = register(c, token)  # same client: now signed in to both, daughter active
    st = c.get('/api/auth/status').json()
    assert {a['username'] for a in st['accounts']} == {'mika', 'daughter'}
    assert st['account']['username'] == 'daughter' and st['active'] == ice
    daughter_profiles = c.get('/api/profiles').json()
    assert [p['name'] for p in daughter_profiles] == ['Daughter']
    c.post('/api/auth/switch', json={'public_id': mika})
    mika_profiles = c.get('/api/profiles').json()
    assert [p['name'] for p in mika_profiles] == ['Mika']
    # Another account's profile is invisible, even when named explicitly.
    assert c.get('/api/categories', headers={'X-Profile-Id': str(daughter_profiles[0]['id'])}).status_code == 404
    # A tab may pick its account with X-Account.
    r = c.get('/api/profiles', headers={'X-Account': ice})
    assert [p['name'] for p in r.json()] == ['Daughter']
    assert c.post('/api/auth/switch', json={'public_id': 'nope'}).status_code == 404
    # Non-admin account: no admin API.
    assert c.get('/api/admin/accounts', headers={'X-Account': ice}).status_code == 403
    # Logging out one account keeps the other.
    st = c.post('/api/auth/logout', json={'public_id': ice}).json()
    assert [a['username'] for a in st['accounts']] == ['mika'] and st['account']['username'] == 'mika'
    st = c.post('/api/auth/logout', json={'all': True}).json()
    assert st['account'] is None and st['accounts'] == []
    assert c.get('/api/profiles').status_code == 401


def test_signing_in_again_reuses_the_session(app):
    c = client(app)
    bootstrap(c)
    first = c.post('/api/auth/login', json={'username': 'mika', 'password': PW}).json()['public_id']
    second = c.post('/api/auth/login', json={'username': 'MIKA', 'password': PW}).json()['public_id']
    assert first == second
    with session() as s:
        assert len(s.exec(select(AccountSession)).all()) == 1


def test_revoked_and_disabled_sessions_stop_working(app):
    admin, tv = client(app), client(app)
    bootstrap(admin)
    tv.post('/api/auth/login', json={'username': 'mika', 'password': PW})
    devices = admin.get('/api/auth/devices').json()
    assert len(devices) == 2 and sum(d['current'] for d in devices) == 1
    other = next(d for d in devices if not d['current'])
    assert admin.delete(f"/api/auth/devices/{other['public_id']}").status_code == 200
    assert tv.get('/api/profiles').status_code == 401
    token = invite(admin, 'kid')
    kid = client(app)
    register(kid, token)
    kid_id = next(a['id'] for a in admin.get('/api/admin/accounts').json() if a['username'] == 'kid')
    admin.patch(f'/api/admin/accounts/{kid_id}', json={'disabled': True})
    assert kid.get('/api/profiles').status_code == 401
    assert kid.post('/api/auth/login', json={'username': 'kid', 'password': PW}).status_code == 401


def test_session_cookie_is_resent_daily_and_never_expires(app):
    c = client(app)
    pid = bootstrap(c)
    r = c.get('/api/profiles')
    assert 'wc_s_' not in r.headers.get('set-cookie', '')
    with session() as s:
        row = s.exec(select(AccountSession).where(AccountSession.public_id == pid)).one()
        row.last_seen_at = now() - timedelta(days=700)  # two years idle: still signed in
        s.add(row)
        s.commit()
    r = c.get('/api/profiles')
    assert r.status_code == 200
    cookie = r.headers['set-cookie']
    assert f'wc_s_{pid}=' in cookie and 'Max-Age=34560000' in cookie and 'HttpOnly' in cookie
    with session() as s:
        row = s.exec(select(AccountSession).where(AccountSession.public_id == pid)).one()
        row.last_seen_at = now() - A.SESSION_IDLE - timedelta(days=1)
        s.add(row)
        s.commit()
    assert c.get('/api/profiles').status_code == 401


def test_invites_are_single_use_expire_and_reset_signs_out(app):
    admin = client(app)
    bootstrap(admin)
    token = invite(admin)  # no username: the person picks one
    assert admin.get(f'/api/auth/invite/{token}').json() == {'username': None, 'role': 'user', 'purpose': 'register'}
    newbie = client(app)
    assert newbie.post('/api/auth/register', json={'token': token, 'password': 'short'}).status_code == 400
    assert newbie.post('/api/auth/register', json={'token': token, 'password': PW, 'username': 'mika'}).status_code == 409
    register(newbie, token, 'anna')
    assert newbie.post('/api/auth/register', json={'token': token, 'password': PW}).status_code == 404
    acc = next(a for a in admin.get('/api/admin/accounts').json() if a['username'] == 'anna')
    link = admin.post(f"/api/admin/accounts/{acc['id']}/reset").json()['link']
    assert admin.get(f"/api/auth/invite/{link.rsplit('/', 1)[1]}").json()['purpose'] == 'reset'
    stranger = client(app)
    stranger.post('/api/auth/register', json={'token': link.rsplit('/', 1)[1], 'password': 'a new password'})
    assert newbie.get('/api/profiles').status_code == 401  # reset signed the old device out
    assert stranger.get('/api/auth/status').json()['account']['username'] == 'anna'
    expired = invite(admin, 'late')
    with session() as s:
        from bt.models import AccountInvite
        for inv in s.exec(select(AccountInvite)).all():
            inv.expires_at = now() - timedelta(seconds=1)
            s.add(inv)
        s.commit()
    assert admin.get(f'/api/auth/invite/{expired}').status_code == 404


def test_tv_sign_in_with_code(app):
    phone, tv = client(app), client(app)
    bootstrap(phone)
    start = tv.post('/api/auth/device/start', json={'device_label': 'Living room TV'}).json()
    assert len(start['code']) == 7 and start['link'].startswith('/link?code=')
    assert tv.post('/api/auth/device/poll', json={'poll': start['poll']}).json() == {'status': 'pending'}
    assert tv.post('/api/auth/device/approve', json={'code': start['code']}).status_code == 401
    assert phone.post('/api/auth/device/approve', json={'code': 'WRONG1'}).status_code == 404
    assert phone.post('/api/auth/device/approve', json={'code': start['code'].lower()}).json()['username'] == 'mika'
    r = tv.post('/api/auth/device/poll', json={'poll': start['poll']}).json()
    assert r['status'] == 'approved' and r['account']['username'] == 'mika'
    assert tv.get('/api/profiles').status_code == 200
    assert tv.post('/api/auth/device/poll', json={'poll': start['poll']}).status_code == 410
    labels = [d['device_label'] for d in phone.get('/api/auth/devices').json()]
    assert 'Living room TV' in labels


def test_login_is_throttled(app):
    c = client(app)
    bootstrap(c)
    other = client(app)
    for _ in range(10):
        assert other.post('/api/auth/login', json={'username': 'mika', 'password': 'nope nope'}).status_code == 401
    assert other.post('/api/auth/login', json={'username': 'mika', 'password': PW}).status_code == 429


def test_settings_split(app):
    admin, user = client(app), client(app)
    bootstrap(admin)
    register(user, invite(admin, 'anna'))
    assert user.put('/api/settings/appearance', json={'theme': 'ocean'}).status_code == 200
    assert user.get('/api/settings').json()['appearance']['theme'] == 'ocean'
    assert admin.get('/api/settings').json()['appearance']['theme'] == 'projector'
    assert user.put('/api/settings/team', json={'max_parallel': 2}).status_code == 403
    assert admin.put('/api/settings/team', json={'max_parallel': 2}).status_code == 200
    assert user.get('/api/settings').json()['team']['max_parallel'] == 2


def test_profiles_belong_to_the_account_and_pins_stay_optional(app):
    c = client(app)
    bootstrap(c)
    assert c.post('/api/profiles', json={'name': 'Date night', 'role': 'admin'}).status_code == 400
    me = c.get('/api/profiles').json()[0]['id']  # PINs stay optional, even next to child profiles
    kid = c.post('/api/profiles', json={'name': 'Fun with the kids', 'role': 'child'},
                 headers={'X-Profile-Id': str(me)})
    assert kid.status_code == 200, kid.text
    kid_id = kid.json()['id']
    assert c.get('/api/admin/accounts', headers={'X-Profile-Id': str(kid_id)}).status_code == 403
    assert c.post('/api/profiles', json={'name': 'x'}, headers={'X-Profile-Id': str(kid_id)}).status_code == 403
    assert c.get('/api/admin/accounts', headers={'X-Profile-Id': str(me)}).status_code == 200
    # Another account (non-admin) with children: its adult profiles may stay open.
    user = client(app)
    register(user, _invite_as(c, me, 'anna'))
    assert user.post('/api/profiles', json={'name': 'Kids', 'role': 'child'}).status_code == 200


def _invite_as(c, pid, username):
    r = c.post('/api/admin/accounts', json={'username': username, 'role': 'user'}, headers={'X-Profile-Id': str(pid)})
    assert r.status_code == 200, r.text
    return r.json()['link'].rsplit('/', 1)[1]


def test_admin_keeps_one_admin_and_cannot_delete_self(app):
    c = client(app)
    bootstrap(c)
    me = c.get('/api/auth/status').json()['account']['id']
    assert c.patch(f'/api/admin/accounts/{me}', json={'role': 'user'}).status_code == 400
    assert c.delete(f'/api/admin/accounts/{me}').status_code == 400
    register(client(app), invite(c, 'anna'))
    anna = next(a for a in c.get('/api/admin/accounts').json() if a['username'] == 'anna')
    assert anna['registered'] and anna['profiles'][0]['name'] == 'Anna' and anna['sessions'] == 1
    assert c.delete(f"/api/admin/accounts/{anna['id']}").status_code == 200
    with session() as s:
        assert not s.exec(select(Profile).where(Profile.name == 'Anna')).first()


def test_media_cookie_selects_profile_without_header(app):
    c = client(app)
    pid = bootstrap(c)
    first = c.get('/api/profiles').json()[0]['id']
    second = c.post('/api/profiles', json={'name': 'Adult lists'}).json()['id']
    c.cookies.set(f'wc_profile_{pid}', str(second))
    r = c.post('/api/categories', json={'name': 'Cookie picked'})
    assert r.status_code == 200, r.text
    names = [x['name'] for x in c.get('/api/categories', headers={'X-Profile-Id': str(second)}).json()]
    assert 'Cookie picked' in names
    assert 'Cookie picked' not in [x['name'] for x in c.get('/api/categories', headers={'X-Profile-Id': str(first)}).json()]


def test_cli_assigns_unowned_profiles(app, capsys):
    with session() as s:
        s.add(Profile(name='Fredagsfest', role='admin'))
        s.add(Profile(name='Lily', role='child'))
        s.commit()
    assert accounts_cli.main(['invite', 'tv']) == 0
    assert accounts_cli.main(['assign', 'Fredagsfest', 'tv']) == 0
    assert accounts_cli.main(['assign', 'Fredagsfest', 'tv']) == 1  # already owned
    assert accounts_cli.main(['assign', 'Nobody', 'tv']) == 1
    with session() as s:
        p = s.exec(select(Profile).where(Profile.name == 'Fredagsfest')).one()
        tv = s.exec(select(Account).where(Account.username == 'tv')).one()
        assert p.account_id == tv.id and p.role == 'adult' and tv.role == 'user'
    accounts_cli.main(['profiles'])
    assert 'Lily' in capsys.readouterr().out


def test_review_fixes_isolation(app):
    from bt.models import AgentEvent, Category, Download
    admin, user = client(app), client(app)
    bootstrap(admin)
    register(user, invite(admin, 'anna'))
    anna_pid = user.get('/api/profiles').json()[0]['id']
    with session() as s:
        cat = Category(profile_id=anna_pid, name='Secret list')
        s.add(cat)
        s.add(AgentEvent(kind='judge', profile_id=anna_pid, message='anna private event'))
        s.add(Download(video_id='vid1', status='done', requested_by_profile=anna_pid))
        s.commit()
    # Agent feed: an account sees only its own events.
    assert 'anna private event' not in str(admin.get('/api/agent/events').json())
    assert 'anna private event' in str(user.get('/api/agent/events').json())
    # Stats are admin-only and scoped to the admin's own playlists.
    assert user.get('/api/stats/page').status_code == 403
    page = admin.get('/api/stats/page')
    assert page.status_code == 200 and 'Secret list' not in page.text
    # Server secrets are hidden from non-admins.
    admin.put('/api/settings/notify', json={'webhook_url': 'https://hooks.example/secret'})
    assert 'hooks.example' not in user.get('/api/settings').text
    assert 'hooks.example' in admin.get('/api/settings').text
    # Downloads: no wiping the shared library; own saved videos may go.
    assert user.delete('/api/downloads').status_code == 403
    assert user.delete('/api/downloads/vid1').status_code == 200
    # A stale X-Account never falls back to another signed-in account.
    pid = admin.get('/api/auth/status').json()['active']
    assert admin.get('/api/profiles', headers={'X-Account': 'gone'}).status_code == 401
    assert admin.get('/api/profiles', headers={'X-Account': pid}).status_code == 200


def test_admin_sets_rails_for_a_child_on_another_account(app):
    c = client(app)
    bootstrap(c)
    me = c.get('/api/profiles').json()[0]['id']
    kid_account = client(app)
    register(kid_account, _invite_as(c, me, 'ria'))
    ria = kid_account.get('/api/profiles').json()[0]
    with session() as s:  # an admin made this account's profile a child (as on prod)
        p = s.get(Profile, ria['id'])
        p.role = 'child'
        s.add(p); s.commit()
    hk = {'X-Profile-Id': str(ria['id'])}
    # The child's own account cannot set them; the profile is out of the admin's own Profiles scope.
    assert kid_account.put(f"/api/profiles/{ria['id']}/safety-rails", json={'rails': 'x'}, headers=hk).status_code == 403
    assert kid_account.put(f"/api/admin/profiles/{ria['id']}/safety-rails", json={'rails': 'x'}, headers=hk).status_code == 403
    assert c.put(f"/api/profiles/{ria['id']}/safety-rails", json={'rails': 'x'}).status_code == 404
    r = c.put(f"/api/admin/profiles/{ria['id']}/safety-rails", json={'rails': 'She is 8.'})
    assert r.status_code == 200 and r.json()['safety_rails'] == 'She is 8.'
    listed = next(a for a in c.get('/api/admin/accounts').json() if a['username'] == 'ria')
    assert listed['profiles'][0]['safety_rails'] == 'She is 8.'
    assert c.put(f"/api/admin/profiles/{me}/safety-rails", json={'rails': 'x'}).status_code == 400
    # Parental switches too: the admin sets them, the child's own account cannot.
    assert kid_account.put(f"/api/admin/profiles/{ria['id']}/parental", json={'controls': {'boards': True}}, headers=hk).status_code == 403
    r = c.put(f"/api/admin/profiles/{ria['id']}/parental", json={'controls': {'boards': False, 'yt_search': False}})
    assert r.status_code == 200 and r.json()['controls']['boards'] is False
    assert kid_account.get('/api/hub/boards', headers=hk).status_code == 403
