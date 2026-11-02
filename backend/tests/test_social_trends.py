import json

import pytest

from bt.hub import social_trends as trends


def status(**kwargs):
    return {'id': '5', 'uri': 'https://origin.example/users/alice/statuses/5',
            'url': 'https://origin.example/@alice/5', 'content': '<p>A post</p>',
            'visibility': 'public', 'account': {'acct': 'alice'}, **kwargs}


def fake(monkeypatch, responses):
    calls = []
    def fetch(url, **kw):
        calls.append((url, kw))
        return json.dumps(responses.pop(0)).encode()
    monkeypatch.setattr(trends.social_fetch, 'fetch', fetch)
    return calls


@pytest.mark.parametrize('path', ['', '/', '/api/v1/trends', '/api/v1/timelines/public', '/api/v1/trends/links?limit=100'])
def test_explicit_endpoint_required(path):
    with pytest.raises(ValueError):
        trends.mastodon_trends('https://social.example' + path)


def test_status_identity_type_sensitive_and_public_only(monkeypatch):
    calls = fake(monkeypatch, [[status(), status(id='99'), status(uri='https://origin.example/other', sensitive=True),
                                status(visibility='private'), status(reblog={'id': '2'})]])
    items = trends.mastodon_trends('https://social.example/api/v1/trends/statuses', config={'item_type': 'article'})
    assert len(items) == 1
    assert items[0]['type'] == 'article'
    assert items[0]['external_id'] == 'mstdn:https://origin.example/users/alice/statuses/5'
    assert items[0]['url'] == 'https://origin.example/users/alice/statuses/5'
    assert items[0]['meta']['display_url'] == 'https://origin.example/@alice/5'
    assert items[0]['meta']['observed_at'].endswith('+00:00')
    assert items[0]['meta']['origin'] == 'origin.example'
    assert 'not verified' in items[0]['meta']['evidence']
    assert calls[0][1]['limit'] == 2_000_000
    assert calls[0][1]['kinds'] == ('application/json',)


def test_tags_bounded_three_timelines_and_deduplicates(monkeypatch):
    calls = fake(monkeypatch, [[{'name': name} for name in ['science', 'SCIENCE', '../bad', 'tech', 'news', 'fourth']],
                              [status()], [status()], [status()]])
    assert len(trends.mastodon_trends('https://social.example/api/v1/trends/tags', 'ignored')) == 1
    assert len(calls) == 4
    assert [url.split('/tag/')[-1] for url, _ in calls[1:]] == ['science?limit=20', 'tech?limit=20', 'news?limit=20']


def test_links_article_url_and_observation(monkeypatch):
    fake(monkeypatch, [[{'url': 'https://news.example/story', 'title': 'News', 'description': '<b>Claim</b>',
                         'history': [{'uses': '10'}], 'provider_name': 'Publisher'},
                        {'url': 'javascript:evil()', 'title': 'Bad'},
                        {'url': 'https://news.example/story', 'title': 'Duplicate'}]])
    items = trends.mastodon_trends('https://social.example/api/v1/trends/links')
    assert len(items) == 1
    assert items[0]['type'] == 'news'
    assert items[0]['url'] == 'https://news.example/story'
    assert items[0]['body'] == ''
    assert 'Sampled public trends' in items[0]['meta']['coverage']
    assert items[0]['meta']['observations']['history'] == [{'uses': '10'}]


def test_sensitive_opt_in_and_bad_config(monkeypatch):
    fake(monkeypatch, [[status(sensitive=True)]])
    assert trends.mastodon_trends('https://social.example/api/v1/trends/statuses', config={'include_sensitive': True})
    with pytest.raises(ValueError):
        trends.validate_config({'item_type': 'video'})
    with pytest.raises(ValueError):
        trends.validate_config({'include_sensitive': 'yes'})


def test_response_must_be_list(monkeypatch):
    fake(monkeypatch, [{'error': 'login required'}])
    with pytest.raises(ValueError):
        trends.mastodon_trends('https://social.example/api/v1/trends/statuses')


def test_language_filter_and_validation(monkeypatch):
    fake(monkeypatch, [[status(language='da'), status(uri='https://origin.example/english', language='en'),
                        status(uri='https://origin.example/unknown')]])
    items = trends.mastodon_trends('https://social.example/api/v1/trends/statuses', config={'languages': ['da']})
    assert len(items) == 1
    assert items[0]['meta']['language'] == 'da'
    with pytest.raises(ValueError):
        trends.validate_config({'languages': 'en'})
    with pytest.raises(ValueError):
        trends.validate_config({'languages': ['en?bad']})


def test_language_filter_for_link_cards_is_not_guessed(monkeypatch):
    fake(monkeypatch, [[{'url': 'https://news.example/unknown', 'title': 'Unknown'},
                       {'url': 'https://news.example/danish', 'title': 'Known', 'language': 'da'}]])
    items = trends.mastodon_trends('https://social.example/api/v1/trends/links', config={'languages': ['da']})
    assert len(items) == 1
    assert items[0]['url'].endswith('/danish')
    assert items[0]['meta']['origin'] == 'news.example'


@pytest.mark.parametrize('source', ['http://social.example/api/v1/trends/links',
                                   'https://user:password@social.example/api/v1/trends/links',
                                   'https://social.example/api/v1/trends/links#fragment'])
def test_source_rejects_credentials_http_and_fragment(source):
    with pytest.raises(ValueError):
        trends.mastodon_trends(source)


def test_content_warnings_excluded_unless_explicit_opt_in(monkeypatch):
    fake(monkeypatch, [[status(spoiler_text='Content warning', sensitive=False)]])
    assert trends.mastodon_trends('https://social.example/api/v1/trends/statuses') == []
    fake(monkeypatch, [[status(spoiler_text='Content warning', sensitive=False)]])
    assert trends.mastodon_trends('https://social.example/api/v1/trends/statuses', config={'include_sensitive': True})


def test_config_matches_source_rule_validation():
    from bt.hub.watchers import normalize
    config = {'item_type': 'web', 'languages': ['en', 'pt-BR'], 'include_sensitive': False}
    assert trends.validate_config(normalize('mastodon_trends', config)) == {
        **config, 'languages': ['en', 'pt-br']}
    for bad in [{'languages': ['EN']}, {'languages': ['en'] * 11}, {'languages': ['en-123']}, []]:
        with pytest.raises(ValueError):
            trends.validate_config(bad)


def test_federated_uri_identity_is_shared_across_instances(monkeypatch):
    fake(monkeypatch, [[status(id='1')], [status(id='900')]])
    a = trends.mastodon_trends('https://one.example/api/v1/trends/statuses')[0]
    b = trends.mastodon_trends('https://two.example/api/v1/trends/statuses')[0]
    assert a['url'] == b['url']
    assert a['external_id'] == b['external_id']
    assert a['meta']['instance'] != b['meta']['instance']
