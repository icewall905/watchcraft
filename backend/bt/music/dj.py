"""The talking part of the DJ and the curators. Every function has a no-model fallback, so the
music section works without an LLM; the model only adds taste, banter and description reading."""
from __future__ import annotations

import json
import logging
import random

from ..hub import ai

log = logging.getLogger(__name__)

# Curator personas. Each one builds mixes from a description in its own voice and taste.
CURATORS = {
    'disco': {'name': 'Disco Dee', 'emoji': '🪩', 'color': '#ff4fa3',
              'taste': 'dancefloor fillers, disco, house, pop bangers, big choruses'},
    'indie': {'name': 'Indie Ivo', 'emoji': '🎸', 'color': '#3fb6ff',
              'taste': 'indie, alternative, guitar music, cult favourites, rising acts'},
    'chill': {'name': 'Mellow Mo', 'emoji': '🌙', 'color': '#8f7cff',
              'taste': 'chill, soul, R&B, downtempo, late-night grooves'},
    'retro': {'name': 'Rewind Rae', 'emoji': '📼', 'color': '#ffb020',
              'taste': '70s, 80s, 90s and 2000s classics, singalongs, throwbacks'},
    'hype': {'name': 'MC Volt', 'emoji': '⚡', 'color': '#2bd67b',
             'taste': 'hip-hop, EDM, high energy, festival anthems, hype tracks'},
}
DEFAULT_CURATOR = 'disco'

FALLBACK_LINES = [
    'Keeping the floor warm with {artist}!',
    'Next up: {artist} — turn it up!',
    'Here comes {artist}. Hands in the air!',
    'Your DJ says: {artist}, right now.',
    'Fresh in the crates: {artist}!',
    'Ooh, this one. {artist}, everybody!',
]

PICKS_SCHEMA = {'type': 'object', 'properties': {
    'line': {'type': 'string'},
    'picks': {'type': 'array', 'items': {'type': 'object', 'properties': {
        'artist': {'type': 'string'}, 'title': {'type': 'string'}, 'why': {'type': 'string'}},
        'required': ['artist', 'title']}}},
    'required': ['line', 'picks']}

CURATE_SCHEMA = {'type': 'object', 'properties': {
    'name': {'type': 'string'}, 'emoji': {'type': 'string'}, 'blurb': {'type': 'string'},
    'queries': {'type': 'array', 'items': {'type': 'string'}},
    'picks': {'type': 'array', 'items': {'type': 'object', 'properties': {
        'artist': {'type': 'string'}, 'title': {'type': 'string'}}, 'required': ['artist', 'title']}}},
    'required': ['name', 'emoji', 'blurb', 'queries', 'picks']}


def _label(t: dict) -> str:
    return f'{t.get("artist") or "?"} – {t.get("title") or "?"}'


def _clean_picks(raw, limit: int) -> list[dict]:
    out = []
    for p in raw or []:
        if not isinstance(p, dict):
            continue
        artist, title = str(p.get('artist') or '').strip()[:80], str(p.get('title') or '').strip()[:120]
        if artist and title:
            out.append({'artist': artist, 'title': title, 'why': str(p.get('why') or '').strip()[:140]})
    return out[:limit]


def fallback_line(track: dict | None, request: str = '') -> str:
    if request:
        return f'Request received: "{request[:60]}" — let me dig through the crates!'
    if not track:
        return "Let's get this party started!"
    return random.choice(FALLBACK_LINES).format(artist=track.get('artist') or 'this one')


def pick_next(recent: list[dict], loved: list[str], vibe: str, request: str, n: int,
              avoid: list[str] | None = None) -> dict | None:
    """Ask the model for the next `n` songs. None when no model is configured or it fails."""
    if not ai.enabled():
        return None
    data = json.dumps({'just_played': [_label(t) for t in recent[-8:]], 'crowd_loves': loved[:15],
                       'crowd_dislikes': (avoid or [])[:25], 'vibe': vibe[:120], 'request': request[:200]}, ensure_ascii=False)
    try:
        out = ai._call(
            'You are a fun, upbeat party DJ playing official music videos from YouTube. Pick the next songs: '
            'real, well-known released songs that have an official music video. Flow from what just played, '
            'match the vibe, honour a guest request first when there is one, never repeat a song that just '
            'played, avoid the same artist twice in a row and mix familiar favourites with fresh discoveries. '
            'Never pick a song listed in `crowd_dislikes`, and avoid its artists unless the guest request asks for them. '
            '`line` is what you say on the mic before the next song: one short, warm, playful sentence '
            '(max 20 words, no hashtags). `why` is a few words per pick.',
            f'Pick {n} songs.\n<data>\n{ai._esc(data)}\n</data>', PICKS_SCHEMA, max_tokens=700, role='worker')
    except Exception as e:  # noqa: BLE001 - a model hiccup falls back to the radio
        log.info('music dj model failed: %s', e)
        return None
    picks = _clean_picks(out.get('picks'), n + 2)
    return {'line': str(out.get('line') or '').strip()[:200], 'picks': picks} if picks else None


def curate(description: str, curator: str) -> dict:
    """Turn a mix description into a name, search queries and concrete songs."""
    persona = CURATORS.get(curator) or CURATORS[DEFAULT_CURATOR]
    words = description.strip()
    base = {'name': (words[:40] or 'New mix').strip().capitalize(), 'emoji': persona['emoji'],
            'blurb': f'{persona["name"]} dug these out for you.', 'queries': [words[:120]], 'picks': [], 'ai': False}
    if not ai.enabled() or not words:
        return base
    try:
        out = ai._call(
            f'You are {persona["name"]}, a music curator known for {persona["taste"]}. Build a playlist of '
            'official music videos for the description. Give a catchy short name (max 4 words), one emoji, '
            'a one-sentence blurb in your voice, 3-6 YouTube Music search queries that find fitting songs '
            '(artist names, genres, eras), and 15-20 concrete real songs (artist + title) that fit.',
            f'<data>\n{ai._esc(words[:500])}\n</data>', CURATE_SCHEMA, max_tokens=1600, role='worker')
    except Exception as e:  # noqa: BLE001
        log.info('music curator model failed: %s', e)
        return base
    queries = [str(q).strip()[:120] for q in out.get('queries') or [] if str(q).strip()][:6]
    return {'name': str(out.get('name') or base['name']).strip()[:60],
            'emoji': (str(out.get('emoji') or '').strip() or base['emoji'])[:4],
            'blurb': str(out.get('blurb') or base['blurb']).strip()[:240],
            'queries': queries or base['queries'], 'picks': _clean_picks(out.get('picks'), 20), 'ai': True}
