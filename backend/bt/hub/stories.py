"""Discovery Stories: engaging, factual summaries of real research sequences.

Every sentence is generated from persisted rows (runs, checks, discoveries, scout runs,
alerts, reports). Nothing is invented: differing facts keep their own sentences, and a
story only appears when something actually happened worth retelling.
"""
from sqlmodel import Session, select

from .models import (Alert, ContentItem, Curator, CuratorDiscovery, CuratorRun, CuratorSource,
                     ScoutRun, Source)


def _links(*entries) -> list[dict]:
    return [{'label': label, 'href': href} for label, href in entries]


def _curator_map(s: Session, pid: int, since: float, curator_id: int | None, kind: str | None = None):
    q = select(CuratorRun, Curator).join(Curator, Curator.id == CuratorRun.curator_id).where(
        CuratorRun.profile_id == pid, CuratorRun.finished_at != None,  # noqa: E711
        CuratorRun.started_at >= since)
    if kind:
        q = q.where(Curator.kind == kind)
    if curator_id is not None:
        q = q.where(CuratorRun.curator_id == curator_id)
    return s.exec(q.order_by(CuratorRun.started_at.desc()).limit(30)).all()


def _alert_story(s: Session, pid: int, since: float, curator_id: int | None) -> list[dict]:
    q = select(Alert).where(Alert.profile_id == pid, Alert.created_at >= since,
                            Alert.severity.in_(('high', 'critical')))
    if curator_id is not None:
        q = q.where(Alert.curator_id == curator_id)
    rows = s.exec(q.order_by(Alert.created_at.desc()).limit(5)).all()
    if not rows:
        return []
    newest = rows[0]
    return [{'kind': 'alerts', 'title': f'{len(rows)} high-severity alert{"s" if len(rows) != 1 else ""} came in.',
             'text': f'Newest: "{newest.title}" ({newest.severity}).',
             'links': _links(('View alerts', '/alerts')), 'at': newest.created_at}]


def _research_story(s: Session, pid: int, since: float, curator_id: int | None) -> list[dict]:
    q = select(Curator).where(Curator.profile_id == pid, Curator.kind == 'research', Curator.report_at >= since,
                              Curator.report != '')
    if curator_id is not None:
        q = q.where(Curator.id == curator_id)
    out = []
    for c in s.exec(q.order_by(Curator.report_at.desc()).limit(3)).all():
        out.append({'kind': 'research', 'title': f'A fresh cited research report on "{c.subject or c.name}".',
                    'text': f'"{c.name}" finished a report that cites its sources.',
                    'links': _links(('Open the report', f'/research/{c.id}')), 'at': c.report_at})
    return out


def _scout_story(s: Session, pid: int, since: float, curator_id: int | None) -> list[dict]:
    if curator_id is not None:
        return []
    runs = s.exec(select(ScoutRun).where(ScoutRun.profile_id == pid, ScoutRun.status == 'complete',
                                         ScoutRun.created_at >= since)
                  .order_by(ScoutRun.created_at.desc()).limit(10)).all()
    validated = sum(len(r.suggestions) for r in runs)
    if not validated:
        return []
    providers = sorted({x.get('provider') for r in runs for x in r.suggestions if x.get('provider')})
    sitemaps = sum(len(r.sitemaps) for r in runs)
    text = (f'Across {len(runs)} research run{"s" if len(runs) != 1 else ""} it validated '
            f'{validated} actionable source{"s" if validated != 1 else ""} ({", ".join(str(p) for p in providers)}).'
            if providers else
            f'Across {len(runs)} research run{"s" if len(runs) != 1 else ""} it validated '
            f'{validated} source{"s" if validated != 1 else ""}.')
    if sitemaps:
        text += f' It also recorded {sitemaps} sitemap lead{"s" if sitemaps != 1 else ""} for later checks.'
    return [{'kind': 'scout', 'title': f'Source Scout found {validated} new source{"s" if validated != 1 else ""} to follow.',
             'text': text + ' Nothing reaches your curators until you approve it on the Sources page.',
             'links': _links(('View sources', '/sources')),
             'at': max((r.created_at for r in runs), default=since)}]


def _run_stories(s: Session, pid: int, since: float, curator_id: int | None) -> list[dict]:
    out, told = [], set()
    for run, owner in _curator_map(s, pid, since, curator_id, kind='watch'):
        # One story per curator, from its newest productive run, so a busy curator cannot crowd out the rest.
        if not run.new_items or owner.id in told:
            continue
        told.add(owner.id)
        source_rows = s.exec(select(Source.id, Source.name)
                             .join(CuratorSource, CuratorSource.source_id == Source.id)
                             .where(CuratorSource.curator_id == owner.id)).all()
        findings = [x for x in s.exec(select(CuratorDiscovery.item_id).where(
            CuratorDiscovery.profile_id == pid, CuratorDiscovery.curator_id == owner.id,
            CuratorDiscovery.found_at >= run.started_at, CuratorDiscovery.found_at <= run.finished_at + 1)
            .order_by(CuratorDiscovery.found_at.desc()).limit(1)).all()]
        base = f'It kept {run.new_items} new find{"s" if run.new_items != 1 else ""}.'
        if source_rows:
            text = (f'It checked {run.seen_items} source item{"s" if run.seen_items != 1 else ""} across '
                    f'{len(source_rows)} source{"s" if len(source_rows) != 1 else ""} and kept {run.new_items} '
                    f'new find{"s" if run.new_items != 1 else ""}.')
        else:
            text = base
        shared = []
        if findings:
            shared = s.exec(select(CuratorDiscovery.curator_id).where(
                CuratorDiscovery.profile_id == pid, CuratorDiscovery.item_id == findings[0],
                CuratorDiscovery.curator_id != owner.id)).all()
        if shared:
            text += ' Another curator independently matched the same item, so the find is corroborated.'
        ai_fallback = any(e.get('source') == 'AI relevance' for e in run.errors)
        if ai_fallback:
            text += ' The AI judge was unavailable, so keyword scores decided.'
        elif len(run.errors) == 1:
            text += f' One source had an issue: {run.errors[0].get("message", "")[:120]}'
        elif run.errors:
            text += f' {len(run.errors)} sources had issues, first: {run.errors[0].get("message", "")[:120]}'
        links = _links(('Open curator', f'/curators/{owner.id}'))
        item = s.get(ContentItem, findings[0]) if findings else None
        if item:
            links += _links(('Show the finding', f'/item/{item.id}'))
        out.append({'kind': 'curator', 'title': f'"{owner.name}" brought home {run.new_items} '
                    f'find{"s" if run.new_items != 1 else ""}.', 'text': text, 'links': links,
                    'at': run.finished_at})
    return out


def _meme_story(s: Session, pid: int, since: float, curator_id: int | None) -> list[dict]:
    if curator_id is not None:
        return []
    from .models import MemeExample, MemeFormat, MemeTrend
    trends = s.exec(select(MemeTrend).where(MemeTrend.updated_at >= since,
                                            MemeTrend.state.in_(('emerging', 'trending')))
                    .order_by(MemeTrend.updated_at.desc()).limit(10)).all()
    out = []
    for t in trends:
        members = (list(s.exec(select(MemeExample.item_id).where(
            MemeExample.format_id == int(t.target_key))).all()) if t.target_kind == 'format' else
            list(s.exec(select(MemeExample.item_id).where(MemeExample.community == t.target_key)).all()))
        if not members:
            continue
        seen = s.exec(select(CuratorDiscovery.item_id).where(CuratorDiscovery.profile_id == pid,
                        CuratorDiscovery.item_id.in_(members))).first()
        if not seen:
            continue  # never tell a profile about memes it has not collected
        m = t.metrics or {}
        name = ''
        links = _links(('Open Meme Radar', '/memes'))
        if t.target_kind == 'format':
            f = s.get(MemeFormat, int(t.target_key))
            name = f.name if f else ''
            if f and f.exemplar_item_id:
                links += _links(('See the example', f'/item/{f.exemplar_item_id}'))
            what = f'the format "{name[:60]}"' if name else 'a recurring meme format'
        else:
            what = f'the community {t.target_key}'
        text = (f'It watched {what} gather {m.get("last7", 0)} new example'
                f'{"s" if m.get("last7", 0) != 1 else ""} in the last 7 days across '
                f'{m.get("communities", 0)} independent communit'
                f'{"y" if m.get("communities", 0) == 1 else "ies"}. The label "{t.state}" comes from those counted '
                'observations only.')
        if m.get('sparse'):
            text += f' The sample is small ({m.get("samples", 0)} examples), so treat it as early.'
        out.append({'kind': 'meme', 'title': f'A meme is {t.state.replace("_", " ")}: {what}.',
                    'text': text, 'links': links, 'at': t.updated_at})
    return out[:1]


def generate(s: Session, pid: int, since: float, curator_id: int | None = None) -> list[dict]:
    """Real notable sequences from persisted data, newest first, bounded to five."""
    all_stories = [*_alert_story(s, pid, since, curator_id), *_research_story(s, pid, since, curator_id),
                   *_scout_story(s, pid, since, curator_id), *_run_stories(s, pid, since, curator_id),
                   *_meme_story(s, pid, since, curator_id)]
    return sorted(all_stories, key=lambda x: x.get('at') or since, reverse=True)[:5]
