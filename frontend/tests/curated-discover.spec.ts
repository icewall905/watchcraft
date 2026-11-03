import { test, expect } from '@playwright/test';
import { freshPage } from './helpers';

test('curated selections remain reachable and filters reset pagination', async ({ page }) => {
	const requests: URL[] = [];
	await page.route('**/api/hub/items?**', route => {
		const url = new URL(route.request().url()); requests.push(url);
		const id = Number(url.searchParams.get('offset') ?? 0) + 1;
		return route.fulfill({ json: { total: 31, items: [{ id, title: `Discovery ${id}`, type: 'article', url: `https://example.test/${id}`, publisher: 'Journal', author: '', summary: 'A thoughtful discovery.', image: '', local_image: false, first_seen: 1, published_at: null, provider: 'test', meta: {}, enrichment: {}, score: 80, reason: '', curator_names: [], state: { saved: false, favorite: false, read_later: false, archived: false, dismissed: false, read_at: null, notes: '', tags: [], rating: 0 } }] } });
	});
	await freshPage(page, '/discover');
	await expect(page.getByRole('heading', { name: 'Discovery 1', exact: true })).toBeVisible();
	await expect(page.getByRole('combobox', { name: 'Sort', exact: true })).toHaveValue('best');
	await page.getByRole('button', { name: 'Next selection' }).click();
	await expect(page.getByRole('heading', { name: 'Discovery 31', exact: true })).toBeVisible();
	await expect(page.getByRole('button', { name: 'Previous selection' })).toBeVisible();
	await page.getByRole('combobox', { name: 'Sort', exact: true }).selectOption('newest');
	await expect(page.getByRole('heading', { name: 'Discovery 1', exact: true })).toBeVisible();
	expect(requests.at(-1)?.searchParams.get('sort')).toBe('newest');
	expect(requests.at(-1)?.searchParams.get('offset')).toBe('0');
	await page.getByRole('button', { name: 'Next selection' }).click();
	await expect(page.getByRole('heading', { name: 'Discovery 31', exact: true })).toBeVisible();
	await page.getByLabel('Filter discoveries', { exact: true }).fill('a narrow subject');
	await expect(page).not.toHaveURL(/[?&]page=/);
	await expect(page.getByRole('heading', { name: 'Discovery 1', exact: true })).toBeVisible();
});
