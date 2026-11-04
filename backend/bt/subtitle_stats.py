"""Read-only subtitle coverage and worker monitoring, scoped to authorized profiles."""
import time
import re
from sqlmodel import select
from sqlalchemy.orm import load_only
from . import profile_pins, subtitle_jobs
from .access import has_children
from .models import Candidate, Category, Download, GeneratedSubtitles, Profile, Video, WatchHistory
from .downloader.service import abs_path


def snapshot(s, pid, heartbeat_path=None, clock=None):
    stamp = time.time() if clock is None else clock
    admin = s.get(Profile, pid).role == 'admin'
    children = has_children(s)
    allowed = {p.id for p in s.exec(select(Profile)).all()
               if (admin or p.id == pid) and (not children or p.role == 'child' or profile_pins.record(s, p.id))}
    owned = set(s.exec(select(Candidate.video_id).join(Category, Category.id == Candidate.category_id)
                       .where(Category.profile_id.in_(allowed))).all())
    jobs = {r.video_id: r for r in s.exec(select(GeneratedSubtitles).options(load_only(
                GeneratedSubtitles.video_id, GeneratedSubtitles.status, GeneratedSubtitles.vtt,
                GeneratedSubtitles.progress, GeneratedSubtitles.retry_at, GeneratedSubtitles.updated_at,
                GeneratedSubtitles.error, GeneratedSubtitles.requested_by))).all()
            if admin or r.video_id in owned or r.requested_by == pid}
    finished = set(s.exec(select(WatchHistory.profile_id, WatchHistory.video_id)
                         .where(WatchHistory.finished == True)).all())
    rows = s.exec(select(Video, Download, Category.profile_id)
        .join(Candidate, Candidate.video_id == Video.id).join(Category, Category.id == Candidate.category_id)
        .join(Download, Download.video_id == Video.id)
        .where(Category.profile_id.in_(allowed), Category.kind == 'shorts', Category.enabled == True,
               Candidate.status == 'accepted', Candidate.dismissed == False, Video.is_short == True,
               Video.duration_s > 0, Video.duration_s <= 180, Download.status == 'done')
        .options(load_only(Video.id, Video.title), load_only(Download.video_id, Download.path))).all()
    eligible = {}
    for video, download, owner in rows:
        if (owner, video.id) in finished or video.id in eligible or not subtitle_jobs.VID_RE.fullmatch(video.id):
            continue
        try:
            path = abs_path(download.path) if download.path else None
            if not path or not path.is_file() or not path.stat().st_size:
                continue
            with path.open('rb') as source:
                if not source.read(1): continue
        except OSError:
            continue
        eligible[video.id] = video
    def state(row):
        if not row: return 'missing'
        if row.status == 'ready': return 'ready' if subtitle_jobs.usable_vtt(row.vtt) else 'failed'
        if row.status == 'queued': return 'retry_waiting' if row.retry_at > stamp else 'pending'
        return row.status if row.status in ('running', 'failed') else 'failed'
    counts = dict.fromkeys(('ready', 'pending', 'running', 'failed', 'missing', 'retry_waiting'), 0)
    for vid in eligible: counts[state(jobs.get(vid))] += 1
    def safe_error(row):
        if row.status == 'ready': return 'Invalid saved subtitle track'
        value = row.error.lower()
        if 'no speech' in value: return 'No speech detected in this clip.'
        if 'timeout' in value or 'timed out' in value: return 'The STT service timed out.'
        if 'connection' in value or 'network' in value: return 'Could not reach the STT service.'
        if 'audio' in value and ('unreadable' in value or 'decode' in value): return 'Could not read the clip audio.'
        match = re.search(r'http(?: status)?[ :]+([45]\d{2})\b', value)
        if match: return f'The STT service returned HTTP {match.group(1)}.'
        return 'Subtitle generation failed. Open the video to retry.'
    def detail(row):
        video = s.get(Video, row.video_id, options=[load_only(Video.id, Video.title)])
        return {'video_id': row.video_id, 'title': video.title if video else row.video_id,
                'progress': round(max(0, min(100, row.progress)), 1), 'retry_at': row.retry_at,
                'updated_at': row.updated_at, 'error': safe_error(row)}
    active = sorted((r for r in jobs.values() if r.status == 'running'), key=lambda r: r.updated_at, reverse=True)
    failures = sorted((r for r in jobs.values() if state(r) == 'failed'), key=lambda r: r.updated_at, reverse=True)
    other = dict.fromkeys(('pending', 'running', 'failed', 'retry_waiting'), 0)
    for vid, row in jobs.items():
        value = state(row)
        if vid not in eligible and value in other: other[value] += 1
    path = heartbeat_path or subtitle_jobs.DATA_DIR / subtitle_jobs.HEARTBEAT_NAME
    try:
        age = stamp - path.stat().st_mtime
        age = round(age, 1) if age >= 0 else None
    except OSError:
        age = None
    return {'scope': 'household' if admin else 'profile', 'eligible': len(eligible), 'coverage': counts,
            'other_jobs': other, 'active': [detail(r) for r in active[:5]],
            'recent_failures': [detail(r) for r in failures[:5]],
            'service': {'heartbeat_age_s': age, 'heartbeat_fresh': age is not None and age <= 90,
                        'stt_enabled': subtitle_jobs.enabled()}}
