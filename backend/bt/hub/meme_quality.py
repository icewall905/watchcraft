"""Conservative eligibility for the humor feed, separate from short Video Moments.

A provider or a word in a caption cannot establish that a picture is funny. We
require a humor-scoped community/tag plus displayable media; this is provenance,
not an estimate of how funny an item is.
"""
import re
from collections.abc import Mapping

_HUMOR_WORD = r'(?:memes?|humou?r|funny|jokes?|shitpost(?:ing|s)?|comics?)'
_SCOPE_PATTERN = r'(?i)^(?:r/|[!#@])?(?:[a-z0-9_-]*' + _HUMOR_WORD + r'|me_?irl|2meirl4meirl)(?:@[a-z0-9.-]+)?$'
_HUMOR = re.compile(_SCOPE_PATTERN)
_HASHTAG_PATTERN = r'(?i)(?<![\w#])#[a-z0-9_]*' + _HUMOR_WORD + r'\b'
_HASHTAG = re.compile(_HASHTAG_PATTERN)
_PROMOTION_PATTERN = r'(?i)\b(?:buy now|shop now|discount code|promo code|link in (?:my |our )?bio|subscribe to (?:my |our |the )?channel)\b'
_PROMOTION = re.compile(_PROMOTION_PATTERN)
_EVENT_URL_PATTERN = r'(?i)https?://[^\s<>]+/(?:events?|tickets?)(?:[/#?]|\b)'
_EVENT_CTA_PATTERN = r'(?i)(?:weitere informationen\s*:|\b(?:book now|buy tickets|tickets (?:available|on sale)|reserve (?:your |a )?seat)\b)'
_EVENT_URL = re.compile(_EVENT_URL_PATTERN)
_EVENT_CTA = re.compile(_EVENT_CTA_PATTERN)


# Generic words learned from hiding a few bad posts cannot mean "avoid all memes".
# These are read-time guards; the user's recorded feedback is left intact.
GENERIC_MEME_TERMS = frozenset(('meme memes humor humour funny joke jokes https http www '
    'the that this they them their there with what when your have from about just been '
    'would could should those these were will more some').split())


def _get(item, key, default=None):
    return item.get(key, default) if isinstance(item, Mapping) else getattr(item, key, default)


def humor_scope(value: str) -> bool:
    """Recognize scoped names such as !programmerhumor@host or #linuxmemes.

    Ignore instance/account domains: funny.example is not a humor community.
    'lol' is deliberately excluded (it appears inside unrelated words).
    """
    return bool(_HUMOR.fullmatch(str(value or '').strip()))


def meme_community(item) -> bool:
    meta = _get(item, 'meta', {}) or {}
    return (humor_scope(meta.get('community', ''))
            or any(humor_scope(t) for t in meta.get('tags', []) if isinstance(t, str))
            or bool(_HASHTAG.search(_caption(item))))


def _caption(item) -> str:
    return ' '.join(str(_get(item, k, '') or '') for k in ('title', 'summary', 'body'))


def is_meme_candidate(item, *, has_media: bool = False) -> bool:
    """Whether an existing item has enough evidence for a visual humor feed."""
    if _get(item, 'type') != 'meme':
        return False
    meta = _get(item, 'meta', {}) or {}
    media = meta.get('media') or {}
    visual = has_media or bool(_get(item, 'image', '')) or (media.get('url') and media.get('type') in ('image', 'video', 'gifv', 'gif'))
    if meta.get('sensitive') or not visual:
        return False
    if not meme_community(item):
        return False
    caption = _caption(item)
    return not (_PROMOTION.search(caption) or (_EVENT_URL.search(caption) and _EVENT_CTA.search(caption)))


def humor_curator(curator) -> bool:
    """Identify humor intent without treating every short-video collector as humor."""
    if 'meme' not in (_get(curator, 'content_types', []) or []):
        return False
    parts = [_get(curator, 'name', ''), *(_get(curator, 'queries', []) or [])]
    return bool(re.search(r'\b(?:memes?|humou?r|funny|jokes?|shitposts?|comics?)\b', ' '.join(map(str, parts)), re.I))


def candidate_clause(model, *, has_media=None):
    """SQLite equivalent of is_meme_candidate for accurate filtering before LIMIT.

    Pass MemeExample.has_media for captured legacy items whose metadata predates
    media extraction. REGEXP uses SQLAlchemy's standard SQLite implementation.
    """
    from sqlalchemy import and_, exists, func, literal, not_, or_, select

    def regex(pattern, value):
        return func.coalesce(value, '').bool_op('REGEXP')(pattern)

    caption = func.coalesce(model.title, '') + literal(' ') + func.coalesce(model.summary, '') + literal(' ') + func.coalesce(model.body, '')
    tags = func.json_each(model.meta, '$.tags').table_valued('value', 'type').alias('meme_tags')
    scope = or_(regex(_SCOPE_PATTERN, func.trim(func.json_extract(model.meta, '$.community'))),
                exists(select(1).select_from(tags).where(tags.c.type == 'text', regex(_SCOPE_PATTERN, func.trim(tags.c.value)))),
                regex(_HASHTAG_PATTERN, caption))
    visual = or_(func.coalesce(model.image, '') != '',
                 and_(func.coalesce(func.json_extract(model.meta, '$.media.url'), '') != '',
                      func.json_extract(model.meta, '$.media.type').in_(['image', 'video', 'gifv', 'gif'])))
    if has_media is not None:
        visual = or_(visual, has_media)
    return and_(model.type == 'meme', visual,
                func.coalesce(func.json_extract(model.meta, '$.sensitive'), 0).in_([0, '']), scope,
                not_(regex(_PROMOTION_PATTERN, caption)),
                not_(and_(regex(_EVENT_URL_PATTERN, caption), regex(_EVENT_CTA_PATTERN, caption))))
