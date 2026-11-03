"""WatchCraft Hub tables (schema v2). Canonical content is stored once; everything
profile-specific (discoveries, state, collections, topics, alerts) is a relation."""
import time
from typing import Optional

from sqlalchemy import JSON, Column, Index, LargeBinary, Text, UniqueConstraint
from sqlmodel import Field, SQLModel


def J(default=None):
    return Field(default_factory=(lambda: [] if default is None else default.copy()), sa_column=Column(JSON))


def T(default=''):
    return Field(default=default, sa_column=Column(Text, default=default))


class Curator(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: int = Field(index=True)
    name: str
    instructions: str = ''
    # v1 single-source fields: kept for rollback; v2 uses CuratorSource.
    provider: str = 'rss'
    source: str = ''
    interval_hours: int = 6  # 0 = manual only
    enabled: bool = True
    next_run: float = 0
    last_success: float | None = None
    error: str = ''
    # v2
    kind: str = 'watch'  # watch | research
    description: str = ''
    icon: str = '✦'
    color: str = '#ffb547'
    tone: str = 'friendly'  # narration voice for this agent's timeline
    queries: list = J()
    keywords: list = J()
    exclusions: list = J()
    content_types: list = J()
    ai_enabled: bool = False
    min_score: int = 0
    max_per_run: int = 30
    collection_id: int | None = None
    topic_id: int | None = None
    alert_enabled: bool = False
    alert_keywords: list = J()
    alert_min_severity: str = 'high'
    subject: str = ''
    subject_type: str = ''
    aliases: list = J()
    # Curator feedback from findings ("more like this" / "less relevant"): learned title terms.
    tuned_up: list = J()
    tuned_down: list = J()
    report: str = ''
    report_at: float | None = None
    report_refs: list = J()  # item ids; report citation [n] = report_refs[n-1]
    last_run: float | None = None
    last_status: str = ''
    created_at: float = Field(default_factory=time.time)


class Source(SQLModel, table=True):
    __table_args__ = (UniqueConstraint('profile_id', 'provider', 'url'),)
    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: int = Field(index=True)
    provider: str
    name: str = ''
    url: str = ''
    enabled: bool = True
    last_success: float | None = None
    last_error: str = ''
    last_checked: float | None = None
    item_count: int = 0
    created_at: float = Field(default_factory=time.time)
    config: dict = J({})  # declarative watcher rules (see hub/watchers.py); {} = provider defaults
    last_repair: float | None = None  # last automatic repair attempt
    rules_changed_at: float | None = None  # page changes across a rule change are not alerted


class WatcherRevision(SQLModel, table=True):
    """Every tested watcher-rule change, so a source can be inspected and rolled back."""
    id: Optional[int] = Field(default=None, primary_key=True)
    source_id: int = Field(index=True)
    profile_id: int = Field(index=True)
    created_at: float = Field(default_factory=time.time)
    config: dict = J({})
    reason: str = ''  # edit | repair | auto-repair | rollback
    item_count: int = 0
    sample: list = J()


class SitemapEntry(SQLModel, table=True):
    """URLs a sitemap source has listed, to tell new and updated pages from known ones."""
    __table_args__ = (UniqueConstraint('source_id', 'url'),)
    id: Optional[int] = Field(default=None, primary_key=True)
    source_id: int = Field(index=True)
    profile_id: int = Field(index=True)
    url: str
    lastmod: float | None = None
    first_seen: float = Field(default_factory=time.time)
    last_seen: float = Field(default_factory=time.time)
    changed_at: float = Field(default_factory=time.time, index=True)
    change: str = 'new'  # baseline | new | updated


class PageRevision(SQLModel, table=True):
    """Profile-scoped observations of a monitored page, oldest first."""
    id: Optional[int] = Field(default=None, primary_key=True)
    source_id: int = Field(index=True)
    profile_id: int = Field(index=True)
    observed_at: float = Field(default_factory=time.time)
    digest: str
    title: str = ''
    body: str = T()


class SourceCheck(SQLModel, table=True):
    """A bounded history of actual source checks, scoped to the owning profile."""
    id: Optional[int] = Field(default=None, primary_key=True)
    source_id: int = Field(index=True)
    profile_id: int = Field(index=True)
    checked_at: float = Field(default_factory=time.time)
    trigger: str = ''
    status: str = ''
    item_count: int = 0
    error: str = ''


class SourcePoll(SQLModel, table=True):
    """Shared raw public-feed responses; never contains curator/profile interpretation."""
    key: str = Field(primary_key=True)
    fetched_at: float = 0
    items: list = J()
    error: str = ''
    lease_until: float = 0
    lease_token: str = ''


class ScoutPlan(SQLModel, table=True):
    __table_args__ = (UniqueConstraint('profile_id', 'query'),)
    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: int = Field(index=True)
    query: str
    interval_hours: int = 24
    enabled: bool = True
    next_run: float = 0
    created_at: float = Field(default_factory=time.time)


class ScoutRun(SQLModel, table=True):
    """Profile-owned, bounded research results; suggestions require explicit approval."""
    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: int = Field(index=True)
    query: str = ''
    status: str = 'running'
    created_at: float = Field(default_factory=time.time)
    finished_at: float | None = None
    suggestions: list = J()
    sitemaps: list = J()
    error: str = ''


class CuratorSource(SQLModel, table=True):
    curator_id: int = Field(primary_key=True)
    source_id: int = Field(primary_key=True, index=True)
    sitemap_cursor: float | None = None  # start of the run that last read this sitemap (see engine.track_sitemap)


class ContentItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    url: str = Field(unique=True)
    type: str = Field(default='web', index=True)
    title: str
    summary: str = T()  # from the source, never AI
    body: str = T()  # extracted plain text (bounded) for full-text search
    author: str = ''
    publisher: str = ''
    image: str = ''
    published_at: float | None = Field(default=None, index=True)
    first_seen: float = Field(default_factory=time.time, index=True)
    provider: str = ''
    external_id: str = Field(default='', index=True)
    meta: dict = J({})  # type-specific source metadata
    enrichment: dict = J({})  # AI output, kept apart from source facts


class CaptureJob(SQLModel, table=True):
    profile_id: int = Field(primary_key=True)
    item_id: int = Field(primary_key=True)
    status: str = Field(default='queued', index=True)
    attempts: int = 0
    next_run: float = Field(default=0, index=True)
    lease: float = 0
    error: str = ''
    summary_status: str = 'pending'
    updated_at: float = Field(default_factory=time.time)


class CapturedAsset(SQLModel, table=True):
    item_id: int = Field(primary_key=True)
    kind: str = Field(primary_key=True)  # image | document | video
    mime: str
    data: bytes = Field(sa_column=Column(LargeBinary, nullable=False))
    size: int = 0
    source_url: str = ''


class CapturedPage(SQLModel, table=True):
    item_id: int = Field(primary_key=True)
    links: list = J()
    captured_at: float = Field(default_factory=time.time)
    truncated: bool = False


class CuratorRun(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    curator_id: int = Field(index=True)
    profile_id: int
    started_at: float = Field(default_factory=time.time)
    finished_at: float | None = None
    status: str = 'running'  # running | ok | partial | failed
    trigger: str = 'schedule'
    new_items: int = 0
    seen_items: int = 0
    errors: list = J()


class CuratorDiscovery(SQLModel, table=True):
    __table_args__ = (UniqueConstraint('curator_id', 'item_id'),)
    id: Optional[int] = Field(default=None, primary_key=True)
    curator_id: int = Field(index=True)
    item_id: int = Field(index=True)
    profile_id: int = Field(index=True)
    source_id: int | None = None
    query: str = ''
    found_at: float = Field(default_factory=time.time, index=True)
    score: float | None = None
    reason: str = ''


class ItemState(SQLModel, table=True):
    profile_id: int = Field(primary_key=True)
    item_id: int = Field(primary_key=True, index=True)
    saved: bool = False
    saved_at: float | None = None
    favorite: bool = False
    read_later: bool = False
    archived: bool = False
    dismissed: bool = False
    read_at: float | None = None
    notes: str = T()
    tags: list = J()
    rating: int = 0  # -1 dislike, 1 like


class Collection(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: int = Field(index=True)
    name: str
    description: str = ''
    icon: str = '📁'
    color: str = '#ffb547'
    view: str = 'grid'
    pinned: bool = False
    created_at: float = Field(default_factory=time.time)


class CollectionItem(SQLModel, table=True):
    collection_id: int = Field(primary_key=True)
    item_id: int = Field(primary_key=True, index=True)
    added_at: float = Field(default_factory=time.time)


class Topic(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: int = Field(index=True)
    name: str
    description: str = T()
    keywords: list = J()
    notes: str = T()
    kind: str = 'knowledge'  # knowledge | news
    muted: bool = False
    pinned: bool = False
    created_at: float = Field(default_factory=time.time)


class TopicItem(SQLModel, table=True):
    topic_id: int = Field(primary_key=True)
    item_id: int = Field(primary_key=True, index=True)
    added_at: float = Field(default_factory=time.time)


class ScoutReportConfig(SQLModel, table=True):
    profile_id: int = Field(primary_key=True)
    frequency: str = 'manual'
    next_run: float | None = None
    show_home: bool = False


class ScoutReportIssue(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: int = Field(index=True)
    curator_id: int | None = None
    period: str = 'daily'
    created_at: float = Field(default_factory=time.time, index=True)
    item_ids: list = J()
    sections: dict = J({})


class Briefing(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: int = Field(index=True)
    name: str
    curator_ids: list = J()
    topic_ids: list = J()
    frequency: str = 'manual'  # manual | daily | weekly
    length: str = 'concise'
    last_generated: float | None = None
    next_run: float | None = None
    created_at: float = Field(default_factory=time.time)


class BriefingIssue(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    briefing_id: int = Field(index=True)
    created_at: float = Field(default_factory=time.time)
    content: str = T()
    item_ids: list = J()
    ai: bool = False


class Alert(SQLModel, table=True):
    __table_args__ = (UniqueConstraint('profile_id', 'item_id'),)
    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: int = Field(index=True)
    item_id: int
    curator_id: int | None = None
    severity: str = 'info'
    title: str = ''
    reason: str = ''
    state: str = Field(default='new', index=True)  # new | read | resolved
    snooze_until: float | None = None
    created_at: float = Field(default_factory=time.time)


class SavedSearch(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: int = Field(index=True)
    name: str
    query: str = ''
    filters: dict = J({})
    pinned: bool = False
    created_at: float = Field(default_factory=time.time)


class MemeExample(SQLModel, table=True):
    """A collected meme example: hash identity + template link on top of the canonical ContentItem."""
    item_id: int = Field(primary_key=True)
    media_sha: str = T('')          # sha256 of the exact captured media bytes
    phash: str = T('')              # 64-bit dHash hex for visual examples ('' for text-only)
    text_sha: str = T('')           # normalized caption hash for text memes
    community: str = T('')          # community/hashtag/account the example came from
    language: str = T('')
    has_media: bool = False
    dup_of: int | None = None       # item id of the exact-media/caption original this repeats
    format_id: int | None = Field(default=None, index=True)
    analyzed_at: float = Field(default_factory=time.time)


class MemeFormat(SQLModel, table=True):
    """A recurring joke/visual template: created once two examples are perceptually or textually alike."""
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = ''
    kind: str = 'visual'            # visual | text
    phash: str = T('')              # exemplar dHash hex (visual formats)
    exemplar_item_id: int | None = None
    example_count: int = 1
    community_count: int = 1
    first_seen: float = Field(default_factory=time.time)
    last_seen: float = Field(default_factory=time.time)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class MemeTrend(SQLModel, table=True):
    """Observed activity around a format or community. Every metric is counted, never estimated."""
    __table_args__ = (UniqueConstraint('target_kind', 'target_key'),)
    id: Optional[int] = Field(default=None, primary_key=True)
    target_kind: str = 'format'     # format | community
    target_key: str = ''            # format id as string, or community label
    state: str = 'newly_observed'   # newly_observed | emerging | trending | established | fading
    prev_state: str = T('')
    first_observed: float | None = None
    last_observed: float | None = None
    observations: list = J()        # [{'day': 'YYYY-MM-DD', 'count': n}, …] newest last, 30 days
    metrics: dict = J({})           # counted metrics + sample flag
    evidence: list = J()            # human-readable factual sentences backing the label
    updated_at: float = Field(default_factory=time.time)


class MemeTrendState(SQLModel, table=True):
    """Per-profile follow/hide on a trend."""
    profile_id: int = Field(primary_key=True)
    trend_id: int = Field(primary_key=True)
    followed: bool = False
    hidden: bool = False


class ItemVector(SQLModel, table=True):
    """Embedding for one item under one model. Switching models re-embeds instead of mixing spaces."""
    item_id: int = Field(primary_key=True)
    model: str = Field(primary_key=True)
    dim: int = 0
    # Packed float32, not JSON text: 3 KB per 768-dim vector instead of 17 KB, and unpacking
    # costs microseconds where json.loads cost 0.22 ms. `dim` is the length check.
    vector: bytes = Field(default=b'', sa_column=Column(LargeBinary, default=b''))
    created_at: float = Field(default_factory=time.time)


# ---------- Master curator: per-profile verdicts, stories, daily editions and learned taste

class ItemVerdict(SQLModel, table=True):
    """The master curator's final word on one item for one profile. Scouts find; this decides what is shown."""
    __table_args__ = (Index('ix_itemverdict_feed', 'profile_id', 'tier', 'final'),)
    profile_id: int = Field(primary_key=True)
    item_id: int = Field(primary_key=True, index=True)
    quality: float = 0       # 0–100: is it any good, regardless of taste
    relevance: float = 0     # 0–100: does it match this profile
    final: float = 0
    tier: str = 'keep'  # feature | keep | set_aside | reject
    reasons: list = J()      # short tags, e.g. ["stale", "duplicate", "liked source"]
    judged_by: str = 'rules'  # rules | llm
    pass_version: int = 0
    overruled: str = ''      # '' | promoted | demoted: the user's call, which later passes keep
    forced: bool = False     # tier fixed by a rule or the user, not by score: a model has nothing to add
    judged_at: float = Field(default_factory=time.time)


class Story(SQLModel, table=True):
    """Items about the same thing (a news event, a meme format, a topic thread), folded into one entry.
    Shared across profiles like ContentItem; what each profile sees comes from its verdicts."""
    id: Optional[int] = Field(default=None, primary_key=True)
    kind: str = Field(default='news', index=True)  # news | meme_format | topic
    title: str = ''
    summary: str = T()
    lead_item_id: int | None = None
    first_seen: float = Field(default_factory=time.time)
    last_seen: float = Field(default_factory=time.time, index=True)
    heat: float = 0


class StoryItem(SQLModel, table=True):
    """An item belongs to at most one story."""
    item_id: int = Field(primary_key=True)
    story_id: int = Field(index=True)
    added_at: float = Field(default_factory=time.time)


class Edition(SQLModel, table=True):
    """One profile's calm daily overview: ordered sections of featured items and stories."""
    __table_args__ = (UniqueConstraint('profile_id', 'day'),)
    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: int = Field(index=True)
    day: str = Field(index=True)  # YYYY-MM-DD in the profile's household timezone
    intro: str = T()
    intro_refs: list = J()   # intro citation [n] = intro_refs[n-1]
    sections: list = J()     # [{"key": "stories", "title": ..., "story_ids": [...], "item_ids": [...]}]
    shown_item_ids: list = J()  # what the reader has already seen today; later passes never reshuffle these
    source_run_ids: list = J()
    built_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class HubLesson(SQLModel, table=True):
    """A plain-language taste rule learned from what a profile keeps, dismisses or overrules."""
    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: int = Field(index=True)
    text: str = T()
    scope: str = ''          # '' = all hub items, or a type such as meme / news / article
    polarity: int = 1        # 1 more like this, -1 less like this
    weight: float = 1.0
    evidence: int = 0
    active: bool = True
    user_edited: bool = False  # the user wrote or changed it; consolidation must not rewrite it
    source_item_ids: list = J()
    last_evidence_at: float = Field(default_factory=time.time)  # decay runs from here, not from edits
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class HubPreferenceCore(SQLModel, table=True):
    """The consolidated taste summary the curator reads, rebuilt nightly from active lessons."""
    profile_id: int = Field(primary_key=True)
    text: str = T()
    sources: dict = J({})
    built_at: float = Field(default_factory=time.time)


class MasterQueue(SQLModel, table=True):
    """A profile whose hub items changed and await a curator pass. Runs debounce into one pass."""
    profile_id: int = Field(primary_key=True)
    first_at: float = Field(default_factory=time.time)  # first unjudged change; bounds the debounce
    due_at: float = Field(default_factory=time.time, index=True)
    lease_until: float = 0
    gen: int = 0       # bumped by every change; a pass only clears the row if nothing changed since its claim
    attempts: int = 0  # passes that skipped model judging (GPU busy) or failed; bounded retries
