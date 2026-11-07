"""Music tables. Tracks are shared (one row per YouTube video); everything else is per profile."""
import time
from typing import Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class MusicTrack(SQLModel, table=True):
    """An official music video as YouTube Music lists it (musicVideoType OMV)."""
    video_id: str = Field(primary_key=True)
    title: str = ''
    artist: str = ''
    artist_id: str = ''
    views: int = 0
    duration_s: int = 0
    thumbnail: str = ''
    first_seen: float = Field(default_factory=time.time)


class MusicFavorite(SQLModel, table=True):
    profile_id: int = Field(primary_key=True)
    kind: str = Field(primary_key=True)  # track | artist
    ref: str = Field(primary_key=True)   # video id | artist name (lower-cased)
    name: str = ''
    artist_id: str = ''
    thumbnail: str = ''
    created_at: float = Field(default_factory=time.time)


class MusicMix(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: int = Field(index=True)
    name: str = ''
    kind: str = 'seed'  # seed (built from one track) | curator (built from a description) | manual
    emoji: str = '🎵'
    seed_video_id: str = ''
    description: str = ''
    curator: str = ''   # curator persona key for curator mixes
    blurb: str = ''     # the curator's own line about the mix
    queries: list = Field(default_factory=list, sa_column=Column(JSON))
    status: str = 'ready'  # building | ready | error
    error: str = ''
    created_at: float = Field(default_factory=time.time)
    refreshed_at: float = 0
    last_played_at: float = 0
    build_started: float = 0  # a build older than service.BUILD_TIMEOUT_S counts as dead


class MusicMixTrack(SQLModel, table=True):
    mix_id: int = Field(primary_key=True)
    video_id: str = Field(primary_key=True)
    position: int = 0
    reason: str = ''


class MusicPlay(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: int = Field(index=True)
    video_id: str = Field(index=True)
    played_at: float = Field(default_factory=time.time)
    skipped: bool = False


class MusicLyrics(SQLModel, table=True):
    """Lyrics for one video, shared by every profile (lyrics do not depend on who listens)."""
    video_id: str = Field(primary_key=True)
    status: str = 'missing'  # found | missing | error
    source: str = ''         # lrclib
    source_id: int = 0
    synced: bool = False
    lines: list = Field(default_factory=list, sa_column=Column(JSON))  # [{t: ms, text, words?: [{t, text}]}]
    plain: str = ''
    duration: float = 0      # length of the recording the timing was made for (s)
    offset_ms: int = 0       # shift applied when playing: + means the words come later
    rate: float = 1.0        # video time = rate * lyric time + offset (a slower or faster edit of the song)
    offset_source: str = ''  # manual | auto
    fetched_at: float = 0
