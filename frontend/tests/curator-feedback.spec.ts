import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { join } from 'node:path';
import { freshPage } from './helpers';

const data = process.env.BT_E2E_DATA;
const backend = process.env.BT_E2E_BACKEND;
test.skip(!data || !backend, 'needs scripts/e2e.sh');

test('"Less like this" on a finding teaches its curator', async ({ page, request }) => {
	const curator = await (await request.post('/api/hub/curators', { data: { name: 'Gadget watcher', keywords: ['gadget'], source_ids: [], interval_hours: 0 } })).json();
	const id = execFileSync(join(backend!, '.venv/bin/python'), ['-c', `
import sys, time
from bt.db import session
from bt.hub.models import ContentItem, CuratorDiscovery
with session() as s:
    item = ContentItem(url='https://example.org/gadget-giveaway', title='Gadget giveaway contest', summary='Win a gadget', publisher='Example')
    s.add(item); s.flush()
    s.add(CuratorDiscovery(curator_id=int(sys.argv[1]), item_id=item.id, profile_id=1, found_at=time.time()))
    s.commit()
    print(item.id)
`, String(curator.id)], { cwd: backend, env: { ...process.env, BT_DATA_DIR: data! } }).toString().trim();
	await freshPage(page, `/item/${id}`);
	await page.getByRole('button', { name: 'Show Gadget watcher less like this' }).click();
	await expect(page.getByText('Learned: giveaway, contest. Similar findings will rank lower.')).toBeVisible();
	await expect(page.getByRole('button', { name: 'Show Gadget watcher more like this' })).toBeDisabled();
	const saved = await (await request.get(`/api/hub/curators/${curator.id}`)).json();
	expect(saved.tuned_down).toEqual(['giveaway', 'contest']);
});
