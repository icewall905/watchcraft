"""Meme Radar: hash identity, exact/perceptual dedup, template grouping and counted trends.

Examples are canonical ContentItems of type 'meme'. This module adds only derived
identity (media sha, dHash, caption sha), template grouping and observations that are
counted from stored timestamps — never estimated, never invented. A trend label is a
function of the counts, and sparse samples keep the label 'newly_observed'.
"""
import hashlib
import io
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from sqlalchemy import func
from sqlmodel import Session, select

from PIL import Image

from ..access import hub_allowed
from ..db import get_kv, save_setting, session
from .extract import drop_links
from .models import (CapturedAsset, ContentItem, Curator, CuratorDiscovery, CuratorSource, ItemState, MemeExample,
                     MemeFormat, MemeTrend, MemeTrendState, Source)

PHASH_FORMAT_DIST = 10   # Hamming distance under which two images count as the same template
PHASH_SIMILAR_DIST = 12  # distance used when answering "find similar"
WINDOW_DAYS = 30

# Public instances that serve anonymous hashtag timelines, probed in order. The first one
# that returns posts for the curator's own queries wins; nothing is invented.
DEFAULT_INSTANCES = ('mastodon.social', 'mastodon.world', 'fosstodon.org')
AUTOPILOT_EVERY = 6 * 3600  # one automatic source change per curator per window
PROBE_QUERIES = 2           # queries tried per instance before moving on

STATES = ('newly_observed', 'emerging', 'trending', 'established', 'fading')


# ---------- hashes

def dhash(img: Image.Image, size: int = 8) -> str:
    g = img.convert('L').resize((size + 1, size), Image.LANCZOS)
    px = list(g.getdata())
    bits = 0
    for row in range(size):
        for col in range(size):
            a, b = px[row * (size + 1) + col], px[row * (size + 1) + col + 1]
            bits = (bits << 1) | (1 if a > b else 0)
    return f'{bits:016x}'


def hamming(a: str, b: str) -> int:
    try:
        return bin(int(a, 16) ^ int(b, 16)).count('1')
    except ValueError:
        return 64


def norm_caption(text: str) -> str:
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]', ' ', (text or '').lower())).strip()


def caption_sha(item: ContentItem) -> str:
    return hashlib.sha256(norm_caption(f'{item.title} {item.summary}').encode()).hexdigest()[:32]


def media_sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:32]


# ---------- analysis

def _refresh_format(s: Session, f: MemeFormat) -> None:
    members = s.exec(select(MemeExample).where(MemeExample.format_id == f.id)).all()
    items = s.exec(select(ContentItem).where(ContentItem.id.in_([m.item_id for m in members]))).all() if members else []
    f.example_count = len([m for m in members if not m.dup_of])
    f.community_count = len({m.community for m in members if m.community})
    seen = [i.first_seen for i in items if i.first_seen]
    if seen:
        f.first_seen, f.last_seen = min(seen), max(seen)
    f.updated_at = time.time()
    s.add(f)


def _create_format(s: Session, kind: str, exemplar: ContentItem, phash: str) -> MemeFormat:
    f = MemeFormat(kind=kind, name=(exemplar.title or '')[:120], phash=phash, exemplar_item_id=exemplar.id,
                   first_seen=exemplar.first_seen, last_seen=exemplar.first_seen)
    s.add(f)
    s.flush()
    return f


# Function words that belong to one language only. Shared words are left out on purpose:
# a word two languages use cannot tell them apart, and guessing wrong would hide the wrong
# memes. Words English captions use too (van, op, plus, nada, as) are left out for the same
# reason. This is a caption-sized heuristic, not a language model.
_LANGUAGE_WORDS = {
    'en': {'the', 'and', 'you', 'that', 'this', 'with', 'have', 'not', 'for', 'but', 'what', 'when', 'just',
           'like', 'they', 'from', 'your', 'about', 'would', 'there', 'their', 'been', 'because', 'really',
           'we'},
    'de': {'der', 'das', 'und', 'nicht', 'ist', 'ich', 'wir', 'mit', 'auf', 'ein', 'eine', 'zu', 'dem', 'auch',
           'aber', 'wie', 'sind', 'wenn', 'oder', 'sehr', 'noch', 'nur', 'mein', 'dein', 'jetzt', 'immer', 'kein',
           'über', 'für', 'du'},
    'es': {'el', 'los', 'las', 'un', 'una', 'por', 'con', 'para', 'pero', 'como', 'más', 'mi', 'está', 'esto',
           'cuando', 'muy', 'porque', 'todo', 'aquí', 'también', 'siempre'},
    'fr': {'le', 'les', 'qui', 'pas', 'une', 'pour', 'avec', 'dans', 'sur', 'est', 'et', 'je', 'nous',
           'vous', 'mais', 'tout', 'comme', 'sont', 'cette', 'être', 'toujours'},
    'nl': {'het', 'een', 'dat', 'niet', 'ik', 'je', 'we', 'met', 'voor', 'maar', 'ook', 'wat',
           'zijn', 'hij', 'zij', 'dit', 'nog', 'wel', 'geen', 'naar', 'altijd'},
    'pt': {'os', 'em', 'um', 'uma', 'por', 'com', 'para', 'mas', 'como', 'mais', 'meu', 'não', 'você',
           'isso', 'quando', 'muito', 'também', 'aqui', 'sempre'},
    'it': {'il', 'lo', 'gli', 'che', 'non', 'una', 'per', 'con', 'su', 'è', 'sono', 'mio', 'tuo', 'come',
           'questo', 'quando', 'molto', 'anche', 'qui', 'perché', 'sempre'},
    'da': {'og', 'ikke', 'jeg', 'hvad', 'meget', 'også', 'fordi', 'altid', 'sammen', 'du'},
    'sv': {'och', 'inte', 'är', 'jag', 'vad', 'mycket', 'också', 'därför', 'alltid', 'tillsammans', 'du'},
    'no': {'og', 'ikke', 'jeg', 'hva', 'veldig', 'også', 'fordi', 'alltid', 'sammen', 'du'},
}


def detect_language(text: str) -> str:
    """Best-effort language of a short caption, for memes whose source did not say.

    A language wins only with a clear lead in words that belong to it alone, backed by
    at least two distinct words of that language (shared ones count as support): one
    word such as "das" next to an English one is not enough, and an unknown language
    hides nothing by mistake.
    """
    words = set(re.findall(r"[a-zà-ÿ']+", (text or '').lower()))
    if len(words) < 2:
        return ''
    unique: dict[str, int] = {}
    support: dict[str, int] = {}
    for word in words:
        owners = [lang for lang, common in _LANGUAGE_WORDS.items() if word in common]
        for lang in owners:
            support[lang] = support.get(lang, 0) + 1
        if len(owners) == 1:
            unique[owners[0]] = unique.get(owners[0], 0) + 1
    if not unique:
        return ''
    best = max(unique.values())
    winners = [lang for lang, n in unique.items() if n == best]
    if len(winners) != 1 or support[winners[0]] < 2:
        return ''
    return winners[0]


# Bump when detect_language changes, so earlier guesses are checked again.
_LANGUAGE_PASS = 'watchcraft_meme_language_pass_v2'


def backfill_languages(s: Session, limit: int = 200) -> int:
    """Re-detect the language of memes whose source did not give one, a bounded slice per call.

    A cursor walks every example once per detector version, so captions that stay
    undetectable cannot keep the same first rows selected forever, and guesses made by
    an older, looser detector get corrected.
    """
    after = int(get_kv(_LANGUAGE_PASS, {}, s=s).get('after') or 0)
    rows = s.exec(select(MemeExample, ContentItem).join(ContentItem, ContentItem.id == MemeExample.item_id)
                  .where(MemeExample.item_id > after).order_by(MemeExample.item_id).limit(limit)).all()
    changed = 0
    for ex, item in rows:
        if str((item.meta or {}).get('language', '')):
            continue
        lang = detect_language(f'{item.title or ""} {item.summary or ""}')
        if lang != ex.language:
            ex.language = lang
            s.add(ex)
            changed += 1
    if rows:
        save_setting(_LANGUAGE_PASS, {'after': rows[-1][0].item_id}, s=s)  # commits with the rows
    return changed


_TITLE_PASS = 'watchcraft_meme_title_pass_v1'


def backfill_titles(s: Session, limit: int = 500) -> int:
    """Take links out of meme titles stored before captions were cleaned, a bounded slice per call."""
    from .engine import index  # engine imports this module
    after = int(get_kv(_TITLE_PASS, {}, s=s).get('after') or 0)
    rows = s.exec(select(ContentItem).where(ContentItem.type == 'meme', ContentItem.id > after)
                  .order_by(ContentItem.id).limit(limit)).all()
    changed = 0
    for item in rows:
        title = drop_links(item.title or '')
        if title and title != item.title:
            item.title = title
            s.add(item)
            index(s, item)
            changed += 1
    if rows:
        save_setting(_TITLE_PASS, {'after': rows[-1].id}, s=s)  # commits with the rows
    return changed


def analyze_item(s: Session, item: ContentItem) -> MemeExample | None:
    """Assign hash identity, exact-duplicate link and template group to one meme item."""
    if s.exec(select(MemeExample).where(MemeExample.item_id == item.id)).first():
        return None
    asset = s.get(CapturedAsset, (item.id, 'image'))
    language = str((item.meta or {}).get('language', ''))[:8]
    if not language:
        # Mastodon often leaves the language null; the caption still says what it is.
        language = detect_language(f'{item.title or ""} {item.summary or ""}')
    ex = MemeExample(item_id=item.id, community=(item.meta or {}).get('community', '')[:200], language=language)
    is_video = (item.meta or {}).get('media', {}).get('type') in ('video', 'gifv')
    video = s.get(CapturedAsset, (item.id, 'video')) if is_video else None
    if asset or video:
        ex.has_media = True
        # A shared poster is not proof that two clips contain the same video.
        ex.media_sha = media_sha(video.data) if video else ('' if is_video else media_sha(asset.data))
        twin = s.exec(select(MemeExample).where(MemeExample.media_sha == ex.media_sha, MemeExample.media_sha != '', MemeExample.item_id != item.id,
                      MemeExample.dup_of == None).order_by(MemeExample.item_id)).first()  # noqa: E711
        if twin:
            ex.dup_of, ex.phash, ex.format_id = twin.item_id, '', twin.format_id
            s.add(ex); s.flush()
            if twin.format_id:
                _refresh_format(s, s.get(MemeFormat, twin.format_id))
            return ex
        try:
            ex.phash = dhash(Image.open(io.BytesIO(asset.data))) if asset else ''
        except Exception:  # noqa: BLE001 — undecodable asset keeps exact-media dedup only
            ex.phash = ''
    else:
        if item.image:
            return None  # image not captured yet; retry on a later sweep
        if is_video:
            s.add(ex); s.flush()
            return ex
        ex.text_sha = caption_sha(item)
        twin = s.exec(select(MemeExample).where(MemeExample.text_sha == ex.text_sha, MemeExample.item_id != item.id,
                      MemeExample.dup_of == None).order_by(MemeExample.item_id)).first()  # noqa: E711
        if twin:
            ex.dup_of, ex.format_id = twin.item_id, twin.format_id
            if not twin.format_id:  # a second identical caption defines a text template
                fmt = _create_format(s, 'text', s.get(ContentItem, twin.item_id), '')
                twin.format_id, ex.format_id = fmt.id, fmt.id
                s.add(twin)
                _refresh_format(s, fmt)
            else:
                _refresh_format(s, s.get(MemeFormat, twin.format_id))
            s.add(ex); s.flush()
            return ex
    s.add(ex); s.flush()
    if ex.phash:
        best_fmt, best_item, best_dist = None, None, 64
        for f in s.exec(select(MemeFormat).where(MemeFormat.kind == 'visual', MemeFormat.phash != '')).all():
            d = hamming(ex.phash, f.phash)
            if d <= PHASH_FORMAT_DIST and d < best_dist:
                best_fmt, best_dist = f, d
        if not best_fmt:
            for other in s.exec(select(MemeExample).where(MemeExample.format_id == None,  # noqa: E711
                                                          MemeExample.phash != '', MemeExample.item_id != item.id,
                                                          MemeExample.dup_of == None)).all():  # noqa: E711
                d = hamming(ex.phash, other.phash)
                if d <= PHASH_FORMAT_DIST and d < best_dist:
                    best_item, best_dist = other, d
        if best_fmt:
            ex.format_id = best_fmt.id
            _refresh_format(s, best_fmt)
        elif best_item:
            fmt = _create_format(s, 'visual', s.get(ContentItem, best_item.item_id), best_item.phash)
            best_item.format_id, ex.format_id = fmt.id, fmt.id
            s.add(best_item)
            _refresh_format(s, fmt)
    s.refresh(ex)
    return ex


def pending_ids(s: Session, limit=200) -> list[int]:
    seen = select(MemeExample.item_id)
    return list(s.exec(select(ContentItem.id).where(ContentItem.type == 'meme', ContentItem.id.not_in(seen))
                       .order_by(ContentItem.id).limit(limit)).all())


def analyze_pending(s: Session, limit=60) -> int:
    done = 0
    for iid in pending_ids(s, limit):
        if analyze_item(s, s.get(ContentItem, iid)):
            done += 1
    s.commit()
    return done


# ---------- trends (counted, never invented)

def _days(observations: list[float], now: float) -> list[int]:
    """Newest-last daily counts over the window; observations are first-seen timestamps."""
    counts = [0] * WINDOW_DAYS
    for ts in observations:
        idx = WINDOW_DAYS - 1 - int((now - ts) // 86400)
        if 0 <= idx < WINDOW_DAYS:
            counts[idx] += 1
    return counts


def label_state(counts: list[int], total: int, communities: int, days_since_first: float) -> tuple[str, list[str], bool]:
    """A pure function of counted signals. Sparse samples never earn a strong label."""
    sparse = total < 5
    last7, prev7 = sum(counts[-7:]), sum(counts[-14:-7])
    baseline_days = max(1, len(counts) - 14)
    baseline_week = sum(counts[:-14]) * 7 / baseline_days if len(counts) > 14 else 0
    base = max(baseline_week, 1)
    ratio = last7 / base
    last2 = sum(counts[-2:])
    evidence = [
        f'{last7} new example{"s" if last7 != 1 else ""} in the last 7 days',
        f'{prev7} in the 7 days before that',
        f'baseline about {baseline_week:.1f} per week before the last fortnight',
        f'{communities} independent communit{"y" if communities == 1 else "ies"} seen',
    ]
    if sparse:
        evidence.append(f'sample small ({total} examples in 30 days) — not enough to call a trend')
    if sparse and not (last7 == 0 and prev7 >= 2):
        return 'newly_observed', evidence, sparse  # a small sample never earns a strong label
    if total < 3:
        return 'newly_observed', evidence, sparse
    if last7 == 0:
        return ('fading' if prev7 >= 2 else 'newly_observed'), evidence, sparse
    if ratio >= 2.0 and last7 >= 6 and communities >= 3:
        return 'trending', evidence, sparse
    if ratio >= 1.5 and last7 >= 3 and communities >= 2:
        return 'emerging', evidence, sparse
    if ratio <= 0.4 and prev7 >= 4:
        return 'fading', evidence, sparse
    if days_since_first >= 14:
        return 'established', evidence, sparse
    return 'newly_observed', evidence, sparse


def _metrics(counts: list[int], total: int, communities: int, first: float | None, last: float | None) -> dict:
    return {'last7': sum(counts[-7:]), 'prev7': sum(counts[-14:-7]),
            'baseline_week': round(sum(counts[:-14]) * 7 / max(1, len(counts) - 14), 2) if len(counts) > 14 else 0.0,
            'samples': total, 'communities': communities, 'first_observed': first, 'last_observed': last,
            'sparse': total < 5}


def refresh_trends(s: Session) -> int:
    """Recount every format- and community-target trend from stored first-seen times."""
    now = time.time()
    targets: list[tuple[str, str, list[int]]] = []
    for f in s.exec(select(MemeFormat).where(MemeFormat.example_count >= 2)).all():
        members = s.exec(select(MemeExample.item_id).where(MemeExample.format_id == f.id)).all()
        targets.append(('format', str(f.id), list(members)))
    community_rows = s.exec(select(MemeExample.community, func.count(MemeExample.item_id))
                            .where(MemeExample.community != '')
                            .group_by(MemeExample.community).having(func.count(MemeExample.item_id) >= 2)).all()
    for community, _ in community_rows:
        members = s.exec(select(MemeExample.item_id).where(MemeExample.community == community)).all()
        targets.append(('community', community, list(members)))
    changed = 0
    for kind, key, item_ids in targets:
        rows = s.exec(select(ContentItem.first_seen, MemeExample.community)
                      .join(MemeExample, MemeExample.item_id == ContentItem.id)
                      .where(ContentItem.id.in_(item_ids))).all()
        if not rows:
            continue
        observations = [r[0] or 0 for r in rows]
        counts = _days(observations, now)
        total = sum(counts)
        communities = len({r[1] for r in rows if r[1] and 0 <= now - (r[0] or 0) < WINDOW_DAYS * 86400})
        first, last = min(observations), max(observations)
        state, evidence, _sparse = label_state(counts, total, communities, (now - first) / 86400)
        trend = s.exec(select(MemeTrend).where(MemeTrend.target_kind == kind, MemeTrend.target_key == key)).first()
        days = [datetime.fromtimestamp(now - (WINDOW_DAYS - 1 - i) * 86400, tz=timezone.utc).strftime('%Y-%m-%d')
                for i in range(WINDOW_DAYS)]
        if not trend:
            trend = MemeTrend(target_kind=kind, target_key=key, state='newly_observed', prev_state='')
            s.add(trend)
            s.flush()
        moved = trend.state != state
        trend.prev_state = trend.state if moved else trend.prev_state
        trend.state = state
        trend.first_observed, trend.last_observed = first, last
        trend.observations = [{'day': d, 'count': c} for d, c in zip(days, counts)]
        trend.metrics = _metrics(counts, total, communities, first, last)
        trend.evidence = evidence
        trend.updated_at = now
        s.add(trend)
        changed += 1 if moved else 0
    s.commit()
    return changed


# ---------- autonomy: keep a meme curator pointed at a source that returns posts

def is_meme_curator(c) -> bool:
    """A curator that collects memes: the Meme Tracker template and anything else typed 'meme'."""
    return c.kind == 'watch' and 'meme' in (c.content_types or [])


def _autopilot_key(cid: int) -> str:
    return f'watchcraft_meme_autopilot_{cid}'


def _probe(instance: str, queries: list[str]) -> int:
    """How many public posts one instance returns for the curator's own hashtags."""
    from .providers import mastodon
    # No queries means the instance's public timeline, which is what the provider does too.
    for q in (queries[:PROBE_QUERIES] or ['']):
        try:
            got = len(mastodon(f'https://{instance}', q))
        except Exception:  # noqa: BLE001 — an instance that refuses is simply not a candidate
            continue
        if got:
            return got
    return 0


def _working_instance(queries: list[str], skip: set[str]) -> str:
    for instance in DEFAULT_INSTANCES:
        if instance not in skip and _probe(instance, queries):
            return instance
    return ''


def ensure_source(c) -> str:
    """Point a meme curator at a public Mastodon source that actually returns posts.

    Returns a short note when it changed something and '' when the curator already works,
    the cooldown has not passed, or no public instance answered. A source the user set up
    and that returns posts is never touched.
    """
    with session() as s:
        row = s.get(Curator, c.id)
        if not row or not hub_allowed(s, row.profile_id):
            return ''
        sources = s.exec(select(Source).join(CuratorSource, CuratorSource.source_id == Source.id)
                         .where(CuratorSource.curator_id == c.id, Source.provider == 'mastodon')).all()
        # A source that returns posts, or that has not been tried yet, needs no help.
        if any(x.enabled and not x.last_error and (x.item_count > 0 or x.last_checked is None) for x in sources):
            return ''
        # Nor does a curator fed by other meme sources (Reddit memes, Lemmy, clips): Mastodon is the fallback.
        others = s.exec(select(Source).join(CuratorSource, CuratorSource.source_id == Source.id)
                        .where(CuratorSource.curator_id == c.id, Source.provider != 'mastodon', Source.enabled == True)).all()  # noqa: E712
        if others:
            return ''
        if time.time() - float(get_kv(_autopilot_key(c.id), {}).get('at') or 0) < AUTOPILOT_EVERY:
            return ''
        queries = [q for q in (row.queries or []) if q.strip()]
        instance = _working_instance(queries, {urlsplit(x.url).hostname or '' for x in sources})
        if not instance:
            save_setting(_autopilot_key(c.id), {'at': time.time(), 'result': 'no public instance answered'})
            return ''
        url = f'https://{instance}'
        target = s.exec(select(Source).where(Source.profile_id == row.profile_id, Source.provider == 'mastodon',
                                             Source.url == url)).first()
        if not target:
            # Re-point this curator's own broken source rather than piling up new ones.
            broken = [x for x in sources if not x.enabled or x.last_error or x.item_count == 0]
            target = next((x for x in broken if list(s.exec(select(CuratorSource.curator_id)
                                                            .where(CuratorSource.source_id == x.id)).all()) == [c.id]), None)
        if not target:
            target = Source(profile_id=row.profile_id, provider='mastodon', name=instance, url=url)
            s.add(target)
            s.flush()
        target.url, target.name, target.enabled = url, instance, True
        target.last_error, target.item_count, target.last_checked = '', 0, None
        if not s.get(CuratorSource, (c.id, target.id)):
            s.add(CuratorSource(curator_id=c.id, source_id=target.id))
        s.add(target)
        s.commit()
    note = f'following {instance} for public memes'
    save_setting(_autopilot_key(c.id), {'at': time.time(), 'result': note})
    return note


def sweep(throttled=True) -> dict:
    """Scheduled entry point: analyze new examples and recount trends; bounded work per tick."""
    if throttled and time.time() - float(get_kv('watchcraft_meme_sweep', {}).get('at') or 0) < 1800:
        return {'skipped': True}
    from .master import LOCK
    with LOCK, session() as s:
        analyzed = analyze_pending(s)
        backfill_languages(s)
        backfill_titles(s)
        transitions = refresh_trends(s)
        formats = s.exec(select(func.count()).select_from(MemeFormat)).one()
    save_setting('watchcraft_meme_sweep', {'at': time.time(), 'analyzed': analyzed})
    return {'analyzed': analyzed, 'transitions': transitions, 'formats': formats}


# ---------- queries for the API

def example_map(s: Session, ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    rows = s.exec(select(MemeExample, MemeFormat.name, ContentItem.meta)
                  .join(ContentItem, ContentItem.id == MemeExample.item_id).outerjoin(MemeFormat, MemeFormat.id == MemeExample.format_id)
                  .where(MemeExample.item_id.in_(ids))).all()
    return {ex.item_id: {'media_sha': ex.media_sha, 'has_media': ex.has_media, 'dup_of': ex.dup_of,
                         'format_id': ex.format_id, 'format_name': name or '', 'community': ex.community,
                         'language': ex.language, 'handle': meta.get('handle', ''),
                         'sensitive': bool(meta.get('sensitive')), 'spoiler': meta.get('spoiler', meta.get('spoiler_text', ''))}
            for ex, name, meta in rows}


def similar_ids(s: Session, item_id: int, limit=12) -> list[int]:
    ex = s.get(MemeExample, item_id)
    if not ex:
        return []
    out: list[tuple[int, int]] = []
    others = s.exec(select(MemeExample).where(MemeExample.item_id != item_id, MemeExample.dup_of == None)).all()  # noqa: E711
    for other in others:
        dist = 65
        if ex.media_sha and other.media_sha == ex.media_sha:
            dist = 0
        elif ex.phash and other.phash:
            dist = hamming(ex.phash, other.phash)
            if dist > PHASH_SIMILAR_DIST:
                continue
        elif ex.text_sha and other.text_sha and other.text_sha == ex.text_sha:
            dist = 0
        elif ex.format_id and other.format_id == ex.format_id:
            dist = 1
        else:
            continue
        out.append((other.item_id, dist))
    out.sort(key=lambda x: x[1])
    return [i for i, _ in out[:limit]]


def trend_dict(s: Session, t: MemeTrend, pid: int) -> dict:
    st = s.get(MemeTrendState, (pid, t.id))
    if t.target_kind == 'format':
        f = s.get(MemeFormat, int(t.target_key))
        name = f.name if f else ''
        exemplar, kind = (f.exemplar_item_id if f else None), (f.kind if f else 'visual')
        members = s.exec(select(MemeExample.item_id).where(MemeExample.format_id == int(t.target_key))).all()
    else:
        name, kind, exemplar = t.target_key, 'community', None
        members = s.exec(select(MemeExample.item_id).where(MemeExample.community == t.target_key)).all()
    visible = set(s.exec(select(CuratorDiscovery.item_id).where(CuratorDiscovery.profile_id == pid,
                  CuratorDiscovery.item_id.in_(list(members) or [-1]))).all())
    visible |= set(s.exec(select(ItemState.item_id).where(ItemState.profile_id == pid,
                   ItemState.item_id.in_(list(members) or [-1]))).all())
    featured = next((m for m in members if m in visible), None)
    return {'id': t.id, 'target_kind': t.target_kind, 'target_key': t.target_key, 'name': name[:120], 'kind': kind,
            'state': t.state, 'prev_state': t.prev_state, 'first_observed': t.first_observed,
            'last_observed': t.last_observed, 'observations': t.observations, 'metrics': t.metrics,
            'evidence': t.evidence, 'updated_at': t.updated_at, 'example_ids': [m for m in members if m in visible][:6],
            'featured_item_id': featured, 'exemplar_item_id': exemplar,
            'followed': bool(st and st.followed), 'hidden': bool(st and st.hidden)}
