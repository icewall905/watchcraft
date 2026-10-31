import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { join } from 'node:path';
import { freshPage } from './helpers';

const data = process.env.BT_E2E_DATA;
const backend = process.env.BT_E2E_BACKEND;
test.skip(!data || !backend, 'needs scripts/e2e.sh');

test('Scout Report produces source-linked history and an opt-in home preview', async ({ page, request }) => {
	const curator = await (await request.post('/api/hub/curators', { data: { name: 'Report researcher', source_ids: [], interval_hours: 0 } })).json();
	const watcher = await (await request.post('/api/hub/curators', { data: { name: 'Report watcher', source_ids: [], interval_hours: 0 } })).json();
	execFileSync(join(backend!, '.venv/bin/python'), ['-c', `
import sys, time
from bt.db import session
from bt.hub.models import ContentItem, CuratorDiscovery, CuratorRun, Alert
cid, wid = int(sys.argv[1]), int(sys.argv[2])
with session() as s:
    item = ContentItem(url='https://example.org/report-evidence', title='A verifiable discovery', summary='A source-provided summary', publisher='Example')
    s.add(item); s.flush()
    s.add(CuratorDiscovery(curator_id=cid, item_id=item.id, profile_id=1, found_at=time.time()))
    s.add(CuratorRun(curator_id=cid, profile_id=1, status='ok', new_items=1, seen_items=3, finished_at=time.time()))
    s.add(Alert(curator_id=wid, profile_id=1, item_id=item.id, title='Widget CVE-2026-9001 published',
                severity='critical', reason='A new advisory touched your keywords.'))
    s.commit()
`, String(curator.id), String(watcher.id)], { cwd: backend, env: { ...process.env, BT_DATA_DIR: data! } });
	await freshPage(page, '/scout-report');
	await page.getByRole('button', { name: 'Generate report' }).click();
	await expect(page.getByRole('link', { name: 'A verifiable discovery' })).toBeVisible();
	await expect(page.getByRole('link', { name: 'Original source ↗' })).toHaveAttribute('href', 'https://example.org/report-evidence');
	// Discovery stories render with fact links; nothing invented, only persisted rows are retold.
	await expect(page.getByText('1 high-severity alert came in.')).toBeVisible();
	await expect(page.getByText('Newest: "Widget CVE-2026-9001 published" (critical).', { exact: false })).toBeVisible();
	await expect(page.getByRole('link', { name: 'View alerts →' })).toBeVisible();
	await expect(page.getByText('"Report researcher" brought home 1 find.')).toBeVisible();
	await page.getByRole('button', { name: 'Generate report' }).click();
	await expect(page.getByText('No new unreported discoveries in this period.')).toBeVisible();
	await page.getByText('Report schedule and home preview').click();
	await page.getByRole('switch', { name: /Show a Scout Report preview/ }).click();
	await page.getByRole('button', { name: /Automatic reports/ }).click();
	await page.getByRole('option', { name: 'Weekly' }).click();
	await page.getByRole('button', { name: 'Save report settings' }).click();
	await expect.poll(async () => (await (await request.get('/api/hub/scout-report/config')).json()).frequency).toBe('weekly');
	await page.goto('/');
	await expect(page.getByRole('region', { name: 'Scout Report preview' })).toBeVisible();
	await expect(page.getByRole('link', { name: 'Open Scout Report →' })).toHaveAttribute('href', '/scout-report');
});
