"""Publisher body boundaries and widgets must not pollute offline reading."""
import json

from bt.hub.extract import extract_page


def body(html):
    return extract_page(html.encode(), 'https://example.org/news/report')['body']


def test_semantic_body_excludes_chrome_and_inline_widgets_without_truncation():
    html = '''<a>Skip to content</a><header>Subscribe Sign in</header>
    <article><h1>Report</h1><div data-gu-name="body">
      <p>The report opens with evidence.</p>
      <aside>Read our related stories</aside>
      <div class="article-body__newsletter"><p>Sign up for daily updates</p></div>
      <p>The newsletter industry is discussed in the actual report.</p>
      <div data-component="read-more"><a>Unrelated recommendation</a></div>
      <div data-component="rich-link"><a>Read more</a></div><h2>Findings</h2><p>The essential ending survives.</p>
      <ul><li>First result</li><li>Second result</li></ul>
      <blockquote>A source quotation.</blockquote>
    </div><div class="related-stories">More news</div></article><footer>Topics</footer>'''
    text = body(html)
    assert text.startswith('The report opens with evidence.')
    assert 'newsletter industry is discussed' in text
    assert 'The essential ending survives.' in text
    assert 'Findings\n\n' in text
    assert 'First result\n\nSecond result' in text
    assert 'A source quotation.' in text
    for clutter in ['Skip to content', 'Subscribe', 'daily updates', 'Unrelated', 'Read more', 'More news', 'Topics']:
        assert clutter not in text


def test_structured_body_shorter_than_document_chrome_preserves_paragraphs():
    text = 'The substantive opening of the article provides clear evidence and context.\n\nThe final paragraph contains the reporting and its conclusion.'
    html = '<main>' + ('Navigation link and unrelated stories. ' * 100) + '</main>'
    html += '<script type="application/ld+json">' + json.dumps({'@type': 'NewsArticle', 'articleBody': text}) + '</script>'
    assert body(html) == text


def test_largest_article_body_and_inline_links_preserve_sentences():
    html = '<article><p>Related card.</p></article><article><p>The <a>actual report</a> retains punctuation.</p><p>Its final paragraph remains available.</p></article>'
    assert body(html) == 'The actual report retains punctuation.\n\nIts final paragraph remains available.'


def test_generic_page_retains_recipe_social_and_technical_content():
    html = '<main><h1>Recipe notes</h1><p>Our recommendation: add salt.</p><div>Social message</div><pre>run --newsletter</pre><p>Final instruction</p></main>'
    text = body(html)
    for expected in ['Recipe notes', 'Our recommendation: add salt.', 'Social message', 'run --newsletter', 'Final instruction']:
        assert expected in text


def test_guardian_body_boundary_and_event_sales_list():
    html = '''<div data-gu-name="body"><div class="article-body-viewer-selector">
    <p>Original reporting about an event and its consequences.</p>
    <p>AI: Probably Nothing To Worry About airs on Channel 4.</p>
    <ul><li><p>Sponsored event. <a href="/guardian-live-events/2026/talk">Book tickets here</a></p></li></ul>
    <ul><li>Real list item mentioning book tickets without a sales link.</li></ul>
    </div><div>Explore more on these topics</div></div>'''
    text = body(html)
    assert 'Original reporting' in text
    assert 'airs on Channel 4.' in text
    assert 'Real list item' in text
    assert 'Sponsored event' not in text
    assert 'Explore more' not in text


def test_indented_code_preserves_whitespace_between_prose_paragraphs():
    code = 'def report():\n    if ready:\n        print("done")\n\n    return True\n'
    html = '<article><p>Example:</p><pre><code>' + code + '</code></pre><p>Continue reading.</p></article>'
    assert body(html) == 'Example:\n\n' + code + '\n\nContinue reading.'
