"""Alert delivery to a webhook, Home Assistant and email.

Channels are administrator configuration and often live on the LAN, so they are
requested directly — no redirects, bounded response — rather than through the
public-content fetcher, which refuses private addresses. Delivery is best effort:
an alert always stays in the app, and a failing channel never fails a curator run.
"""
import smtplib
import time
from email.message import EmailMessage

import httpx
from sqlmodel import select

from ..db import get_kv, get_settings, save_setting, session
from .models import Alert, ContentItem

SEVERITIES = ['info', 'low', 'medium', 'high', 'critical']
CURSOR_KEY = 'watchcraft_notify_cursor'
STATUS_KEY = 'watchcraft_notify_status'
BATCH = 20          # alerts delivered per scheduler tick
THROTTLE = 60.0     # seconds between delivery passes
TIMEOUT = 15


def channels(cfg: dict | None = None) -> dict:
    """Which channels are configured. Never returns a secret."""
    cfg = cfg if cfg is not None else get_settings().get('notify', {})
    return {'webhook': bool(cfg.get('webhook_url')),
            'home_assistant': bool(cfg.get('ha_webhook_url')),
            'email': bool(cfg.get('email_host') and cfg.get('email_to'))}


def _payload(alert: Alert, item: ContentItem | None) -> dict:
    return {'event': 'watchcraft.alert', 'id': alert.id, 'severity': alert.severity,
            'title': alert.title, 'reason': alert.reason, 'url': item.url if item else '',
            'publisher': item.publisher if item else '', 'curator_id': alert.curator_id,
            'created_at': alert.created_at}


def _post(url: str, payload: dict) -> str:
    r = httpx.post(url, json=payload, timeout=TIMEOUT, follow_redirects=False,
                   headers={'User-Agent': 'WatchCraft/0.2'})
    r.raise_for_status()
    return f'HTTP {r.status_code}'


def _mail(cfg: dict, payload: dict) -> str:
    msg = EmailMessage()
    msg['Subject'] = f"[WatchCraft] {payload['severity']}: {payload['title']}"[:200]
    msg['From'] = cfg.get('email_user') or cfg['email_to']
    msg['To'] = cfg['email_to']
    body = payload['reason'] or payload['title']
    if payload['url']:
        body += f"\n\n{payload['url']}"
    msg.set_content(body)
    with smtplib.SMTP(cfg['email_host'], int(cfg.get('email_port') or 587), timeout=TIMEOUT + 5) as smtp:
        if cfg.get('email_starttls', True):
            smtp.starttls()
        if cfg.get('email_user'):
            smtp.login(cfg['email_user'], cfg.get('email_password') or '')
        smtp.send_message(msg)
    return 'sent'


def deliver(alert: Alert, item: ContentItem | None, cfg: dict) -> dict:
    """Send one alert to every configured channel. Returns a short result per channel."""
    payload = _payload(alert, item)
    out = {}
    for name, key in (('webhook', 'webhook_url'), ('home_assistant', 'ha_webhook_url')):
        if not cfg.get(key):
            continue
        try:
            out[name] = _post(cfg[key], payload)
        except Exception as e:  # noqa: BLE001 — one channel never blocks the others
            out[name] = f'failed: {type(e).__name__}'
    if cfg.get('email_host') and cfg.get('email_to'):
        try:
            out['email'] = _mail(cfg, payload)
        except Exception as e:  # noqa: BLE001
            out['email'] = f'failed: {type(e).__name__}'
    return out


def dispatch(throttled=True) -> dict:
    """Deliver alerts created since the last pass. Called from the scheduler tick."""
    cfg = get_settings().get('notify', {})
    if not any(channels(cfg).values()):
        return {'skipped': 'no channels configured'}
    now = time.time()
    state = get_kv(STATUS_KEY, {})
    if throttled and now - float(state.get('at') or 0) < THROTTLE:
        return {'skipped': True}
    cursor = int(get_kv(CURSOR_KEY, {}).get('id') or 0)
    floor = SEVERITIES.index(cfg['min_severity']) if cfg.get('min_severity') in SEVERITIES else 3
    sent, results = 0, {}
    with session() as s:
        for alert in s.exec(select(Alert).where(Alert.id > cursor).order_by(Alert.id).limit(BATCH)).all():
            cursor = alert.id
            rank = SEVERITIES.index(alert.severity) if alert.severity in SEVERITIES else 0
            if rank < floor:
                continue
            results.update(deliver(alert, s.get(ContentItem, alert.item_id), cfg))
            sent += 1
    save_setting(CURSOR_KEY, {'id': cursor})
    save_setting(STATUS_KEY, {'at': now, 'sent': sent, 'results': results})
    return {'sent': sent, 'results': results}


def test(cfg: dict | None = None) -> dict:
    """Send one synthetic alert so an administrator can check a channel without waiting."""
    cfg = cfg if cfg is not None else get_settings().get('notify', {})
    if not any(channels(cfg).values()):
        raise ValueError('Configure a webhook, Home Assistant webhook or email address first.')
    alert = Alert(id=0, profile_id=0, item_id=0, severity='info', title='WatchCraft test notification',
                  reason='This is a test. Real alerts follow the same path.')
    return deliver(alert, None, cfg)
