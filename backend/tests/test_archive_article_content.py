"""Archive structured article text and require usable summaries for every chunk."""
import json

import pytest

from bt.hub import capture
from bt.hub.extract import TEXT_LIMIT, extract_page


@pytest.mark.parametrize('graph', [False, True])
def test_structured_article_body_replaces_application_shell(graph):
    article = {'@type': 'NewsArticle', 'headline': 'Full report',
               'articleBody': 'First paragraph. <b>Essential evidence.</b> Final detail.'}
    data = {'@graph': [{'@type': 'Organization', 'name': 'Publisher'}, article]} if graph else article
    html = ('<title>Report</title><main>Loading application</main>'
            '<script type="application/ld+json">' + json.dumps(data) + '</script>').encode()
    item = extract_page(html, 'https://example.org/report')
    assert item['body'] == 'First paragraph. Essential evidence. Final detail.'
    assert item['title'] == 'Full report'


@pytest.mark.parametrize('body', ['  ', '<div></div>'])
def test_empty_structured_body_preserves_readable_html(body):
    html = ('<p>Readable article.</p><script type="application/ld+json">' + json.dumps({
        '@type': 'Article', 'articleBody': body}) + '</script>').encode()
    assert extract_page(html, 'https://example.org/report')['body'] == 'Readable article.'


def test_structured_article_body_obeys_archive_limit():
    html = ('<script type="application/ld+json">' + json.dumps({
        '@type': 'Article', 'articleBody': 'x' * (TEXT_LIMIT + 20)}) + '</script>').encode()
    assert len(extract_page(html, 'https://example.org/report')['body']) == TEXT_LIMIT


def test_short_structured_excerpt_does_not_replace_full_html_article():
    full = 'The complete report includes all the evidence and an essential ending.'
    html = ('<article><p>' + full + '</p></article><script type="application/ld+json">' +
            json.dumps({'@type': 'Article', 'articleBody': 'Brief excerpt.'}) + '</script>').encode()
    assert extract_page(html, 'https://example.org/report')['body'] == full


@pytest.mark.parametrize('result', [{0: ''}, {0: ' \n\t '}, {1: 'Wrong chunk'}, {0: None}])
def test_summary_rejects_missing_or_empty_map_results(monkeypatch, result):
    monkeypatch.setattr(capture.ai, 'summarize_items', lambda items: result)
    with pytest.raises(ValueError, match='every part'):
        capture.full_summary('Report', 'Full article text')


def test_summary_rejects_empty_reduce_result(monkeypatch):
    results = iter([{0: 'First part', 1: 'Final part'}, {0: '  '}])
    monkeypatch.setattr(capture.ai, 'summarize_items', lambda items: next(results))
    with pytest.raises(ValueError, match='final summary'):
        capture.full_summary('Report', 'a' * 12001)


def test_summary_includes_article_tail(monkeypatch):
    calls = []

    def summarize(items):
        calls.append(items)
        return {n: ' Summary ' for n in range(len(items))}

    monkeypatch.setattr(capture.ai, 'summarize_items', summarize)
    assert capture.full_summary('Report', 'a' * 12000 + 'Important ending') == 'Summary'
    assert calls[0][1]['body'] == 'Important ending'
    assert calls[1][0]['body'] == 'Summary\nSummary'
