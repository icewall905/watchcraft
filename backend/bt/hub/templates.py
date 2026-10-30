"""Starter curator templates. Each creates an ordinary, fully editable curator."""

def T(key, name, description, icon, queries, keywords, providers, types, hours=24, kind='watch'):
    return {'key': key, 'name': name, 'description': description, 'icon': icon, 'kind': kind, 'queries': queries,
            'keywords': keywords, 'provider_keys': providers, 'content_types': types, 'interval_hours': hours}


TEMPLATES = [
    T('youtube', 'YouTube channel watcher', 'New uploads from channels you add as RSS feeds (youtube.com/feeds/videos.xml?channel_id=…).',
      '▶', [], [], ['rss'], ['video'], 6),
    T('news', 'General news watcher', 'Recent stories about a subject from the web and Hacker News.', '📰',
      ['latest news'], [], ['web_search', 'hackernews'], ['news', 'article'], 6),
    T('ai-research', 'AI research watcher', 'New papers and open-source models.', '🧠',
      ['large language model', 'open weights model release'], ['llm', 'model', 'transformer', 'inference'],
      ['arxiv', 'github_search', 'hackernews'], ['document', 'project', 'news']),
    T('github-releases', 'GitHub release watcher', 'Releases of the repositories you depend on (add each repo as a source).', '📦',
      [], [], ['github_releases'], ['project'], 6),
    T('security', 'CVE / security monitor', 'Authoritative CVEs and known-exploited vulnerabilities for products you run.', '🛡',
      ['home assistant', 'proxmox'], ['home assistant', 'proxmox'], ['nvd', 'cisa_kev'], ['advisory'], 12),
    T('knitting', 'Knitting pattern finder', 'Beginner-friendly knitting patterns and tutorials.', '🧶',
      ['beginner knitting pattern free', 'easy sweater knitting pattern', 'scarf knitting pattern beginner'],
      ['knit', 'pattern', 'beginner'], ['web_search'], ['pattern', 'article', 'video']),
    T('recipes', 'Recipe explorer', 'New recipes matching your tastes.', '🍳',
      ['easy weeknight dinner recipe', 'vegetarian recipe'], ['recipe'], ['web_search'], ['recipe']),
    T('science', 'Scientific paper watcher', 'Fresh preprints in a field.', '🔬',
      ['quantum computing'], [], ['arxiv'], ['document'], 24),
    T('home-assistant', 'Home Assistant updates', 'Releases, news and security for Home Assistant.', '🏠',
      ['home assistant'], ['home assistant', 'hass'], ['github_search', 'hackernews', 'nvd'], ['project', 'news', 'advisory'], 12),
    T('product-research', 'Technology / product research', 'Reviews, comparisons and launches for a product category.', '🛒',
      ['best 24gb vram gpu for local llm'], [], ['web_search', 'hackernews'], ['article', 'news', 'web']),
    T('osint', 'OSINT research brief', 'One-off deep research on a business, person, domain or topic from public sources.', '🕵',
      [], [], ['web_search', 'wikipedia', 'hackernews', 'github_search'], [], 0, 'research'),
]
