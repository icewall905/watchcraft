import { test, expect } from '@playwright/test';
import { freshPage } from './helpers';

test('Source Scout researches, restores history, and adds a suggestion to a curator', async ({ page, request }) => {
	const curator = await (await request.post('/api/hub/curators', { data: { name: 'My research', source_ids: [] } })).json();
	let saved = false;
	let accepted: unknown;
	const result = {
		id: 17, query: 'https://example.org/', status: 'complete', created_at: Date.now() / 1000, finished_at: Date.now() / 1000,
		events: [{ kind: 'validated', message: 'Validated a feed with 1 sample items.', at: Date.now() / 1000 }],
		suggestions: [{ provider: 'rss', url: 'https://example.org/feed', name: 'Example feed', count: 1,
			sample: [{ title: 'A real article', url: 'https://example.org/article' }], validated: true }],
		sitemaps: ['https://example.org/sitemap.xml'], error: ''
	};
	await page.route('**/api/hub/scout/runs', async route => {
		if (route.request().method() === 'POST') { saved = true; await route.fulfill({ json: result }); }
		else await route.fulfill({ json: saved ? [result] : [] });
	});
	await page.route('**/api/hub/scout/runs/17/accept', async route => {
		accepted = route.request().postDataJSON();
		await route.fulfill({ json: { id: 42, provider: 'rss', name: 'Example feed', url: 'https://example.org/feed', curator_ids: [curator.id] } });
	});
	await freshPage(page, '/sources');
	await page.getByRole('textbox', { name: 'Website or topic' }).fill('https://example.org/');
	await page.getByRole('button', { name: /Research again/ }).click();
	await page.getByRole('option', { name: 'Weekly' }).click();
	await page.getByRole('button', { name: 'Find sources' }).click();
	await expect(page.getByText('Validated a feed with 1 sample items.')).toBeVisible();
	await expect(page.getByText('A real article')).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Scheduled research' })).toBeVisible();
	await page.getByRole('button', { name: 'Pause', exact: true }).click();
	await expect(page.getByText(/Paused/)).toBeVisible();
	await page.getByRole('button', { name: 'Resume', exact: true }).click();
	await page.getByText('Sitemaps found (1)').click();
	await expect(page.getByRole('link', { name: 'https://example.org/sitemap.xml' })).toBeVisible();
	await page.getByRole('button', { name: /Add to curator \(optional\)/ }).click();
	await page.getByRole('option', { name: 'My research' }).click();
	await page.getByRole('article').getByRole('button', { name: 'Add source', exact: true }).click();
	await expect.poll(() => accepted).toEqual({ index: 0, curator_id: curator.id });
	await page.reload();
	await expect(page.getByLabel('Research history').getByRole('button', { name: /https:\/\/example.org/ })).toBeVisible();
	await expect(page.getByText('A real article')).toBeVisible();
});
