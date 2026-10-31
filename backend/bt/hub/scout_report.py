"""Deterministic, profile-owned report of actual research activity and evidence."""
import time

from fastapi import HTTPException
from sqlalchemy import text
from sqlmodel import select

from ..access import hub_allowed
from ..db import session
from . import stories
from .models import (Alert, ContentItem, Curator, CuratorDiscovery, CuratorRun, ScoutPlan, ScoutReportIssue, ScoutRun)


def generate(pid: int, period: str, curator_id: int | None = None):
    now = time.time()
    since = now - (86400 if period == 'daily' else 7 * 86400)
    # Serialize duplicate checks and issue insertion across API/scheduler processes.
    with session() as s:
        s.exec(text('BEGIN IMMEDIATE'))
        if not hub_allowed(s, pid):
            raise HTTPException(403, 'Hub discovery and research are unavailable for this profile.')
        curator = s.get(Curator, curator_id) if curator_id is not None else None
        if curator_id is not None and (not curator or curator.profile_id != pid):
            raise HTTPException(404, 'Curator not found')
        old = s.exec(select(ScoutReportIssue).where(ScoutReportIssue.profile_id == pid,
                      ScoutReportIssue.curator_id == curator_id).order_by(ScoutReportIssue.created_at.desc()).limit(100)).all()
        already = {iid for issue in old for iid in issue.item_ids}
        q = (select(CuratorDiscovery, ContentItem, Curator)
             .join(ContentItem, ContentItem.id == CuratorDiscovery.item_id)
             .join(Curator, Curator.id == CuratorDiscovery.curator_id)
             .where(CuratorDiscovery.profile_id == pid, Curator.profile_id == pid, CuratorDiscovery.found_at >= since))
        if curator_id is not None: q = q.where(Curator.id == curator_id)
        rows = s.exec(q.order_by(CuratorDiscovery.found_at.desc()).limit(500)).all()
        findings, seen = [], set()
        for discovery, item, owner in rows:
            if item.id in already or item.id in seen: continue
            seen.add(item.id)
            findings.append({'id': item.id, 'title': item.title[:500], 'summary': (item.enrichment.get('summary') or item.summary or '')[:500],
                             'summary_ai': bool(item.enrichment.get('summary')), 'url': item.url, 'publisher': item.publisher, 'curator_id': owner.id,
                             'curator_name': owner.name, 'found_at': discovery.found_at})
            if len(findings) >= 30: break
        run_query = select(CuratorRun).where(CuratorRun.profile_id == pid, CuratorRun.started_at >= since)
        if curator_id is not None: run_query = run_query.where(CuratorRun.curator_id == curator_id)
        runs = s.exec(run_query.order_by(CuratorRun.started_at.desc()).limit(100)).all()
        scouts = [] if curator_id is not None else s.exec(select(ScoutRun).where(
            ScoutRun.profile_id == pid, ScoutRun.created_at >= since, ScoutRun.status == 'complete')
            .order_by(ScoutRun.created_at.desc()).limit(20)).all()
        alert_query = select(Alert).where(Alert.profile_id == pid, Alert.state == 'new')
        if curator_id is not None: alert_query = alert_query.where(Alert.curator_id == curator_id)
        alerts = s.exec(alert_query.order_by(Alert.created_at.desc()).limit(5)).all()
        upcoming_query = select(Curator).where(Curator.profile_id == pid, Curator.enabled == True,
                                               Curator.next_run > now, Curator.next_run < 1e11)
        if curator_id is not None: upcoming_query = upcoming_query.where(Curator.id == curator_id)
        upcoming = s.exec(upcoming_query.order_by(Curator.next_run).limit(5)).all()
        scheduled_scouts = [] if curator_id is not None else s.exec(select(ScoutPlan).where(ScoutPlan.profile_id == pid,
            ScoutPlan.enabled == True, ScoutPlan.next_run > now).order_by(ScoutPlan.next_run).limit(5)).all()
        next_work = [{'id': c.id, 'name': c.name, 'next_run': c.next_run, 'href': f'/curators/{c.id}'} for c in upcoming]
        next_work += [{'id': p.id, 'name': f'Source Scout: {p.query[:120]}', 'next_run': p.next_run, 'href': '/sources'} for p in scheduled_scouts]
        next_work = sorted(next_work, key=lambda entry: entry['next_run'])[:5]
        sections = {'since': since, 'curator_name': curator.name if curator else '', 'findings': findings,
                    'runs': {'count': len(runs), 'new_items': sum(r.new_items for r in runs),
                             'failed': sum(r.status in ('failed', 'partial') for r in runs)},
                    'scout': {'runs': len(scouts), 'validated_sources': sum(len(r.suggestions) for r in scouts)},
                    'alerts': [{'id': a.id, 'title': a.title, 'severity': a.severity} for a in alerts],
                    'upcoming': next_work,
                    'stories': stories.generate(s, pid, since, curator_id)}
        issue = ScoutReportIssue(profile_id=pid, curator_id=curator_id, period=period,
                                 item_ids=[item['id'] for item in findings], sections=sections)
        s.add(issue); s.commit(); s.refresh(issue)
        return issue.model_dump()



def scheduled(pid: int, frequency: str, lease: float):
    from .models import ScoutReportConfig
    from sqlalchemy import update
    try:
        generate(pid, frequency)
    except Exception:
        # Keep the short lease so a failed scheduled run retries instead of silently skipping a period.
        return
    with session() as s:
        s.exec(update(ScoutReportConfig).where(ScoutReportConfig.profile_id == pid,
               ScoutReportConfig.frequency == frequency, ScoutReportConfig.next_run == lease)
               .values(next_run=time.time() + (86400 if frequency == 'daily' else 604800)))
        s.commit()
