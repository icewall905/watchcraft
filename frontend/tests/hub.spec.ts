import { test, expect } from '@playwright/test';
import { freshPage } from './helpers';

// Every hub section renders a real state (empty or populated) without page errors.
const ROUTES = ['/discover', '/news', '/knowledge', '/library', '/memes', '/briefings', '/alerts', '/research', '/curators', '/curators/new', '/sources', '/find?q=test', '/tv', '/settings?tab=search'];

test.beforeEach(async ({ request }) => {
	// These are browsing/fallback checks, independent of a configured AI server.
	await request.put('/api/settings/search', { data: { hub_ai: false } });
});

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
	// Drafting must reach a usable form: a duplicate list key used to abort the whole render.
	const errors: string[] = [];
	page.on('pageerror', (e) => errors.push(e.message));
	await freshPage(page, '/curators/new');
	await page.getByLabel('Your goal').fill('Find beginner knitting patterns for scarves');
	await page.getByRole('button', { name: 'Draft a curator' }).click();
	await expect(page.getByRole('heading', { name: 'Review the proposal' })).toBeVisible();
	await expect(page.getByRole('textbox', { name: 'Name', exact: true })).toHaveValue(d.name);
	expect(errors).toEqual([]);
});

test('the curator form can add a Mastodon source', async ({ page, request }) => {
	await freshPage(page, '/curators/new');
	// The Meme Tracker template is the one that needs a Mastodon source.
	await page.getByRole('button', { name: /Meme Tracker/ }).click();
	const group = page.locator('.grp').filter({ hasText: 'Mastodon communities' });
	await expect(group).toBeVisible();
	await group.getByLabel('Add Mastodon communities URL').fill('https://mastodon.world');
	await group.getByRole('button', { name: 'Add instance' }).click();
	// The new source becomes a selectable row for this curator.
	await expect(group.getByRole('checkbox')).toHaveCount(1);
	for (const s of await (await request.get('/api/hub/sources')).json())
		if (s.provider === 'mastodon') await request.delete(`/api/hub/sources/${s.id}`);
});

test('the curator form offers default Lemmy and Mastodon endpoints', async ({ page, request }) => {
	await freshPage(page, '/curators/new');
	await page.getByRole('button', { name: /Meme Tracker/ }).click();
	const lemmy = page.locator('.grp').filter({ hasText: 'Lemmy communities' });
	await lemmy.getByRole('button', { name: 'lemmy.world · selfhosted' }).click();
	await expect(lemmy.getByRole('checkbox')).toHaveCount(1);
	await expect(lemmy.getByRole('checkbox')).toBeChecked();
	const trends = page.locator('.grp').filter({ hasText: 'Mastodon trends' });
	await trends.getByRole('button', { name: 'mastodon.social · links' }).click();
	await expect(trends.getByRole('checkbox')).toHaveCount(1);
	await expect(trends.getByRole('checkbox')).toBeChecked();
	// Clicking the same default again deselects it instead of adding a duplicate.
	await lemmy.getByRole('button', { name: 'lemmy.world · selfhosted' }).click();
	await expect(lemmy.getByRole('checkbox')).not.toBeChecked();
	for (const s of await (await request.get('/api/hub/sources')).json())
		if (['lemmy', 'mastodon_trends'].includes(s.provider)) await request.delete(`/api/hub/sources/${s.id}`);
});

test('Search by meaning explains itself when no embeddings model is set', async ({ page }) => {
	await freshPage(page, '/find?q=rust');
	await page.getByRole('button', { name: 'Search by meaning' }).click();
	await expect(page.getByRole('heading', { name: 'By meaning' })).toBeVisible();
	await expect(page.getByText(/embeddings model/i)).toBeVisible();
});
