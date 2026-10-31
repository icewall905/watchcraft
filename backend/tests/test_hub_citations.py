import pytest

from bt.hub import ai
from bt.hub.models import ContentItem


@pytest.mark.parametrize('text', ['No citation.', 'Wrong [0].', 'Wrong [2].'])
def test_cited_summary_rejects_missing_or_invalid_references(monkeypatch, text):
    monkeypatch.setattr(ai, '_call', lambda *args, **kwargs: {'text': text})
    with pytest.raises(ValueError):
        ai._cited([ContentItem(url='https://example.com', title='Example')], 'Summarize', 100)


def test_cited_summary_accepts_valid_reference(monkeypatch):
    monkeypatch.setattr(ai, '_call', lambda *args, **kwargs: {'text': 'A fact [1].'})
    assert ai._cited([ContentItem(url='https://example.com', title='Example')], 'Summarize', 100) == 'A fact [1].'


def test_cited_summary_rejects_partially_cited_prose(monkeypatch):
    monkeypatch.setattr(ai, '_call', lambda *args, **kwargs: {'text': 'A fact [1]. An unsupported claim.'})
    with pytest.raises(ValueError, match='uncited'):
        ai._cited([ContentItem(url='https://example.com', title='Example')], 'Summarize', 100)


def test_cited_summary_rejects_uncited_lowercase_sentence(monkeypatch):
    monkeypatch.setattr(ai, '_call', lambda *args, **kwargs: {'text': 'A fact [1]. lower-case unsupported claim.'})
    with pytest.raises(ValueError, match='uncited'):
        ai._cited([ContentItem(url='https://example.com', title='Example')], 'Summarize', 100)


def test_cited_summary_includes_source_url_in_retrieval_context(monkeypatch):
    seen = {}

    def call(_system, user, *_args, **_kwargs):
        seen['user'] = user
        return {'text': 'A fact [1].'}

    monkeypatch.setattr(ai, '_call', call)
    ai._cited([ContentItem(url='https://source.example/story', title='Example', publisher='Source')], 'Summarize', 100)
    assert 'https://source.example/story' in seen['user']


@pytest.mark.parametrize('text', [
    'Acme Inc. reported a release at https://source.example/story [1].',
    'The U.S. team published a result [1]. A second source confirmed it [1].',
])
def test_cited_summary_accepts_abbreviations_and_urls(monkeypatch, text):
    monkeypatch.setattr(ai, '_call', lambda *args, **kwargs: {'text': text})
    assert ai._cited([ContentItem(url='https://example.com', title='Example')], 'Summarize', 100) == text
