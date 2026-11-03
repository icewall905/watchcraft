import { test, expect } from '@playwright/test';
import { freshPage } from './helpers';

const held = (id: number, title: string, type: string) => ({
	id, url: `https://example.test/${id}`, type, title, summary: 'Held for a second look.', author: '', publisher: 'Example Journal', image: '', local_image: false,
	published_at: null, first_seen: 1, provider: 'test', meta: {}, enrichment: {}, state: { saved: false, favorite: false, read_later: false, archived: false, dismissed: false, read_at: null, notes: '', tags: [], rating: 0 },
	score: 30, reason: '', curator_names: [], verdict: { tier: 'set_aside', quality: 30, relevance: 30, final: 30, reasons: ['Too little detail'], judged_by: 'rules', overruled: '', judged_at: 1 }
});

test('set-aside items group by type and promotion removes them', async ({ page }) => {
	let promoted = false;
	await page.route('**/api/hub/today/set-aside**', (route) => route.fulfill({ json: { items: promoted ? [held(11, 'One more article', 'article')] : [held(10, 'A quiet news item', 'news'), held(11, 'One more article', 'article')], total: promoted ? 1 : 2 } }));
	await page.route('**/api/hub/items/10/overrule', async (route) => { promoted = true; await route.fulfill({ json: { tier: 'feature', reasons: ['you promoted it'] } }); });
	await freshPage(page, '/today/set-aside');
	await expect(page.getByRole('heading', { name: 'Articles' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'News', exact: true })).toBeVisible();
	await page.locator('article.item').filter({ hasText: 'A quiet news item' }).getByRole('button', { name: 'Bring into Today' }).click();
	await expect(page.getByRole('heading', { name: 'A quiet news item' })).toHaveCount(0);
	await expect(page.getByRole('status').filter({ hasText: 'was added to your edition' })).toBeVisible();
});

test('set-aside request errors offer a retry', async ({ page }) => {
	await page.route('**/api/hub/today/set-aside**', (route) => route.fulfill({ status: 503, json: { detail: 'Unavailable' } }));
	await freshPage(page, '/today/set-aside');
	await expect(page.getByRole('alert')).toContainText('Unavailable');
	await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
});


test('promotion preserves the next page of set-aside items', async ({ page }) => {
	let items = Array.from({ length: 31 }, (_, n) => held(n + 1, `Held item ${n + 1}`, 'news'));
	await page.route('**/api/hub/today/set-aside**', route => {
		const offset = Number(new URL(route.request().url()).searchParams.get('offset'));
		return route.fulfill({ json: { total: items.length, items: items.slice(offset, offset + 30) } });
	});
	await page.route('**/api/hub/items/1/overrule', route => { items = items.filter(i => i.id !== 1); return route.fulfill({ json: { tier: 'feature' } }); });
	await freshPage(page, '/today/set-aside');
	await page.locator('article.item').filter({ has: page.getByRole('heading', { name: 'Held item 1', exact: true }) }).getByRole('button').last().click();
	await page.getByRole('button', { name: 'Show more (1)', exact: true }).click();
	await expect(page.getByRole('heading', { name: 'Held item 31', exact: true })).toBeVisible();
});
