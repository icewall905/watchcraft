import { expect, test } from '@playwright/test';
import { freshPage, resetBackend } from './helpers';

test.beforeAll(async ({ request }) => { await resetBackend(request); });

const video = { id: 'savevid0001', title: 'Curated egg drop', channel_id: 'UC1', channel_name: 'Chan', duration_s: 600, thumbnail: '' };

test('curated video can be saved to a new manual list, which shows no curation controls', async ({ page }) => {
	const profiles = await (await page.request.get('/api/profiles')).json();
	const headers = { 'X-Profile-Id': String(profiles[0].id) };
	const curated = await (await page.request.post('/api/categories', { headers, data: { name: 'Curated science', brief: 'science' } })).json();
	await page.route(`**/api/feed/category/${curated.id}*`, (route) => route.fulfill({ json: [
		{ id: 77, category_id: curated.id, video_id: video.id, status: 'accepted', score: 90, summary: 'fun', source: 'agent', video }
	] }));
	const saved: { video_id: string; category_id: number }[] = [];
	await page.route('**/api/candidates', async (route) => {
		if (route.request().method() !== 'POST') return route.continue();
		saved.push(route.request().postDataJSON());
		await route.fulfill({ json: { ok: true } });
	});

	await freshPage(page, `/category/${curated.id}`);
	await page.getByRole('link', { name: 'Curated egg drop' }).hover();
	await page.getByRole('button', { name: 'Save to playlist' }).click();
	await expect(page.getByRole('dialog')).toContainText('Save to playlist');
	await page.getByLabel('New list').fill('Watch later');
	await page.getByRole('button', { name: 'Create & save' }).click();
	await expect.poll(() => saved.length).toBe(1);

	const cats = await (await page.request.get('/api/categories', { headers })).json();
	const list = cats.find((c: { name: string }) => c.name === 'Watch later');
	expect(list.kind).toBe('manual');
	expect(saved[0]).toEqual({ video_id: video.id, category_id: list.id });

	await page.unroute('**/api/candidates');
	await page.goto(`/category/${list.id}`);
	await expect(page.getByRole('heading', { name: 'Watch later' })).toBeVisible();
	await expect(page.getByText('Hand-picked list')).toBeVisible();
	await expect(page.getByRole('button', { name: /Find more now/ })).toHaveCount(0);
	await expect(page.getByRole('tablist')).toHaveCount(0);
});

test('every video card on The Screen has Save to playlist', async ({ page }) => {
	const sub = { ...video, id: 'savevid0002', title: 'Subscribed upload' };
	await page.route('**/api/feed/home*', (route) => route.fulfill({ json: [
		{ kind: 'category', title: 'Science', emoji: '🔬', color: '#88f', category_id: 1, items: [{ video, score: 80, summary: 'fun', category_id: 1 }] },
		{ kind: 'subscriptions', title: 'New from your channels', emoji: '📺', items: [{ video: sub }] }
	] }));
	await freshPage(page, '/tv');
	for (const title of ['Curated egg drop', 'Subscribed upload']) {
		const card = page.locator('.card', { has: page.getByRole('link', { name: title }) });
		await card.hover();
		await expect(card.getByRole('button', { name: /Save to playlist/ })).toBeVisible();
	}
	await page.locator('.card', { has: page.getByRole('link', { name: 'Subscribed upload' }) }).getByRole('button', { name: /Save to playlist/ }).click();
	await expect(page.getByRole('dialog')).toContainText('Subscribed upload');
});
