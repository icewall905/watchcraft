"""Find sources for a Meme Tracker: the AI proposes meme communities, only ones that return memes are kept.

The Common Room equivalent is commonroom/discover.py. Here the caller adopts the validated candidates
onto the curator, so a run picks them up at once.
"""
import logging

from . import ai

log = logging.getLogger(__name__)
MAX_CANDIDATES = 12   # validated one by one; each is a network read
MAX_ADOPT = 6
SCHEMA = {'type': 'object', 'properties': {'sources': {'type': 'array', 'items': {'type': 'object', 'properties': {
    'platform': {'type': 'string'}, 'locator': {'type': 'string'}, 'name': {'type': 'string'}},
    'required': ['platform', 'locator']}}}, 'required': ['sources']}


def platforms() -> dict[str, str]:
    """Platform key -> locator hint. Reddit only when its API keys are set: anonymous reads are blocked."""
    from ..commonroom import reddit_oauth
    out = {'lemmy': 'https://lemmy.world/c/memes or !memes@lemmy.world',
           'mastodon': 'https://mastodon.social/tags/memes (a hashtag on a large public instance)'}
    if reddit_oauth.configured():
        out['reddit_memes'] = 'r/ProgrammerHumor'
    return out


def propose(c, have: set[str]) -> list[tuple[str, str, str]]:
    """(provider, locator, name) suggestions from the AI, most valuable first."""
    if not ai.enabled():
        raise ValueError('Turn on AI in Settings to find sources.')
    listing = '\n'.join(f'- {k}: locator like {v!r}' for k, v in platforms().items())
    system = ('You find the best public meme communities for a person\'s taste. Suggest up to 15 real, active '
              'communities that exist today and post image memes, most valuable first. Use exactly the locator '
              'format shown for each platform. Prefer focused, high-quality communities; skip NSFW ones and any '
              'already in use.')
    user = (f'Platforms:\n{listing}\n\n<data>\n' + ai._esc(
        f'Curator: {c.name}\nInstructions: {c.instructions}\nQueries: {", ".join(c.queries or [])}\n'
        f'Keywords: {", ".join(c.keywords or [])}\nLiked: {", ".join(c.tuned_up or [])}\n'
        f'Disliked: {", ".join(c.tuned_down or [])}\nAlready in use: {", ".join(sorted(have)) or "none"}')
        + '\n</data>')
    res = ai._call(system, user, SCHEMA, max_tokens=1200, role='meme_sources')
    allowed = platforms()
    out = []
    for row in res.get('sources', []) if isinstance(res, dict) else []:
        if isinstance(row, dict) and row.get('platform') in allowed and str(row.get('locator') or '').strip():
            out.append((str(row['platform']), str(row['locator']).strip()[:300], str(row.get('name') or '')[:100]))
    return out


def validate(provider: str, url: str) -> int:
    """How many memes the source returns now; raises ValueError when it is dead or not a meme source."""
    from .lemmy import lemmy
    from .providers import mastodon, reddit_memes
    if provider == 'lemmy':
        got = lemmy(url, '', {'item_type': 'meme'})
    elif provider == 'mastodon':
        got = [i for i in mastodon(url, '') if i.get('image')]
    elif provider == 'reddit_memes':
        got = reddit_memes(url)
    else:
        raise ValueError('Unsupported platform')
    if not got:
        raise ValueError('No image memes there right now.')
    return len(got)
