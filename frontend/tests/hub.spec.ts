import { test, expect } from '@playwright/test';
import { freshPage } from './helpers';

// Every hub section renders a real state (empty or populated) without page errors.
const ROUTES = ['/discover', '/news', '/knowledge', '/library', '/briefings', '/alerts', '/research', '/curators', '/curators/new', '/sources', '/find?q=test', '/tv', '/settings?tab=search'];

test('hub sections render without errors', async ({ page, request }) => {
	const errors: string[] = [];
	page.on('pageerror', (e) => errors.push(page.url() + ' ' + e.message));
	const col = await (await request.post('/api/hub/collections', { data: { name: 'Knitting', pinned: true } })).json();
	const topic = await (await request.post('/api/hub/topics', { data: { name: 'Home Assistant', keywords: ['home assistant'] } })).json();
	const src = await (await request.post('/api/hub/sources', { data: { provider: 'hackernews' } })).json();
	const cur = await (await request.post('/api/hub/curators', { data: { name: 'Local AI', queries: ['local llm'], source_ids: [src.id], interval_hours: 0 } })).json();
	await freshPage(page, '/discover');
	for (const r of [...ROUTES, `/curators/${cur.id}`, `/knowledge/${topic.id}`, `/library?collection=${col.id}`]) {
		await page.goto(r);
		await expect(page.locator('main h1, main h2').first()).toBeVisible();
		await expect(page.locator('main')).not.toContainText('Internal Server Error');
	}
	await expect(page.getByRole('navigation', { name: 'Main', exact: true }).getByRole('link', { name: 'Knitting' })).toBeVisible(); // pinned collection
	expect(errors).toEqual([]);
	await request.delete(`/api/hub/curators/${cur.id}`);
	await request.delete(`/api/hub/collections/${col.id}`);
	await request.delete(`/api/hub/topics/${topic.id}`);
	await request.delete(`/api/hub/sources/${src.id}`);
});

test('import a page into the library and find it', async ({ page, request }) => {
	// Network-free: the template wizard and draft endpoint work without AI or providers.
	const d = await (await request.post('/api/hub/curators/draft', { data: { goal: 'Find beginner knitting patterns for scarves' } })).json();
	expect(d.name).toBeTruthy();
	expect(d.provider_keys).toContain('web_search');
	await freshPage(page, '/curators/new?goal=Find%20beginner%20knitting%20patterns');
	await expect(page.locator('main')).toContainText(/knitting/i);
});
