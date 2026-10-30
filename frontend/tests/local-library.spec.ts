import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { join } from 'node:path';
import { freshPage } from './helpers';

const data = process.env.BT_E2E_DATA;
const backend = process.env.BT_E2E_BACKEND;
test.skip(!data || !backend, 'needs scripts/e2e.sh');

test('saved article displays its summary and local text without fetching its source', async ({ page, request }) => {
	const curator = await (await request.post('/api/hub/curators', { data: { name: 'Local test', source_ids: [], interval_hours: 0 } })).json();
	const id = execFileSync(join(backend!, '.venv/bin/python'), ['-c', `
import sys
from bt.db import session
from bt.hub.models import ContentItem, CuratorDiscovery, CaptureJob
with session() as s:
    item=ContentItem(url='https://example.org/should-not-be-requested', title='Local article', summary='Source description', body='First paragraph.\\n\\nSecond paragraph.', enrichment={'summary': 'The main idea in brief.'})
    s.add(item); s.flush()
    s.add(CuratorDiscovery(curator_id=int(sys.argv[1]), item_id=item.id, profile_id=1, found_at=1))
    s.add(CaptureJob(profile_id=1, item_id=item.id, status='complete', summary_status='ready'))
    s.commit(); print(item.id)
`, String(curator.id)], { cwd: backend, env: { ...process.env, BT_DATA_DIR: data! } }).toString().trim();
	const outgoing: string[] = [];
	page.on('request', (req) => { if (req.url().startsWith('https://example.org')) outgoing.push(req.url()); });
	await freshPage(page, `/item/${id}`);
	await expect(page.getByText('The main idea in brief.')).toBeVisible();
	await expect(page.getByText(/First paragraph/)).toBeVisible();
	await expect(page.getByText('Local capture: complete · Summary: ready')).toBeVisible();
	await expect(page.getByRole('link', { name: /Open original/ })).toHaveAttribute('href', 'https://example.org/should-not-be-requested');
	expect(outgoing).toEqual([]);
});
