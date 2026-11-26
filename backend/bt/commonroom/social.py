"""What makes the wall feel social: top comments on cards, the same link discussed elsewhere, liveliness
(rising/hot) and a varied page mix. Pure helpers; the feed API wires them up."""
import re
from urllib.parse import urlsplit

TOP_N = 2
TOP_CHARS = 400
GONE = {'', '[deleted]', '[removed]', 'deleted', 'removed'}
BOTS = re.compile(r'(^automoderator$|bot$|^bot[-_]|^botdefense)', re.I)


def link_key(url: str) -> str:
    """Same article/link across platforms: https, no tracking params, no www., no trailing slash."""
    if not url:
        return ''
    from ..hub.engine import canonical_url
    try:
        c = canonical_url(url)
    except ValueError:
        return ''
    p = urlsplit(c)
    host = (p.hostname or '').removeprefix('www.')
    return p._replace(netloc=host).geturl()[:1000]


# Sites that republish Reddit posts under /r/<sub>/<id> (no comments of their own).
REDDIT_MIRRORS = ('reddclips.com',)
_REDDIT_PATH = re.compile(r'^/r/([A-Za-z0-9_]{2,21})/(?:comments/)?([a-z0-9]{4,12})(?:/|$)')


def reddit_ref(url: str) -> tuple[str, str] | None:
    """(subreddit, post id) for a Reddit post URL or a known Reddit mirror; None otherwise."""
    p = urlsplit(url or '')
    host = (p.hostname or '').lower().removeprefix('www.')
    if host not in REDDIT_MIRRORS and host not in ('reddit.com', 'old.reddit.com', 'np.reddit.com'):
        return None
    if host.endswith('reddit.com') and '/comments/' not in p.path:
        return None
    m = _REDDIT_PATH.match(p.path)
    return (m.group(1), m.group(2)) if m else None


def _walk(tree: list, depth: int = 0):
    for c in tree or []:
        if isinstance(c, dict):
            yield c, depth
            yield from _walk(c.get('children') or [], depth + 1)


def top_comments(tree: list | None, op: str = '', n: int = TOP_N) -> list[dict]:
    """Best replies to show on a card: highest score among top-level comments, preferring readable lengths.
    Skips the original poster, removed comments and bots."""
    op = (op or '').lower()
    cands = []
    for c, depth in _walk(tree or []):
        if depth:
            continue
        body = ' '.join(str(c.get('body') or '').split())
        author = str(c.get('author') or '')
        if body.lower() in GONE or len(body) < 3 or (op and author.lower() == op) or BOTS.search(author):
            continue
        try:
            score = int(c.get('score') or 0)
        except (TypeError, ValueError):
            score = 0
        length = len(body)
        # Short quips and medium replies read best on a card; walls of text are for the post view.
        fit = 1.0 if 20 <= length <= 300 else 0.8 if length < 20 else 0.6
        cands.append(((max(score, 0) + 1) * fit, score, author, body))
    cands.sort(key=lambda t: t[0], reverse=True)
    return [{'author': a, 'score': sc, 'body': b[:TOP_CHARS] + ('…' if len(b) > TOP_CHARS else '')}
            for _, sc, a, b in cands[:n]]


# ---------- liveliness

HIST = 6               # score/comment samples kept per post
HIST_GAP_S = 300       # samples closer together than this replace the last one
WINDOW_S = 6 * 3600    # velocity looks this far back when samples allow
MIN_SPAN_S = 1200      # shorter sample spans fall back to the lifetime rate
FRESH_BADGE_S = 2 * 3600
RISING_Q, HOT_Q = 0.80, 0.95
MIN_PEERS = 20         # a platform needs this many recent posts before it can award badges
RISING_MIN_ENG = 5     # absolute floors: a quiet community cannot make a near-silent post look lively
HOT_MIN_ENG, HOT_MIN_COMMENTS = 15, 3


def sample(meta: dict | None, score: int, comments: int, now: float) -> dict:
    """meta with one more [ts, score, comments] sample (only when the numbers are real)."""
    meta = dict(meta or {})
    if not score and not comments:
        return meta
    hist = [h for h in (meta.get('hist') or []) if isinstance(h, list) and len(h) == 3]
    if hist and hist[-1][1:] == [score, comments] and now - hist[-1][0] <= WINDOW_S:
        return meta  # nothing moved: keep the older timestamp so growth is measured over the longer span
    if len(hist) > 1 and now - hist[-1][0] < HIST_GAP_S:  # never move the oldest baseline
        hist[-1] = [now, score, comments]
    else:
        hist.append([now, score, comments])
    meta['hist'] = hist[-HIST:]
    return meta


def velocity(meta: dict | None, score: int, comments: int, published_at: float, now: float) -> float:
    """Engagement per hour (score + 2 x comments): recent growth when sampled, else the lifetime rate."""
    eng = max(0, score) + 2 * max(0, comments)
    if not eng:
        return 0.0
    hist = [h for h in ((meta or {}).get('hist') or []) if isinstance(h, list) and len(h) == 3]
    recent = [h for h in hist if now - h[0] <= WINDOW_S]
    if recent and now - recent[0][0] >= MIN_SPAN_S:
        first = recent[0]
        grown = eng - (max(0, first[1]) + 2 * max(0, first[2]))
        return max(0.0, grown) / ((now - first[0]) / 3600)
    return eng / max(1.0, (now - published_at) / 3600)


def quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    v = sorted(values)
    return v[min(len(v) - 1, int(q * len(v)))]


def thresholds(rows) -> dict[str, tuple[float, float]]:
    """{platform: (rising, hot)} velocity cut-offs from recent posts, rows = (platform, velocity)."""
    per: dict[str, list[float]] = {}
    for platform, v in rows:
        if v > 0:
            per.setdefault(platform, []).append(v)
    return {p: (quantile(v, RISING_Q), quantile(v, HOT_Q)) for p, v in per.items() if len(v) >= MIN_PEERS}


def badges(platform: str, v: float, published_at: float, now: float, cuts: dict, score: int = 0,
           comments: int = 0) -> list[str]:
    """hot/rising against the platform's own cut-offs, but never for a downvoted or near-silent post."""
    out = []
    rising, hot = cuts.get(platform, (0.0, 0.0))
    eng = max(0, score) + 2 * max(0, comments)
    if score >= 0 and hot and v >= hot and eng >= HOT_MIN_ENG and comments >= HOT_MIN_COMMENTS:
        out.append('hot')
    elif score >= 0 and rising and v >= rising and eng >= RISING_MIN_ENG:
        out.append('rising')
    if now - published_at < FRESH_BADGE_S:
        out.append('fresh')
    return out


# ---------- page mix

KIND_RUN = 2           # at most this many of one kind in a row
COMMUNITY_CAP = 3      # per page, before later posts of that community are pushed back
WILD_GAP = 8           # wildcards at least this far apart


def mix(posts: list[dict]) -> list[dict]:
    """Reorder one page (never drops a post, so cursors stay valid): no long runs of one kind, a community
    cannot crowd the page, wildcards spread out. Greedy: the best-ranked post that breaks no rule goes next;
    when every remaining post breaks one, rules are relaxed from least to most important."""
    left = list(posts)
    out: list[dict] = []
    per_comm: dict[str, int] = {}
    last_wild = -WILD_GAP

    def ok(p: dict, level: int) -> bool:
        """level 0 checks every rule; each level up relaxes the least important remaining one."""
        k = p.get('kind') or ''
        if level < 3 and k and len(out) >= KIND_RUN and all((o.get('kind') or '') == k for o in out[-KIND_RUN:]):
            return False
        if level < 2 and per_comm.get(p.get('community') or '', 0) >= COMMUNITY_CAP:
            return False
        if level < 1 and p.get('wildcard') and len(out) - last_wild < WILD_GAP:
            return False
        return True
    while left:
        i = next((n for level in range(4) for n, p in enumerate(left) if ok(p, level)), 0)
        p = left.pop(i)
        if p.get('wildcard'):
            last_wild = len(out)
        per_comm[p.get('community') or ''] = per_comm.get(p.get('community') or '', 0) + 1
        out.append(p)
    return out


POP_PEERS = 20         # a community needs this many recent posts before its percentiles mean anything


def crowd(score: int, comments: int) -> float:
    """How much a post's own community engaged with it: votes plus replies (a reply weighs three votes)."""
    return max(0, score or 0) + 3 * max(0, comments or 0)


def percentile(value: float, peers: list[float]) -> float | None:
    """Share of peers this value beats (0..1); None for small or silent communities (feeds without numbers)."""
    if len(peers) < POP_PEERS or not any(peers):
        return None
    return sum(1 for p in peers if p < value) / len(peers)


def popularity_text(pct: float | None, community: str) -> str:
    """Judge-facing line for clearly popular or ignored posts; '' in the unremarkable middle."""
    if pct is None:
        return ''
    where = community or 'its community'
    if pct >= 0.95:
        return f'top 5% of {where} lately'
    if pct >= 0.8:
        return f'top 20% of {where} lately'
    if pct <= 0.2:
        return f'bottom 20% of {where} lately: few people cared'
    return ''
