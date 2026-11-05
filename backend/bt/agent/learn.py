"""Learn from unprocessed feedback: distil lessons (LLM) + adjust channel trust. No YouTube calls."""
from __future__ import annotations

import logging
from collections import defaultdict

from sqlmodel import col, func, select

from ..db import session
from ..models import Candidate, Category, Feedback, Lesson, Profile, ProfileChannel, Video, now
from . import prompts
from .runtime import Interrupt, Runtime

BATCH = 8
TRUST_DELTA = {2: 0.2, 1: 0.2, -1: -0.3}
APPROVED_REJECTION = "Approved rejection"
log = logging.getLogger(__name__)


def _norm(t: str) -> str:
    return " ".join("".join(ch for ch in t.lower() if ch.isalnum() or ch.isspace()).split())


def pending_count() -> int:
    with session() as s:
        return len(s.exec(select(Feedback.id).where(Feedback.processed == False)).all())  # noqa: E712


def _items(s, fbs: list[Feedback]) -> tuple[list[dict], set[str]]:
    items, channels = [], set()
    for f in fbs:
        if f.content_type == "article":
            from ..hub.models import ContentItem, ItemVerdict
            article = s.get(ContentItem, f.item_id) if f.item_id is not None else None
            verdict = s.get(ItemVerdict, (f.profile_id, f.item_id)) if f.item_id is not None else None
            items.append({
                "feedback_id": f.id, "content_type": "article", "item_id": f.item_id,
                "rating": f.rating, "chips": f.chips, "text": f.text,
                "category_id": None, "category": None, "channel_id": None, "channel": None,
                "article_title": article.title if article else f"Article {f.item_id or ''}".strip(),
                "publisher": article.publisher if article else None,
                "url": article.url if article else None,
                "summary": ((article.enrichment or {}).get("summary") or article.summary) if article else None,
                "article_excerpt": (article.body or "")[:1600] if article else None,
                "ai_score_when_picked": verdict.final if verdict else None,
                "ai_reasons_when_picked": verdict.reasons if verdict else None,
            })
            continue
        v = s.get(Video, f.video_id)
        cat = s.get(Category, f.category_id) if f.category_id else None
        cand = (s.exec(select(Candidate).where(Candidate.category_id == f.category_id,
                                               Candidate.video_id == f.video_id)).first()
                if f.category_id else None)
        if v and v.channel_id:
            channels.add(v.channel_id)
        items.append({
            "feedback_id": f.id, "content_type": "video", "video_id": f.video_id, "channel_id": v.channel_id if v else None, "rating": f.rating, "chips": f.chips, "text": f.text,
            "category_id": f.category_id, "category": cat.name if cat else None,
            "video_title": v.title if v else f.video_id,
            "channel": f"{v.channel_name} (id {v.channel_id})" if v and v.channel_id else None,
            "ai_summary_when_picked": cand.summary if cand else None,
            "ai_score_when_picked": cand.score if cand else None,
            "ai_reasons_when_picked": cand.reasons if cand else None,
            "ai_red_flags_when_picked": cand.red_flags if cand else None,
            "ai_tags": cand.tags if cand else None,
            "description_excerpt": (v.description or "")[:300] if v else None,
        })
    return items, channels


def _adjust_trust(s, profile_id: int, fbs: list[Feedback]) -> list[dict]:
    changes = []
    for f in fbs:
        if f.content_type != "video":
            continue
        # This rating is a correction to the AI's rejection, not approval of the channel.
        if f.rating == 1 and APPROVED_REJECTION in (f.chips or []):
            continue
        delta = TRUST_DELTA.get(f.rating)
        v = s.get(Video, f.video_id)
        if not delta or not v or not v.channel_id:
            continue
        pc = s.exec(select(ProfileChannel).where(ProfileChannel.profile_id == profile_id,
                                                 ProfileChannel.channel_id == v.channel_id)).first()
        pc = pc or ProfileChannel(profile_id=profile_id, channel_id=v.channel_id)
        old = pc.trust or 0.0
        pc.trust = round(max(-1.0, min(1.0, old + delta)), 3)
        s.add(pc)
        changes.append({"channel_id": v.channel_id, "channel": v.channel_name, "from": old, "to": pc.trust})
    return changes


def process(rt: Runtime) -> int:
    """Process up to one batch of feedback per profile. Returns number of feedback rows processed."""
    with session() as s:
        fbs = s.exec(select(Feedback).where(Feedback.processed == False)  # noqa: E712
                     .order_by(col(Feedback.created_at)).limit(50)).all()
    if not fbs:
        return 0
    by_profile_type: dict[tuple[int, str], list[Feedback]] = defaultdict(list)
    for f in fbs:
        key = (f.profile_id, f.content_type or "video")
        if len(by_profile_type[key]) < BATCH:
            by_profile_type[key].append(f)
    done = 0
    for (pid, content_type), batch in by_profile_type.items():
        rt.check()
        if not rt.llm_ok():
            break
        done += _process_profile(rt, pid, batch, content_type=content_type)
    return done


def _process_profile(rt: Runtime, pid: int, batch: list[Feedback], content_type: str = "video") -> int:
    with session() as s:
        prof = s.get(Profile, pid)
        cats = list(s.exec(select(Category).where(Category.profile_id == pid)).all()) if content_type == "video" else []
        lessons = list(s.exec(select(Lesson).where(Lesson.profile_id == pid, Lesson.content_type == content_type,
                                                   Lesson.active == True)).all())  # noqa: E712
        from .memory import select_memory
        core, lessons = select_memory(s, pid, lessons, content_type=content_type)
        items, channels = _items(s, batch)
    meaningful = [it for it in items if content_type == "article" or it["rating"] != 0
                  or it["chips"] or (it["text"] or "").strip()]
    learnable_ids = {it["feedback_id"] for it in meaningful}
    if content_type == "article":
        article_ids = {fb.item_id for fb in batch if fb.item_id is not None}
        with session() as s:
            latest_by_item = {}
            rows = (s.exec(select(Feedback).where(Feedback.profile_id == pid,
                                                  Feedback.content_type == "article",
                                                  Feedback.item_id.in_(article_ids)).order_by(Feedback.id)).all()
                    if article_ids else [])
            for row in rows:
                latest_by_item[row.item_id] = row.id
        learnable_ids = {fb.id for fb in batch if fb.item_id is not None
                         and latest_by_item.get(fb.item_id) == fb.id
                         and (fb.rating != 0 or fb.chips or (fb.text or "").strip())}
    out_lessons: list[dict] = []
    reviews: list[dict] = []
    if meaningful:
        rt.state("learning", f"Learning from {len(batch)} {content_type} feedback item(s)", None, profile_id=pid)
        rt.event("learn", f"🧠 Learning from {len(batch)} new {content_type} feedback item(s) ({prof.name if prof else pid})",
                 "info", profile_id=pid, payload={"feedback_ids": [f.id for f in batch], "content_type": content_type})
        msgs = prompts.learn_messages(prof.name if prof else str(pid), cats, lessons, meaningful, core=core,
                                      content_type=content_type)
        try:
            out = rt.llm.chat_json(msgs, json_schema=prompts.LEARN_SCHEMA, max_tokens=2500, schema_name="lessons",
                                   abort=rt.check)
        except Interrupt:
            raise
        except Exception as e:  # noqa: BLE001
            rt.llm_failed("learning from feedback", e, profile_id=pid)
            return 0
        out_lessons = [x for x in (out.get("lessons") or []) if isinstance(x, dict)][:3]
        reviews = [x for x in (out.get("reviews") or []) if isinstance(x, dict)][:8]

    from . import knowledge
    revised = []
    for proposal in reviews:
        rt.check()
        try:
            report = knowledge.review(rt, pid, proposal, {item["feedback_id"] for item in meaningful}, content_type=content_type)
            if report:
                revised.append(report)
        except Interrupt:
            raise
        except Exception as e:
            # Keep source rules intact; the feedback remains retryable on the next pass.
            rt.llm_failed("reviewing rule evidence", e, profile_id=pid)
            return 0

    cat_ids = {c.id for c in cats}
    learned, trust = [], []
    with session() as s:
        existing = {le.id: le for le in s.exec(select(Lesson).where(Lesson.profile_id == pid,
                                                                      Lesson.content_type == content_type)).all()}
        norm_existing = {(_norm(le.text), le.category_id, le.channel_id): le for le in existing.values() if le.active}
        for x in out_lessons:
            text = str(x.get("text") or "").strip()[:500]
            if not text:
                continue
            # The learner generated its response before the independent revision.
            # Do not resurrect that response's now-superseded original wording.
            if any(_norm(text) == _norm(report["before"]) or x.get("replaces_lesson_id") == report["lesson_id"]
                   for report in revised):
                continue
            cid = x.get("category_id")
            cid = int(cid) if isinstance(cid, (int, float, str)) and str(cid).isdigit() and int(cid) in cat_ids else None
            ch = x.get("channel_id")
            ch = ch if isinstance(ch, str) and ch in channels else None
            citations = x.get("source_feedback_ids")
            # Compatibility with older model/test outputs: only an unambiguous single
            # meaningful item can be attributed without explicit citations.
            if citations is None:
                citations = [next(iter(learnable_ids))] if len(learnable_ids) == 1 else []
            valid_ids = learnable_ids
            if (not isinstance(citations, list) or (content_type == "article" and not citations)
                    or any(type(i) is not int or i not in valid_ids for i in citations)):
                continue
            # Model-proposed replacements are evidence, not authority to erase a rule.
            # Only reinforce an identical lesson in the identical scope. Explicit user
            # edits remain authoritative through the settings API.
            if content_type == "article":
                cited_items = {fb.item_id for fid in citations if (fb := s.get(Feedback, fid))}
                latest_rows = (s.exec(select(Feedback.item_id, func.max(Feedback.id))
                                      .where(Feedback.profile_id == pid, Feedback.content_type == "article",
                                             Feedback.item_id.in_(cited_items))
                                      .group_by(Feedback.item_id)).all() if cited_items else [])
                latest_by_item = dict(latest_rows)
                if any((fb := s.get(Feedback, fid)) is None or latest_by_item.get(fb.item_id) != fid
                       for fid in citations):
                    continue
            target = norm_existing.get((_norm(text), cid, ch))
            if target is not None:
                target.updated_at = now()
                target.source_feedback_ids = sorted(set((target.source_feedback_ids or []) + citations))
                target.weight = round(min(3.0, (target.weight or 1.0) + 0.25), 2)
                s.add(target)
                learned.append((target, "reinforced"))
            else:
                if content_type == "article":
                    cid, ch = None, None
                le = Lesson(profile_id=pid, content_type=content_type, category_id=cid, channel_id=ch,
                            text=text, source_feedback_ids=sorted(set(citations)))
                s.add(le)
                s.flush()
                knowledge.register(s, le)
                norm_existing[(_norm(text), cid, ch)] = le
                learned.append((le, "new"))
        trust = _adjust_trust(s, pid, batch)
        for f in batch:
            row = s.get(Feedback, f.id)
            row.processed = True
            s.add(row)
        s.commit()
    for le, how in learned:
        rt.event("learn", f'🧠 Learned{"" if how == "new" else f" ({how})"}: "{le.text}"', "success",
                 profile_id=pid, category_id=le.category_id,
                 payload={"lesson_id": le.id, "how": how, "channel_id": le.channel_id, "content_type": content_type})
    for t in trust:
        rt.event("learn", f"📈 Channel trust {t['channel'] or t['channel_id']}: {t['from']:+.1f} → {t['to']:+.1f}",
                 "info", profile_id=pid, payload=t)
    if content_type == "article":
        try:
            from ..hub import article_preferences
            article_preferences.changed(pid)
        except Exception:  # noqa: BLE001 — feedback is durable even if rejudging cannot be queued
            log.exception("could not queue article rejudging after feedback learning for profile %s", pid)
    if meaningful and not learned:
        rt.event("learn", "🧠 Feedback noted — no new lesson needed", "debug", profile_id=pid)
    return len(batch)
