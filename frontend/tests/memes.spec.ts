import { test, expect } from '@playwright/test';
import { freshPage } from './helpers';

test('Meme Radar is in navigation and renders its empty states', async ({ page }) => {
	await freshPage(page, '/');
	const nav = page.getByRole('navigation', { name: 'Main', exact: true });
	await expect(nav.getByRole('link', { name: 'Meme Radar' })).toBeVisible();
	await nav.getByRole('link', { name: 'Meme Radar' }).click();
	await expect(page.getByRole('heading', { name: 'Meme Radar', level: 1 })).toBeVisible();
	for (const t of ['Trending', 'Emerging', 'Formats', 'Explore', 'Saved', 'History'])
		await expect(page.getByRole('tab', { name: t, exact: true })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Nothing counted trending yet' })).toBeVisible();
	await page.getByRole('tab', { name: 'Explore', exact: true }).click();
	await expect(page.getByRole('heading', { name: 'No memes yet' })).toBeVisible();
	await expect(page.getByRole('link', { name: 'Create a Meme Tracker' })).toHaveAttribute('href', '/curators/new?template=meme-radar');
});

test('Explore loads facets once and protects sensitive image previews', async ({ page }) => {
	let topicCalls = 0;
	await page.route('**/api/hub/items/999/assets/image', (route) => route.fulfill({ contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1ZkAAAAASUVORK5CYII=', 'base64') }));
	await page.route('**/api/hub/topics', async (route) => { topicCalls++; await route.fulfill({ json: [] }); });
	await page.route('**/api/hub/memes/facets', (route) => route.fulfill({ json: {
		communities: [{ key: '#linux', count: 1 }], languages: [{ key: 'en', count: 1 }]
	} }));
	await page.route('**/api/hub/memes/items?*', (route) => route.fulfill({ json: { total: 1, items: [{
		id: 999, title: 'Sensitive example', summary: '', first_seen: 1, url: 'https://example.com/post',
		provider: 'mastodon', local_image: true, state: {}, meme: { sensitive: true, spoiler: 'Warning', community: '#linux' }
	}] } }));
	await freshPage(page, '/memes?tab=examples');
	await expect(page.getByLabel('Community filter').locator('option')).toHaveText(['All communities', '#linux (1)']);
	await expect(page.getByLabel('Language filter').locator('option')).toHaveText(['All languages', 'en (1)']);
	await expect(page.locator('.meme-card .shot')).toHaveClass(/sensitive/);
	await expect(page.locator('.meme-card img')).toHaveCSS('filter', 'blur(7px)');
	const callsAfterLoad = topicCalls;
	await page.getByRole('tab', { name: 'Saved', exact: true }).click();
	await expect(page.getByRole('tab', { name: 'Saved', exact: true })).toHaveAttribute('aria-selected', 'true');
	await expect.poll(() => topicCalls).toBe(callsAfterLoad);
});
