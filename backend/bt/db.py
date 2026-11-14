"""Engine, sessions, settings helpers, agent state/control (shared across processes)."""
import copy
import json
import secrets
from contextlib import contextmanager
from datetime import timedelta

from sqlalchemy import event, inspect
from sqlmodel import Session, SQLModel, create_engine, select

from .config import DB_PATH, DEFAULT_SETTINGS
from .models import AgentEvent, PlaybackTarget, Profile, Setting, YtRequest, now

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False, "timeout": 30})


def _migrate_shared_learning_columns(connection) -> None:
    """Add content scoping without rebuilding or rewriting legacy video records."""
    for table, columns in (
        ("feedback", (("content_type", "VARCHAR NOT NULL DEFAULT 'video'"), ("item_id", "INTEGER"))),
        ("lesson", (("content_type", "VARCHAR NOT NULL DEFAULT 'video'"),)),
        ("memorycore", (("content_type", "VARCHAR NOT NULL DEFAULT 'video'"),)),
    ):
        have = {c["name"] for c in inspect(connection).get_columns(table)}
        for name, definition in columns:
            if name not in have:
                connection.exec_driver_sql(f'ALTER TABLE {table} ADD COLUMN "{name}" {definition}')
    connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_feedback_profile_type_processed ON feedback (profile_id, content_type, processed)")
    connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_lesson_profile_type_active ON lesson (profile_id, content_type, active)")
    connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_memorycore_profile_type_id ON memorycore (profile_id, content_type, id)")


@event.listens_for(engine, "connect")
def _pragmas(conn, _):
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=30000")
    cur.close()


def init_db() -> None:
    # All three services start together. Serialize schema creation and first-run
    # defaults so they cannot create duplicate profiles or settings.
    with engine.begin() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        from . import curation  # noqa: F401  (registers v1 + hub tables)
        from .hub import migrate, models as _hub_models  # noqa: F401
        from .music import models as _music_models  # noqa: F401
        SQLModel.metadata.create_all(connection)
        if 'target_images' not in {c['name'] for c in inspect(connection).get_columns('moodboard')}:
            connection.exec_driver_sql("ALTER TABLE moodboard ADD COLUMN target_images INTEGER NOT NULL DEFAULT 100")
        # Additive shared-learning type columns. Existing rows are videos by default;
        # keep the legacy video_id column unchanged for old feedback and callers.
        _migrate_shared_learning_columns(connection)
        if 'kind' not in {c['name'] for c in inspect(connection).get_columns('category')}:
            connection.exec_driver_sql("ALTER TABLE category ADD COLUMN kind VARCHAR NOT NULL DEFAULT 'playlist'")
        if 'rate' not in {c['name'] for c in inspect(connection).get_columns('musiclyrics')}:
            connection.exec_driver_sql("ALTER TABLE musiclyrics ADD COLUMN rate FLOAT NOT NULL DEFAULT 1.0")
        have_mix = {c['name'] for c in inspect(connection).get_columns('musicmix')}
        for col in ('build_started', 'last_played_at'):  # Music columns added after the first release
            if col not in have_mix:
                connection.exec_driver_sql(f"ALTER TABLE musicmix ADD COLUMN {col} FLOAT NOT NULL DEFAULT 0")
        if 'shorts_verified' not in {c['name'] for c in inspect(connection).get_columns('video')}:
            connection.exec_driver_sql("ALTER TABLE video ADD COLUMN shorts_verified BOOLEAN NOT NULL DEFAULT 0")
        if 'local_only' not in {c['name'] for c in inspect(connection).get_columns('generatedsubtitles')}:
            connection.exec_driver_sql("ALTER TABLE generatedsubtitles ADD COLUMN local_only BOOLEAN NOT NULL DEFAULT 0")
        have_subtitles = {c['name'] for c in inspect(connection).get_columns('generatedsubtitles')}
        for name, definition in (('retry_count', 'INTEGER NOT NULL DEFAULT 0'),
                                 ('retry_at', 'FLOAT NOT NULL DEFAULT 0')):
            if name not in have_subtitles:
                connection.exec_driver_sql(f'ALTER TABLE generatedsubtitles ADD COLUMN {name} {definition}')
        # Additive household-access migration, before querying the updated Profile model.
        # Existing households retain one administrator: their first profile.
        if 'role' not in {c['name'] for c in inspect(connection).get_columns('profile')}:
            connection.exec_driver_sql("ALTER TABLE profile ADD COLUMN role VARCHAR NOT NULL DEFAULT 'adult'")
            connection.exec_driver_sql("UPDATE profile SET role = 'admin' WHERE id = (SELECT MIN(id) FROM profile)")
        # Additive playback pairing columns; existing screens get a pairing code so their
        # receivers can rediscover it on the next heartbeat (notification via the response).
        have = {c['name'] for c in inspect(connection).get_columns('playbacktarget')}
        if have:
            for name in ('pair', 'rate'):
                if name not in have:
                    connection.exec_driver_sql(f"ALTER TABLE playbacktarget ADD COLUMN \"{name}\" JSON NULL")
            with Session(connection) as s:
                coded = False
                for t in s.exec(select(PlaybackTarget).where(PlaybackTarget.pair == None)).all():  # noqa: E711
                    t.pair = {"code": str(secrets.randbelow(900000) + 100000), "profiles": []}
                    s.add(t)
                    coded = True
                if coded:
                    s.commit()
        with Session(connection) as s:
            fresh = not s.exec(select(Profile)).first()
            if not s.get(Setting, "watchcraft_schema_version"):
                s.add(Setting(key="watchcraft_schema_version", value={"version": migrate.VERSION if fresh else 1}))
                s.commit()
        migrate.migrate(connection)
        with Session(connection) as s:
            if not s.exec(select(Profile)).first():
                s.add(Profile(name="Family", emoji="🏠", color="#7c5cff", role="admin"))
                s.merge(Setting(key="setup", value={"completed": False}))
                s.merge(Setting(key="agent", value={"enabled": False}))
                s.commit()


@contextmanager
def session():
    with Session(engine, expire_on_commit=False) as s:
        yield s


def get_session():
    """FastAPI dependency."""
    with Session(engine, expire_on_commit=False) as s:
        yield s


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def get_settings(s: Session | None = None) -> dict:
    """All global settings merged over defaults."""
    def _load(sess):
        # Only the known keys: the table also caches thousands of per-video rows.
        rows = {r.key: r.value for r in sess.exec(select(Setting).where(Setting.key.in_(list(DEFAULT_SETTINGS)))).all()}
        return {k: _deep_merge(v, rows.get(k, {})) if isinstance(v, dict) else rows.get(k, v)
                for k, v in DEFAULT_SETTINGS.items()}
    if s is not None:
        return _load(s)
    with session() as sess:
        return _load(sess)


def save_setting(key: str, value: dict, s: Session | None = None) -> None:
    def _save(sess):
        row = sess.get(Setting, key) or Setting(key=key)
        row.value = value
        sess.add(row)
        sess.commit()
    if s is not None:
        return _save(s)
    with session() as sess:
        _save(sess)


def get_kv(key: str, default=None, s: Session | None = None) -> dict:
    """Raw (non-default-merged) kv, used for agent_state / agent_control."""
    def _load(sess):
        row = sess.get(Setting, key)
        return row.value if row else (default if default is not None else {})
    if s is not None:
        return _load(s)
    with session() as sess:
        return _load(sess)


def drop_kv(key: str) -> None:
    with session() as s:
        row = s.get(Setting, key)
        if row:
            s.delete(row)
            s.commit()


def drop_kv_prefix(prefix: str) -> int:
    """Delete every kv row whose key starts with `prefix`. -> number of rows deleted."""
    with session() as s:
        rows = s.exec(select(Setting).where(Setting.key.startswith(prefix))).all()
        for r in rows:
            s.delete(r)
        s.commit()
        return len(rows)


# --- Agent state (written by worker, read by api) & control (written by api, read by worker)

def set_agent_state(**state) -> None:
    state["updated_at"] = now().isoformat()
    save_setting("agent_state", state)


def get_agent_state(s: Session | None = None) -> dict:
    return get_kv("agent_state", {"state": "offline"}, s)


def get_agent_control(s: Session | None = None) -> dict:
    return get_kv("agent_control", {"paused": False, "run_now": None, "playing_until": None}, s)


def update_agent_control(**changes) -> dict:
    """Atomic read-modify-write (api + worker both write control; a lost `paused` would be bad)."""
    conn = engine.raw_connection()
    try:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        row = cur.execute("SELECT value FROM setting WHERE key = 'agent_control'").fetchone()
        ctl = {"paused": False, "run_now": None, "playing_until": None}
        if row and row[0]:
            ctl.update(json.loads(row[0]))
        ctl.update(changes)
        cur.execute("INSERT INTO setting (key, value) VALUES ('agent_control', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (json.dumps(ctl),))
        conn.commit()
        return ctl
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def emit(kind: str, message: str, level: str = "info", **kw) -> AgentEvent:
    """Append an AgentEvent row (streamed to UI via SSE)."""
    payload = kw.pop("payload", {}) or {}
    ev = AgentEvent(kind=kind, message=message, level=level, payload=payload, **kw)
    with session() as s:
        s.add(ev)
        s.commit()
        s.refresh(ev)
    return ev


def log_yt_request(kind: str, origin: str = "worker", ok: bool = True, detail: str = "") -> None:
    with session() as s:
        s.add(YtRequest(kind=kind, origin=origin, ok=ok, detail=detail[:500]))
        s.commit()


def yt_request_counts(s: Session | None = None) -> dict:
    def _count(sess):
        t = now()
        hour = sess.exec(select(YtRequest).where(YtRequest.origin == "worker", YtRequest.ts >= t - timedelta(hours=1))).all()
        day = sess.exec(select(YtRequest).where(YtRequest.origin == "worker", YtRequest.ts >= t - timedelta(days=1))).all()
        return {"hour": len(hour), "day": len(day)}
    if s is not None:
        return _count(s)
    with session() as sess:
        return _count(sess)
