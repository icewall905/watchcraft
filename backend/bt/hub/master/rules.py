"""Deterministic gates and quality signals. No model calls: these run on every new item."""
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from sqlmodel import Session, select

from ...models import Profile
from ..models import ContentItem, MemeExample, MemeTrend, MemeTrendState, Topic

DAY = 86400.0
# Past this age an item is set aside rather than shown: old news is not news.
STALE_AFTER = {'news': 3 * DAY, 'meme': 14 * DAY, 'article': 30 * DAY, 'web': 30 * DAY}
CLICKBAIT = re.compile(r"you won'?t believe|will shock you|what happens next|must see|"
                       r"this one trick|\bjaw[- ]dropping\b|breaks the internet", re.I)
# Structured sources (releases, papers, advisories, people, sites) carry few of the signals
# below, so they start higher instead of waiting for a summary they will never have.
STRUCTURED = {'project', 'document', 'advisory', 'person', 'organization', 'domain', 'recipe', 'pattern'}
BASE = 50.0
# A site's front page or section index is a list of stories, not a story.
HOMEPAGE_TITLE = re.compile(r"^latest\b|\blatest (\w+ )?news\b|\bbreaking (news|headlines)\b|"
                            r"\btop (news|stories|headlines)\b|\bnews (and|&) [a-z ]*guides\b|"
                            r"\bheadlines and video\b|\bnews (&|and) archive\b", re.I)
HASHTAG = re.compile(r'(?:^|\s)#\s?[^\W\d]')


@dataclass
class ProfileRules:
    """What one profile has asked never to see, loaded once per pass."""
    child: bool = False
    hidden_languages: set[str] = field(default_factory=set)
    muted_terms: list[str] = field(default_factory=list)  # whole words or phrases
    hidden_formats: set[int] = field(default_factory=set)
    hidden_communities: set[str] = field(default_factory=set)


@dataclass
class RuleResult:
    reject: bool = False      # never shown, not even under "set aside"
    set_aside: bool = False   # shown only when the reader asks for what was left out
    quality: float = BASE
    reasons: list[str] = field(default_factory=list)


def load(s: Session, pid: int) -> ProfileRules:
    p = s.get(Profile, pid)
    prefs = (p.prefs or {}) if p else {}
    topics = s.exec(select(Topic).where(Topic.profile_id == pid, Topic.muted == True)).all()  # noqa: E712
    hidden_trends = select(MemeTrendState.trend_id).where(MemeTrendState.profile_id == pid, MemeTrendState.hidden == True)  # noqa: E712
    hidden = s.exec(select(MemeTrend.target_kind, MemeTrend.target_key).where(MemeTrend.id.in_(hidden_trends))).all()
    return ProfileRules(
        child=bool(p and p.role == 'child'),
        hidden_languages={x for x in (prefs.get('hidden_meme_languages') or []) if isinstance(x, str)},
        muted_terms=[k.lower() for t in topics for k in (t.keywords or [t.name]) if isinstance(k, str) and k.strip()],
        hidden_formats={int(k) for kind, k in hidden if kind == 'format' and k.isdigit()},
        hidden_communities={k for kind, k in hidden if kind == 'community' and k})


def _num(v) -> float:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


def engagement(meta: dict) -> float | None:
    """Reactions the source reported, or None when it reports none. Social counts are a sample
    of one instance, so they only ever nudge quality; they never decide alone."""
    obs = meta.get('observations') if isinstance(meta.get('observations'), dict) else {}
    if 'favourites' in obs or 'reblogs' in obs:
        return _num(obs.get('favourites')) + _num(obs.get('reblogs'))
    if 'score' in obs or 'upvotes' in obs:
        return _num(obs['score'] if 'score' in obs else obs.get('upvotes'))  # score already nets the votes
    # Lifetime counts such as GitHub stars say nothing about today, so they are not read.
    for k in ('points', 'reactions', 'score'):
        if k in meta and meta[k] is not None:
            return _num(meta[k])
    return None


def _homepage(item: ContentItem) -> bool:
    """A web result that is a site's front page or section index. News and articles are
    stories by construction, so only web results are judged this way."""
    if item.type != 'web':
        return False
    u = urlsplit(item.url or '')
    path = u.path.strip('/')
    if u.netloc and not path and not u.query and not u.fragment:
        return True
    # A front-page title only counts on a section address (/world, /category/denmark-news):
    # an article lives at a dated or long slug even when its headline starts "Latest".
    parts = path.split('/')
    section = len(parts) <= 2 and not any(ch.isdigit() for ch in path) and all(p.count('-') < 2 for p in parts)
    return section and bool(HOMEPAGE_TITLE.search(item.title or ''))


def _placeholder_title(item: ContentItem) -> bool:
    t = (item.title or '').strip()
    return not t or t == item.url or t.endswith(' post') and ' ' not in t[:-5]


def check(item: ContentItem, ex: MemeExample | None, rules: ProfileRules, now: float | None = None) -> RuleResult:
    now = now or time.time()
    r = RuleResult()
    meta = item.meta or {}
    hay = f'{item.title or ""} {item.summary or ""}'.lower()

    # Hard gates: the profile said no, or it is a copy of something already kept.
    muted = next((m for m in rules.muted_terms if re.search(rf'(?<!\w){re.escape(m)}(?!\w)', hay)), None)
    if muted:
        return RuleResult(reject=True, quality=0, reasons=[f'muted topic "{muted}"'])
    language = (ex.language if ex else '') or str(meta.get('language') or '')
    if language and language in rules.hidden_languages:
        return RuleResult(reject=True, quality=0, reasons=[f'hidden language {language}'])
    if meta.get('sensitive') and rules.child:
        return RuleResult(reject=True, quality=0, reasons=['sensitive'])
    if ex and ex.dup_of:
        return RuleResult(reject=True, quality=0, reasons=['duplicate'])
    if ex and (ex.format_id in rules.hidden_formats or ex.community and ex.community in rules.hidden_communities):
        return RuleResult(reject=True, quality=0, reasons=['hidden meme trend'])

    def adjust(points: float, reason: str):
        r.quality += points
        r.reasons.append(reason)

    when = item.published_at or item.first_seen
    limit = STALE_AFTER.get(item.type)
    if limit and when and now - when > limit:
        r.set_aside = True
        r.reasons.append('stale')

    if _placeholder_title(item):
        adjust(-20, 'no real title')
    if _homepage(item):
        r.set_aside = True
        adjust(-20, 'a front page, not a story')
    if CLICKBAIT.search(item.title or ''):
        adjust(-15, 'clickbait headline')
    letters = [c for c in item.title or '' if c.isalpha()]
    if len(letters) >= 12 and sum(c.isupper() for c in letters) / len(letters) > 0.7:
        adjust(-10, 'shouting headline')

    if item.type in STRUCTURED:
        adjust(10, 'structured source')
    if item.type == 'meme':
        media = meta.get('media') if isinstance(meta.get('media'), dict) else {}
        if not (media or item.image):
            adjust(-30, 'no image')
        elif media.get('alt'):
            adjust(5, 'described image')
        if len(item.summary or '') > 400:
            adjust(-10, 'wall of text')
        if len(meta.get('tags') or []) > 6 or len(HASHTAG.findall(item.title or '')) >= 4:
            adjust(-30, 'hashtag spam')
        if meta.get('sensitive'):
            adjust(-10, 'sensitive')
    elif item.type in ('news', 'article', 'web'):
        if len((item.title or '').split()) < 3:
            adjust(-10, 'thin headline')
        if len(item.summary or '') >= 80:
            adjust(10, 'has a summary')
        elif not item.summary:
            adjust(-10, 'no summary')
        if item.type == 'article' and len(item.body or '') >= 3000:
            adjust(10, 'long read')
        if item.image:
            adjust(5, 'has an image')

    pop = engagement(meta)
    if pop is not None:
        if pop >= 100:
            adjust(20, 'widely liked')
        elif pop >= 10:
            adjust(10, 'liked')
        elif pop == 0 and item.type == 'meme':
            adjust(-15, 'no reactions')

    r.quality = max(0.0, min(100.0, r.quality))
    return r
