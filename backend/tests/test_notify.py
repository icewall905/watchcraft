"""Alert delivery to webhook, Home Assistant and email, and its settings contract."""
import os
import tempfile

os.environ.setdefault('BT_DATA_DIR', tempfile.mkdtemp(prefix='wc-notify-'))

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel

from bt import db
from bt.db import init_db, save_setting, session
from bt.hub import notify
from bt.hub.models import Alert, ContentItem
from bt.main import app

H = {'X-Profile-Id': '1'}


@pytest.fixture(autouse=True)
def _schema():
    """Tests that do not use the client still need the tables."""
    init_db()


@pytest.fixture
def client():
    with db.engine.begin() as conn:
        conn.exec_driver_sql('DROP TABLE IF EXISTS content_fts')
    SQLModel.metadata.drop_all(db.engine)
    init_db()
    yield TestClient(app, raise_server_exceptions=True)
    SQLModel.metadata.drop_all(db.engine)
    init_db()


def _alert(severity='high', title='Something happened'):
    with session() as s:
        item = ContentItem(url=f'https://example.org/{title.replace(" ", "-")}', type='news', title=title,
                           summary='summary', publisher='Example')
        s.add(item); s.flush()
        a = Alert(profile_id=1, item_id=item.id, severity=severity, title=title, reason='because')
        s.add(a); s.commit(); s.refresh(a)
        return a.id


def _configure(**values):
    save_setting('notify', {**db.get_settings()['notify'], **values})


def test_channels_report_configuration_without_secrets():
    _configure(webhook_url='https://example.org/hook', email_host='smtp.example.org',
               email_to='me@example.org', email_password='hunter2')
    got = notify.channels()
    assert got == {'webhook': True, 'home_assistant': False, 'email': True}
    assert 'hunter2' not in str(got)


def test_dispatch_sends_alerts_at_or_above_the_threshold_once(client, monkeypatch):
    sent = []

    def fake_post(url, json=None, **kwargs):
        sent.append((url, json))
        class R:
            status_code = 204
            def raise_for_status(self): pass
        return R()

    monkeypatch.setattr(notify.httpx, 'post', fake_post)
    _configure(webhook_url='https://example.org/hook', min_severity='high')
    low = _alert('low', 'a low one')
    high = _alert('high', 'a high one')
    assert notify.dispatch(throttled=False)['sent'] == 1
    assert [p['id'] for _, p in sent] == [high]
    assert sent[0][1]['event'] == 'watchcraft.alert' and sent[0][1]['severity'] == 'high'
    # The cursor advanced past both, so a second pass sends nothing.
    assert notify.dispatch(throttled=False)['sent'] == 0
    assert len(sent) == 1
    assert low != high


def test_dispatch_is_a_no_op_without_channels(client):
    assert notify.dispatch(throttled=False) == {'skipped': 'no channels configured'}


def test_a_failing_channel_is_reported_and_does_not_stop_the_others(client, monkeypatch):
    def boom(*_a, **_k):
        raise ConnectionError('down')

    monkeypatch.setattr(notify.httpx, 'post', boom)
    _configure(webhook_url='https://example.org/hook', ha_webhook_url='http://ha.local/api/webhook/x',
               min_severity='info')
    _alert('info', 'anything')
    result = notify.dispatch(throttled=False)
    assert result['sent'] == 1
    assert result['results']['webhook'].startswith('failed:') and result['results']['home_assistant'].startswith('failed:')


def test_test_notification_needs_a_channel(client):
    with pytest.raises(ValueError):
        notify.test()


def test_settings_redact_the_email_password_and_validate_input(client):
    assert client.put('/api/settings/notify', json={'email_password': 'hunter2', 'email_host': 'smtp.example.org',
                                                    'email_to': 'me@example.org'}, headers=H).status_code == 200
    got = client.get('/api/settings').json()['notify']
    assert got['email_password'] == '********' and got['email_host'] == 'smtp.example.org'
    # Saving the redacted value keeps the stored password.
    client.put('/api/settings/notify', json={'email_password': '********', 'email_to': 'other@example.org'}, headers=H)
    with session() as s:
        assert db.get_settings(s)['notify']['email_password'] == 'hunter2'
    assert client.put('/api/settings/notify', json={'webhook_url': 'ftp://nope'}, headers=H).status_code == 400
    assert client.put('/api/settings/notify', json={'email_port': 0}, headers=H).status_code == 400
    assert client.put('/api/settings/notify', json={'min_severity': 'urgent'}, headers=H).status_code == 400


def test_test_endpoint_reports_a_missing_channel(client):
    assert client.post('/api/notify/test', headers=H).status_code == 422
