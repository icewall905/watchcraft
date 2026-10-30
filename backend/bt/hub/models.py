"""WatchCraft Hub tables (schema v2). Canonical content is stored once; everything
profile-specific (discoveries, state, collections, topics, alerts) is a relation."""
import time
from typing import Optional

from sqlalchemy import JSON, Column, Text, UniqueConstraint
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
