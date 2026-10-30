import { test, expect } from '@playwright/test';
import { freshPage } from './helpers';

test('Agent Activity shows real agent state and the parallel-agent setting saves', async ({ page, request }) => {
	const now = Date.now() / 1000;
	const team = {
		limit: 2, running: 1, waiting: 1,
		agents: [
			{ key: 'curator-1', kind: 'curator', id: 1, name: 'Local AI', icon: '🤖', color: '#ffb547', mission: 'Tracks local model releases.', role: 'watch',
				status: 'running', since: now - 42, next_run: null, last_run: now - 3600, found_today: 2, error: '', enabled: true,
				doing: 'Checking a real feed.', timeline: [{ step: 'checking', message: 'Checking a real feed.', at: now - 2 },
					{ step: 'source_error', message: 'One source needs attention.', at: now - 1, detail: 'Source returned HTTP 503.' }],
				finds: [{ id: 5, title: 'A new open model', found_at: now - 600 }], last: null },
			{ key: 'curator-2', kind: 'curator', id: 2, name: 'Security', icon: '🛡️', color: '#ff6b6b', mission: '', role: 'watch',
				status: 'waiting', since: now, next_run: null, found_today: 0, finds: [], last: null },
			{ key: 'scout', kind: 'scout', id: null, name: 'Source Scout', icon: '🧭', color: '#5cc8ff', mission: 'Finds feeds.', status: 'idle',
				since: null, next_run: null, plans: 1, last: { query: 'https://lwn.net/', status: 'complete', suggestions: 1, finished_at: now - 60 } }
		],
		journal: [{ at: now - 60, agent: 'scout', name: 'Source Scout', icon: '🧭', kind: 'scout_run', status: 'complete', query: 'https://lwn.net/', suggestions: 1 }]
	};
	await page.route('**/api/hub/team', (route) => route.fulfill({ json: team }));
	await freshPage(page, '/team');
	await expect(page.getByRole('heading', { name: 'Agent Activity' })).toBeVisible();
	await expect(page.getByText('1/2')).toBeVisible();
	await expect(page.getByText(/Working · \d+s/)).toBeVisible();
	await expect(page.getByText('Waiting for a free slot')).toBeVisible();
	await expect(page.getByText('A new open model')).toBeVisible();
	const card = page.getByRole('article').filter({ hasText: 'Local AI' });
	await expect(card.locator('.now')).toHaveText('Checking a real feed.');
	await card.getByText('Run timeline').click();
	await expect(card.getByText('One source needs attention.')).toBeVisible();
	await card.getByText('Technical details').click();
	await expect(card.getByText('Source returned HTTP 503.')).toBeVisible();
	await expect(page.getByText('scouted “https://lwn.net/” and validated 1 source.')).toBeVisible();
	await page.screenshot({ path: 'test-results/team.png', fullPage: true });

	expect((await (await request.get('/api/settings')).json()).team.max_parallel).toBe(1);
	await page.goto('/settings?tab=ai');
	await expect(page.getByRole('heading', { name: 'Agent team' })).toBeVisible();
	const res = await request.put('/api/settings/team', { data: { max_parallel: 3 } });
	expect((await res.json()).max_parallel).toBe(3);
	await request.put('/api/settings/team', { data: { max_parallel: 1 } });
});
