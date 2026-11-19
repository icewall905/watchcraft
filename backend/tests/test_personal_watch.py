from test_access import client, headers  # noqa: F401

from bt.hub import personal_watch as watch, watcher_push as push

PATH = '/api/hub/watcher'
URL = 'https://carrier.example/tracking'


def test_page_watch_baseline_failure_updates_and_profile_isolation(client, monkeypatch):
    stored = client.post(PATH, json={'goal': 'Watch my parcel', 'plan': {'source': URL, 'selector': '#status'},
                                     'interval': 10})
    assert stored.status_code == 200, stored.text
    watch_id = stored.json()['id']
    operation = f'{PATH}/watches/{watch_id}'
    other = headers(client.post('/api/profiles', json={'name': 'Other'}).json())
    assert client.get(PATH, headers=other).json() == []
    assert client.patch(operation, json={'enabled': False}, headers=other).status_code == 404

    samples = iter(['In transit', None, 'Out for delivery', 'Out for delivery'])

    def page(url, config):
        assert url == URL and config == {'content_selector': '#status'}
        body = next(samples)
        if body is None:
            raise ValueError('upstream errors are temporary')
        return [{'url': url, 'body': f'Parcel history\n{body}'}]

    monkeypatch.setattr(watch.providers, 'page_watch', page)
    assert watch.check_one()
    assert not watch.check_one()
    assert client.get(f'{PATH}/updates').json() == []
    for expected_error in [True, False, False]:
        assert client.patch(operation, json={'enabled': True}).status_code == 200
        assert watch.check_one()
        assert bool(client.get(PATH).json()[0]['error']) == expected_error

    event, = client.get(f'{PATH}/updates?unread=true').json()
    assert event['text'] == 'Out for delivery'
    assert client.get(f'{PATH}/updates', headers=other).json() == []
    read = f"{PATH}/updates/{event['id']}/read"
    assert client.post(read, headers=other).status_code == 404
    assert client.post(read).status_code == 200
    assert client.get(f'{PATH}/updates?unread=true').json() == []
    assert client.delete(operation).status_code == 200
    assert client.get(f'{PATH}/updates').json() == []


def test_search_watches_complete_each_condition_once(client, monkeypatch):
    monkeypatch.setattr(watch.ai, 'enabled', lambda: True)
    terms = ['27B is publicly released', 'Flash is publicly released']
    monkeypatch.setattr(watch.ai, '_call', lambda *_args, **_kwargs: {
        'mode': 'bing_news', 'source': 'Qwen 4 releases', 'terms': terms, 'selector': '', 'question': ''})
    plan = client.post(f'{PATH}/draft', json={'goal': 'Watch Qwen releases'}).json()['plan']
    response = client.post(PATH, json={'goal': 'Watch Qwen releases', 'plan': plan, 'interval': 60})
    assert response.status_code == 200, response.text
    watch_id = response.json()['id']
    operation = f'{PATH}/watches/{watch_id}'
    rows = [{'url': 'https://example.org/27b', 'title': '27B release'},
            {'url': 'https://example.org/flash', 'title': 'Flash release'}]
    monkeypatch.setattr(watch.providers.PROVIDERS['bing_news'], 'run', lambda *_: rows)
    matches = iter([[{'condition': 1, 'item': 1}],
                    [{'condition': 1, 'item': 1}, {'condition': 2, 'item': 9}, {'condition': 2, 'item': 2}]])
    monkeypatch.setattr(watch.ai, '_call', lambda *_args, **_kwargs: {'matches': next(matches)})
    assert watch.check_one()
    assert client.patch(operation, json={'enabled': True}).status_code == 200
    assert watch.check_one()
    assert [event['text'] for event in client.get(f'{PATH}/updates').json()] == ['Flash release', '27B release']
    assert client.get(PATH).json()[0]['completed']
    assert client.patch(operation, json={'enabled': True}).status_code == 409


def test_push_retries_and_does_not_replay_or_skip_after_deletion(client, monkeypatch):
    monkeypatch.setenv('BT_WATCHER_NTFY_URL', 'https://ntfy.example')
    android = f'{PATH}/android'
    sent = []
    clock = 1000

    class Clock:
        @staticmethod
        def time():
            return clock

    def send(device, event_id):
        sent.append((device.token, event_id))
        if len(sent) == 1:
            raise TimeoutError
        return True

    def update():
        with watch.session() as s:
            event = watch.WatchEvent(watch_id=1, profile_id=1, text='Parcel changed', url=URL, created_at=clock)
            s.add(event)
            s.commit()
            return event.id

    monkeypatch.setattr(push, 'time', Clock)
    monkeypatch.setattr(push, 'send', send)
    previous = update()
    paired = client.post(android)
    assert paired.status_code == 200
    token = paired.json()['endpoint'].rsplit('/', 1)[1]
    assert token.startswith('wc_') and len(token) == 63
    assert client.post(android).json()['endpoint'] == paired.json()['endpoint']
    assert not push.dispatch_one()
    latest = update()
    assert latest > previous and push.dispatch_one()
    assert not push.dispatch_one()
    clock += 61
    assert push.dispatch_one()
    assert not push.dispatch_one()
    assert sent == [(token, latest), (token, latest)]
    with watch.session() as s:
        s.delete(s.get(watch.WatchEvent, latest))
        s.commit()
    newest = update()
    assert newest > latest and push.dispatch_one()
    assert sent[-1] == (token, newest)
    assert client.delete(android).status_code == 200
    update()
    assert not push.dispatch_one()


def test_notification_is_profile_scoped_and_uses_the_push_queue(client, monkeypatch):
    monkeypatch.setenv('BT_WATCHER_NTFY_URL', 'https://ntfy.example')
    android = f'{PATH}/android'
    paired = client.post(android)
    assert paired.status_code == 200
    token = paired.json()['endpoint'].rsplit('/', 1)[1]
    other = headers(client.post('/api/profiles', json={'name': 'Other'}).json())
    assert client.post(android, headers=other).json()['endpoint'] != paired.json()['endpoint']
    assert client.delete(android, headers=other).status_code == 200
    result = client.post(f'{PATH}/updates/test')
    assert result.status_code == 200, result.text
    event = result.json()
    assert event['watch_id'] == 0 and event['unread']
    assert client.get(f'{PATH}/updates?unread=true').json() == [event]
    assert client.get(f'{PATH}/updates?kind=test').json() == [event]
    assert client.get(f'{PATH}/updates?kind=watch').json() == []
    assert client.get(f'{PATH}/updates?unread=true', headers=other).json() == []
    assert client.post(f'{PATH}/updates/test').status_code == 429
    sent = []
    monkeypatch.setattr(push, 'send', lambda device, event_id: sent.append((device.token, event_id)) or True)
    assert push.dispatch_one()
    assert sent == [(token, event['id'])]
    assert client.post(f"{PATH}/updates/{event['id']}/read").status_code == 200
    assert client.get(f'{PATH}/updates?unread=true').json() == []



def test_ntfy_payload_contains_only_generic_alert_data(monkeypatch):
    monkeypatch.setenv('BT_WATCHER_NTFY_URL', 'https://ntfy.example')
    monkeypatch.delenv('BT_WATCHER_NTFY_TOKEN', raising=False)
    device = push.WatchDevice(token='wc_' + 'a' * 60, profile_id=7, cursor=0)

    def publish(url, **kwargs):
        assert url == 'https://ntfy.example'
        assert not kwargs['follow_redirects']
        assert kwargs['headers'] == {}
        assert kwargs['json'] == {'topic': device.token, 'title': 'The Watcher',
                                  'message': 'You have a new Watcher update.', 'priority': 4, 'tags': ['watcher-42']}
        return push.httpx.Response(200, request=push.httpx.Request('POST', url))

    monkeypatch.setattr(push.httpx, 'post', publish)
    assert push.send(device, 42)
