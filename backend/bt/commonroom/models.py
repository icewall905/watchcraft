"""Common Room tables. Scoped by profile_id (threads, picks, feedback); posts and sources are shared."""
import time
from typing import Optional

from sqlalchemy import Column, JSON, UniqueConstraint
from sqlmodel import Field, SQLModel


class CRThread(SQLModel, table=True):
    """One interest. Its curator harvests sources, judges posts and keeps the good ones as picks."""
    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: int = Field(index=True)
    name: str
    interests: str = ''
    instructions: str = ''
    avoid: str = ''
    strictness: str = 'balanced'      # strict | balanced | loose
    child_friendly: bool = False      # forced on for child profiles
    enabled: bool = True
    position: int = 0
    interval_minutes: int = 30
    next_run: float = 0
    last_run: float | None = None
    last_status: str = ''
    error: str = ''
    lease: str = ''
    build_started: float = 0
    source_offset: int = 0            # rotates interests/searches across runs
    discovered_at: float = 0          # last AI source discovery
    want_more: bool = False           # the feed ran low; backfill next run
    search_terms: list = Field(default_factory=list, sa_column=Column(JSON))  # model-expanded subtopics
    created_at: float = Field(default_factory=time.time)


class CRSource(SQLModel, table=True):
    """A community/feed on one platform, shared by every thread that follows it."""
    __table_args__ = (UniqueConstraint('platform', 'locator'),)
    id: Optional[int] = Field(default=None, primary_key=True)
    platform: str = Field(index=True)
    locator: str
    label: str = ''
    fresh_marker: str = ''            # newest external id/time already seen
    ladder: int = 0                   # index into the adapter's sort ladder for backfill
    backfill_cursor: str = ''         # page token within the current ladder step
    exhausted: bool = False           # walked every ladder step to the end
    exhausted_at: float = 0           # when; the ladder re-arms after harvest.REARM_S
    next_poll: float = 0
    last_ok: float = 0
    failures: int = 0
    error: str = ''
    # Shared library health, filled by harvest and the source expedition.
    status: str = 'ok'                # ok | broken | disabled
    notes: str = ''
    created_by_thread: int = 0
    hits: int = 0                     # posts fetched
    kept: int = 0                     # posts some thread kept


class CRThreadSource(SQLModel, table=True):
    __table_args__ = (UniqueConstraint('thread_id', 'source_id'),)
    id: Optional[int] = Field(default=None, primary_key=True)
    thread_id: int = Field(index=True)
    source_id: int = Field(index=True)
    origin: str = 'user'              # user | ai
    enabled: bool = True
    fit: int = 0                      # expedition sample: posts that fit ...
    tried: int = 0                    # ... of posts judged
    kept: int = 0                     # posts this thread kept from the source
    created_at: float = Field(default_factory=time.time)


class CRSourceHunt(SQLModel, table=True):
    """One source expedition for a thread: a durable job with a lease and a short log."""
    id: Optional[int] = Field(default=None, primary_key=True)
    thread_id: int = Field(index=True)
    status: str = 'queued'            # queued | running | done | failed
    reason: str = ''                  # new | button | dry | weekly
    lease: str = ''
    lease_until: float = 0
    found: int = 0
    error: str = ''
    log: list = Field(default_factory=list, sa_column=Column(JSON))
    created_at: float = Field(default_factory=time.time)
    started_at: float = 0
    finished_at: float = 0


class CRPost(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    url: str = Field(unique=True)     # canonical permalink of the post on its platform
    platform: str = Field(index=True)
    community: str = ''
    external_id: str = ''
    author: str = ''
    title: str = ''
    body: str = ''
    link_url: str = ''
    link_key: str = Field(default='', index=True)  # normalised link_url: the same link discussed elsewhere
    domain: str = ''
    media: list = Field(default_factory=list, sa_column=Column(JSON))
    meta: dict = Field(default_factory=dict, sa_column=Column(JSON))
    score: int = 0
    comments: int = 0
    published_at: float = Field(default=0, index=True)
    fetched_at: float = Field(default_factory=time.time, index=True)
    nsfw: bool = False
    spoiler: bool = False
    language: str = Field(default='', index=True)  # platform's tag, else detected; '' = unknown
    kid_ok: int | None = None         # None = unchecked, 1 = passed, 0 = failed (fail closed)
    kid_image_ok: int | None = None


class CRPostVector(SQLModel, table=True):
    post_id: int = Field(primary_key=True)
    model: str = ''
    dim: int = 0
    vector: bytes = b''


class CRCandidate(SQLModel, table=True):
    """A post a thread has already judged (kept or not), so it is never judged twice."""
    __table_args__ = (UniqueConstraint('thread_id', 'post_id'),)
    id: Optional[int] = Field(default=None, primary_key=True)
    thread_id: int = Field(index=True)
    post_id: int = Field(index=True)
    status: str = 'pending'           # pending | kept | rejected
    misses: int = 0                   # answered judge batches that skipped this post
    created_at: float = Field(default_factory=time.time)


class CRPick(SQLModel, table=True):
    __table_args__ = (UniqueConstraint('thread_id', 'post_id'),)
    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: int = Field(index=True)
    thread_id: int = Field(index=True)
    post_id: int = Field(index=True)
    score: int = 0
    why: str = ''
    fun: int = 0                      # judge's 0-100 entertainment score
    kind: str = ''                    # humor | showcase | question | discussion | news | deepdive | oddity
    wildcard: bool = False            # kept for being a delight though only loosely on-topic
    published_at: float = Field(default=0, index=True)
    picked_at: float = Field(default_factory=time.time, index=True)
    seen_at: float = 0
    saved: bool = False
    hidden: bool = False
    hidden_at: float = 0


class CRFeedback(SQLModel, table=True):
    __table_args__ = (UniqueConstraint('profile_id', 'thread_id', 'post_id'),)
    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: int = Field(index=True)
    thread_id: int = Field(index=True)
    post_id: int = Field(index=True)
    verdict: str = ''                 # up | down | ''
    reasons: list = Field(default_factory=list, sa_column=Column(JSON))
    note: str = ''
    title: str = ''
    community: str = ''
    created_at: float = Field(default_factory=time.time, index=True)


class CRBlock(SQLModel, table=True):
    __table_args__ = (UniqueConstraint('profile_id', 'kind', 'value'),)
    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: int = Field(index=True)
    kind: str                         # community | author | domain
    value: str
    created_at: float = Field(default_factory=time.time)


class CRComments(SQLModel, table=True):
    """Cached comment tree; `kid` holds the child-filtered copy (None until checked)."""
    post_id: int = Field(primary_key=True)
    tree: list = Field(default_factory=list, sa_column=Column(JSON))
    kid: list | None = Field(default=None, sa_column=Column(JSON))
    fetched_at: float = 0
    count: int = 0          # the post's comment count when the tree was fetched
    checked_at: float = 0   # last fetch attempt, successful or not (refetch backoff)


class CRSourcePost(SQLModel, table=True):
    """Which source delivered a post, so every thread following that source can judge it."""
    __table_args__ = (UniqueConstraint('source_id', 'post_id'),)
    id: Optional[int] = Field(default=None, primary_key=True)
    source_id: int = Field(index=True)
    post_id: int = Field(index=True)
    created_at: float = Field(default_factory=time.time, index=True)


class CRVisit(SQLModel, table=True):
    """Visit boundary per profile and scope ('all' or a thread id), splitting New from Deep."""
    __table_args__ = (UniqueConstraint('profile_id', 'scope'),)
    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: int = Field(index=True)
    scope: str
    boundary: float = 0               # previous visit time: picks after it are "New"
    visit_at: float = 0               # this visit's start: picks after it join the end of Deep


class CRAsset(SQLModel, table=True):
    """Captured, kid-checked picture bytes; the only image a child profile is ever served."""
    post_id: int = Field(primary_key=True)
    mime: str = ''
    data: bytes = b''
