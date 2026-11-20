"""One ledger of what each profile taught its curator, and the living knowledge it was woven into."""
import time
from typing import Optional

from sqlalchemy import Index, UniqueConstraint
from sqlmodel import Field, SQLModel

from ..hub.models import J, T

SURFACES = ('video', 'short', 'article', 'news', 'meme', 'web', 'moodboard', 'music', 'hub', 'correction')
SECTIONS = {
    'overall': 'Overall', 'videos': 'Videos', 'shorts': 'Shorts', 'news': 'News', 'articles': 'Articles',
    'memes': 'Memes', 'web': 'Web & projects', 'moodboards': 'Mood Boards', 'music': 'Music',
}
CONFIDENCE = ('tentative', 'established', 'explicit')


class Teaching(SQLModel, table=True):
    """One thing the curator learned, with the signals that taught it. Rejected rows are tombstones."""
    __table_args__ = (UniqueConstraint('profile_id', 'origin', 'origin_id', name='uq_teaching_origin'),
                      Index('ix_teaching_profile_status', 'profile_id', 'status'))
    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: int = Field(index=True)
    surface: str = 'hub'
    section: str = 'overall'
    polarity: int = 1
    text: str = T()
    reason: str = T()
    signals: list = J()        # [{kind, surface, ref_type, ref_id, title, url, note, at}]
    status: str = 'pending'    # pending | merged | rejected | superseded
    user_authored: bool = False
    origin: str = ''           # hub_lesson | lesson | mood | music | chat | correction | user
    origin_id: str = ''
    origin_created_at: float = 0   # distinguishes a reused lesson id from the lesson this mirrors
    origin_updated_at: float = 0   # the lesson's last change this row reflects (sync skips unchanged lessons)
    board_id: Optional[int] = None
    correction: str = T()
    note: str = T()            # why the weave discarded or kept it
    merged_version_id: Optional[int] = None
    rejected_at: Optional[float] = None
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class ProfileKnowledge(SQLModel, table=True):
    """One immutable version of a profile's living knowledge. The newest row is current."""
    __table_args__ = (Index('ix_profileknowledge_profile_version', 'profile_id', 'version'),)
    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: int = Field(index=True)
    version: int = 1
    parent_id: Optional[int] = None
    cause: str = 'nightly'     # migration | nightly | chat | edit | undo
    sections: list = J()       # [{key, title, statements: [{sid, text, polarity, confidence, locked, teaching_ids, board_id, since, updated}]}]
    changes: dict = J({})      # {added: [...], changed: [{sid, before, after}], removed: [...], merged: [teaching ids]}
    text: str = T()
    created_at: float = Field(default_factory=time.time)


class KnowledgeChat(SQLModel, table=True):
    """The correction conversation beside a profile's knowledge."""
    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: int = Field(index=True)
    role: str = 'user'         # user | curator
    text: str = T()
    proposal: dict = J({})     # {ops: [{op: add|edit|remove, section, sid, text, polarity}]}
    proposal_status: str = 'none'  # none | pending | accepted | rejected | stale
    base_version_id: Optional[int] = None
    applied_version_id: Optional[int] = None
    created_at: float = Field(default_factory=time.time)
