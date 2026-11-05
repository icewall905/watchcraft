import { test, expect } from '@playwright/test';
import { freshPage } from './helpers';

test('news feedback uses hub article endpoint, toggles ratings, and submits optional learning details', async ({ page }) => {
	const initial = { saved: false, favorite: false, read_later: false, archived: false, dismissed: false, read_at: null, notes: '', tags: [], rating: 0 };
	await page.route('**/api/hub/news*', route => route.fulfill({ json: { topics: [], groups: [{
		key: 'story-1', title: 'A useful local story', latest: Date.now() / 1000,
		items: [{ id: 911, type: 'news', title: 'A useful local story', summary: 'A concise report.', provider: 'feed', publisher: 'Local', local_image: false, state: initial, url: 'https://example.test/story' }]
	}] } }));
	const sent: { rating: number; chips: string[]; text: string }[] = [];
	await page.route('**/api/hub/items/911/feedback', async route => {
		const body = route.request().postDataJSON();
		sent.push(body);
		await route.fulfill({ json: { state: { ...initial, rating: body.rating }, feedback: { rating: body.rating, chips: body.chips, text: body.text } } });
	});
	await freshPage(page, '/news');
	await page.getByRole('button', { name: 'Like article', exact: true }).click();
	await expect(page.getByRole('button', { name: 'Like article', exact: true })).toHaveAttribute('aria-pressed', 'true');
	await page.getByRole('button', { name: 'Like article', exact: true }).click();
	await expect(page.getByRole('button', { name: 'Like article', exact: true })).toHaveAttribute('aria-pressed', 'false');
	await page.getByRole('button', { name: 'Like article', exact: true }).click();
	await page.getByRole('button', { name: 'Dislike article' }).click();
	await expect(page.getByRole('button', { name: 'Dislike article' })).toHaveAttribute('aria-pressed', 'true');
	await page.getByRole('button', { name: 'Why?' }).click();
	await page.getByRole('button', { name: 'Too repetitive' }).click();
	await page.getByLabel('Optional note').fill('Too many versions of this story.');
	await page.getByRole('button', { name: 'Send feedback' }).click();
	await expect(page.getByRole('button', { name: 'Why?' })).toBeVisible();
	expect(sent).toEqual([
		{ rating: 1, chips: [], text: '' },
		{ rating: 0, chips: [], text: '' },
		{ rating: 1, chips: [], text: '' },
		{ rating: -1, chips: [], text: '' },
		{ rating: -1, chips: ['Too repetitive'], text: 'Too many versions of this story.' }
	]);
	await page.setViewportSize({ width: 390, height: 844 });
	await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
	await page.screenshot({ path: '/tmp/watchcraft-article-feedback-390.png' });
});
