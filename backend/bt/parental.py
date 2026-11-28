"""Parental controls: per child profile switches for each service and function.

Grown-ups (an account administrator, or a grown-up profile of the child's own account) switch each feature on
or off per child. Only child profiles are affected. Unset switches use the default, which is locked down: a
child gets The Screen (watching their playlists) until a grown-up turns more on. The controls only take away:
anything already closed to children (The Jukebox, grown-up-only Hub routes, Jellyfin without "allow children")
stays closed.

The server enforces them for every /api request (`gate`, an app-wide dependency); the web app hides what is off.
"""
import re

from fastapi import HTTPException, Request

from .db import session
from .models import Profile

# (key, label, group, hint). Order is the order of the settings screen.
FEATURES: list[tuple[str, str, str, str]] = [
    ('screen', 'The Screen', 'Watch', 'Watch their own playlists and the TV home.'),
    ('scroll', 'The Scroll', 'Watch', 'Short videos.'),
    ('subscriptions', 'Subscriptions', 'Watch', 'See videos from subscribed channels.'),
    ('marquee', 'The Marquee', 'Watch', 'Jellyfin films and series (also needs "allow children" in Settings → Jellyfin).'),
    ('yt_search', 'YouTube live search', 'YouTube actions', 'Search all of YouTube.'),
    ('playlists_edit', 'Create and edit playlists', 'YouTube actions', 'Make, change, reorder and delete playlists.'),
    ('subscribe', 'Subscribe and block channels', 'YouTube actions', 'Change channel subscriptions.'),
    ('yt_import', 'Import from YouTube', 'YouTube actions', 'Import subscriptions or a feed from a YouTube account.'),
    ('front_page', 'The Front Page', 'Explore', 'Today\'s kid-safe edition.'),
    ('finds', 'The Finds', 'Explore', 'Kid-safe items their curators found.'),
    ('wire', 'The Wire', 'Explore', 'News.'),
    ('boards', 'The Boards', 'Explore', 'Mood Boards.'),
    ('commons', 'The Commons', 'Explore', 'Common Room posts and memes.'),
    ('stacks', 'The Stacks', 'Explore', 'Saved items and collections.'),
    ('almanac', 'The Almanac', 'Explore', 'Topics.'),
    ('dispatch', 'The Dispatch', 'Explore', 'Briefings.'),
    ('alerts', 'Alerts', 'Explore', 'Curator alerts.'),
    ('study', 'The Study', 'Research', 'Research with curators.'),
    ('scout', 'The Scout', 'Research', 'Scout reports.'),
    ('curators', 'Curators', 'Workshop', 'See their curators and agent activity.'),
    ('curators_edit', 'Create and change curators', 'Hub actions', 'Make, change and run curators and sources.'),
    ('board_search', 'Mood Board image search', 'Hub actions', 'Search for and add pictures (all pictures are still kid-checked).'),
    ('share', 'Share with others', 'Hub actions', 'Share items and boards with other profiles or by link.'),
]
KEYS = [k for k, *_ in FEATURES]
DEFAULTS = {k: k == 'screen' for k in KEYS}

ITEMS = ('finds', 'stacks', 'front_page', 'wire', 'alerts', 'almanac', 'dispatch', 'study', 'scout', 'boards')
CURATOR_VIEWS = ('curators', 'curators_edit', 'study', 'scout', 'dispatch', 'alerts')
ANY = None
WRITE = ('POST', 'PUT', 'PATCH', 'DELETE')

# (path regex below /api, methods or ANY, features: the request is allowed when any one is on). First match wins;
# a path that matches nothing is not affected (video playback, profiles, settings, history...).
RULES: list[tuple[re.Pattern, tuple | None, tuple[str, ...]]] = [(re.compile(p), m, f) for p, m, f in [
    (r'^/search$', ANY, ('yt_search',)),
    (r'^/categories/(autofill|autofill-brief|reorder)$', ANY, ('playlists_edit',)),
    (r'^/categories(/\d+)?$', WRITE, ('playlists_edit',)),
    (r'^/candidates', WRITE, ('playlists_edit',)),
    (r'^/channels/[^/]+(/block)?$', WRITE, ('subscribe',)),
    (r'^/channels$', ANY, ('subscriptions', 'subscribe')),
    (r'^/ytimport/', ANY, ('yt_import',)),
    # A channel page lists any channel's videos: live search, or (checked in the route) a subscribed channel.
    (r'^/channel/(?!.*/avatar$)', ANY, ('yt_search', 'subscriptions', 'subscribe')),
    (r'^/downloads', WRITE, ('playlists_edit',)),  # save offline / remove: a server-side download of any video
    (r'^/agent/(events|candidates|memory)', ANY, ('curators',)),  # /agent/stream is scoped per child instead
    (r'^/feed/', ANY, ('screen', 'subscriptions')),
    (r'^/shorts', ANY, ('scroll',)),
    (r'^/jellyfin(/|$)', ANY, ('marquee',)),
    (r'^/hub/today', ANY, ('front_page',)),
    (r'^/hub/news', ANY, ('wire',)),
    (r'^/hub/boards/(search|search/status)$', ANY, ('board_search',)),
    (r'^/hub/boards/[^/]+/pinterest', ANY, ('board_search',)),
    (r'^/hub/boards/[^/]+/(images|reference)$', ('POST',), ('board_search',)),
    (r'^/hub/boards(/|$)', ANY, ('boards',)),
    (r'^/hub/mood-share/(boards/[^/]+/(to-profile|members|links)|links)', WRITE, ('share',)),
    (r'^/hub/mood-share/', ANY, ('boards',)),
    (r'^/hub/items/[^/]+/share$', WRITE, ('share',)),
    (r'^/hub/(commonroom|memes)(/|$)', ANY, ('commons',)),
    (r'^/hub/collections', ANY, ('stacks',)),
    (r'^/hub/topics', ANY, ('almanac', 'wire')),
    (r'^/curator/', ANY, ('almanac',)),
    (r'^/hub/briefings', ANY, ('dispatch',)),
    (r'^/hub/alerts', ANY, ('alerts',)),
    (r'^/hub/scout', ANY, ('scout',)),
    (r'^/hub/(curators|sources|curator)(/|$)', WRITE, ('curators_edit',)),
    (r'^/hub/(curators|sources|registry|curator)(/|$)', ANY, CURATOR_VIEWS),
    (r'^/hub/team', ANY, ('curators',)),
    (r'^/hub/(items|stories|search|saved-searches|shelf)', ANY, ITEMS),
]]


def effective(p: Profile | None) -> dict[str, bool]:
    """Every switch for this profile; grown-ups have everything on."""
    if not p or p.role != 'child':
        return {k: True for k in KEYS}
    saved = p.parental if isinstance(p.parental, dict) else {}
    return {k: bool(saved[k]) if isinstance(saved.get(k), bool) else DEFAULTS[k] for k in KEYS}


def allowed(p: Profile | None, key: str) -> bool:
    return effective(p)[key]


def clean(data) -> dict[str, bool]:
    if not isinstance(data, dict):
        raise HTTPException(400, 'Send the switches as an object.')
    unknown = set(data) - set(KEYS)
    if unknown:
        raise HTTPException(400, 'Unknown switch: ' + ', '.join(sorted(unknown)))
    if not all(isinstance(v, bool) for v in data.values()):
        raise HTTPException(400, 'Switches are true or false.')
    return dict(data)


def needed(method: str, path: str) -> tuple[str, ...] | None:
    """Features that open this /api request (any one), or None when parental controls do not cover it."""
    for rx, methods, features in RULES:
        if rx.search(path) and (methods is ANY or method in methods):
            return features
    return None


def catalog() -> list[dict]:
    return [{'key': k, 'label': label, 'group': group, 'hint': hint, 'default': DEFAULTS[k]}
            for k, label, group, hint in FEATURES]


def gate(request: Request) -> None:
    """App-wide dependency: a child's request to a switched-off feature gets 403."""
    path = request.url.path
    if not path.startswith('/api/'):
        return
    features = needed(request.method, path[4:])
    if not features:
        return
    from .access import profile_id
    from .jellyfin import cast
    try:
        raw = request.headers.get('x-profile-id')
        pid = profile_id(request, int(raw) if raw and raw.isdigit() else None)
    except HTTPException:
        return  # no usable profile: the route's own checks answer
    if path.startswith('/api/jellyfin/'):
        pid = cast.grant_pid(request.headers.get(cast.HEADER)) or pid  # a screen playing what a sender cast
    with session() as s:
        p = s.get(Profile, pid)
        if p and p.role == 'child' and not any(allowed(p, f) for f in features):
            raise HTTPException(403, 'A grown-up has switched this off for this profile.')
