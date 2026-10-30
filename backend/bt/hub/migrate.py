"""Versioned, additive migrations for WatchCraft Hub. Runs inside init_db's BEGIN IMMEDIATE.

v1 → v2: add curator columns, create Source per v1 curator, move v1 discoveries into
canonical ContentItem + CuratorDiscovery + ItemState. The v1 discovery table is kept."""
import json
import time

from sqlalchemy import inspect
from sqlmodel import Session, select

from ..models import Setting
from .models import Curator

VERSION = 2


def _add_missing_columns(conn) -> None:
    have = {c['name'] for c in inspect(conn).get_columns('curator')}
    for col in Curator.__table__.columns:
        if col.name in have:
            continue
        default = col.default.arg if col.default is not None and not callable(col.default.arg) else None
        sqltype = col.type.compile(conn.dialect)
        if default is None and col.name in {'queries', 'keywords', 'exclusions', 'content_types', 'alert_keywords', 'aliases', 'report_refs'}:
            default = '[]'
        literal = 'NULL' if default is None else repr(int(default)) if isinstance(default, bool) else repr(default) if isinstance(default, (int, float)) else "'" + str(default).replace("'", "''") + "'"
        conn.exec_driver_sql(f'ALTER TABLE curator ADD COLUMN "{col.name}" {sqltype} DEFAULT {literal}')
    if 'created_at' not in have:
        conn.exec_driver_sql('UPDATE curator SET created_at = ? WHERE created_at IS NULL', (time.time(),))


def migrate(conn) -> None:
    from .engine import canonical_url, ensure_fts
    _add_missing_columns(conn)
    ensure_fts(conn)
    with Session(conn) as s:
        row = s.get(Setting, 'watchcraft_schema_version')
        version = (row.value or {}).get('version', 1) if row else 1
        if version >= VERSION:
            return
        _v2(conn, canonical_url)
        s.merge(Setting(key='watchcraft_schema_version', value={'version': VERSION, 'migrated_at': time.time()}))
        s.commit()


def _v2(conn, canonical_url) -> None:
    from .engine import index
    from .models import ContentItem, CuratorDiscovery, CuratorSource, ItemState, Source
    from ..curation import Discovery
    provider_map = {'rss': 'rss', 'github': 'github_releases'}
    with Session(conn) as s:
        for c in s.exec(select(Curator)).all():
            if not c.source:
                continue
            prov = provider_map.get(c.provider, c.provider)
            src = s.exec(select(Source).where(Source.profile_id == c.profile_id, Source.provider == prov, Source.url == c.source)).first()
            if not src:
                src = Source(profile_id=c.profile_id, provider=prov, url=c.source, name=c.name, last_success=c.last_success)
                s.add(src); s.flush()
            if not s.get(CuratorSource, (c.id, src.id)):
                s.add(CuratorSource(curator_id=c.id, source_id=src.id))
        for d in s.exec(select(Discovery)).all():
            try:
                url = canonical_url(d.url)
            except ValueError:
                continue
            item = s.exec(select(ContentItem).where(ContentItem.url == url)).first()
            if not item:
                item = ContentItem(url=url, title=d.title, type='project' if d.provider == 'github' else 'news',
                                   provider=provider_map.get(d.provider, d.provider), first_seen=d.discovered_at)
                s.add(item); s.flush(); index(s, item)
            if not s.exec(select(CuratorDiscovery).where(CuratorDiscovery.curator_id == d.curator_id, CuratorDiscovery.item_id == item.id)).first():
                s.add(CuratorDiscovery(curator_id=d.curator_id, item_id=item.id, profile_id=d.profile_id, found_at=d.discovered_at, score=None))
            st = s.get(ItemState, (d.profile_id, item.id)) or ItemState(profile_id=d.profile_id, item_id=item.id)
            if d.saved:
                st.saved, st.saved_at = True, st.saved_at or d.discovered_at
            s.add(st)
        s.commit()


__all__ = ['migrate', 'VERSION', 'json']
