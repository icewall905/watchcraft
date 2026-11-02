import { test, expect, type Page } from '@playwright/test';
import { freshPage } from './helpers';

// Item lists filter as the user types: no submit, and tags count as matches.
const state = { saved: false, favorite: false, read_later: false, archived: false, dismissed: false, read_at: null, notes: '', tags: [], rating: 0 };

const item = (id: number, title: string, tags: string[] = []) => ({
	id, url: `https://example.org/${id}`, type: 'news', title, summary: 'A short summary', author: '', publisher: 'Example',
	image: '', local_image: false, published_at: 1793620000, first_seen: 1793620000, provider: 'rss',
	meta: tags.length ? { tags } : {}, enrichment: {}, state: { ...state }, score: 50, reason: '', curator_names: ['Local AI']
});

const ITEMS = [item(1, 'Rust release notes', ['programming']), item(2, 'Local servers guide', ['selfhosted'])];

async function stubItems(page: Page, seen: string[]) {
	await page.route('**/api/hub/items*', async (route) => {
		const url = new URL(route.request().url());
		seen.push(url.search);
		const q = (url.searchParams.get('q') ?? '').toLowerCase();
		const items = q
			? ITEMS.filter((i) => `${i.title} ${(i.meta.tags ?? []).join(' ')}`.toLowerCase().includes(q))
			: ITEMS;
		await route.fulfill({ json: { items, total: items.length } });
	});
}

test('Discover filters as you type without a submit', async ({ page }) => {
	const seen: string[] = [];
	await stubItems(page, seen);
	await freshPage(page, '/discover');
	await expect(page.getByRole('heading', { name: 'Rust release notes' })).toBeVisible();

	await page.getByRole('searchbox', { name: 'Filter discoveries' }).fill('selfhosted');
	await expect(page).toHaveURL(/q=selfhosted/);
	await expect(page.getByRole('heading', { name: 'Local servers guide' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Rust release notes' })).toHaveCount(0);
	expect(seen.some((s) => s.includes('q=selfhosted'))).toBe(true);

	// Clearing restores the full list.
	await page.getByRole('button', { name: 'Clear', exact: true }).click();
	await expect(page).not.toHaveURL(/q=/);
	await expect(page.getByRole('heading', { name: 'Rust release notes' })).toBeVisible();
});

test('a tag chip filters the list by that tag', async ({ page }) => {
	const seen: string[] = [];
	await stubItems(page, seen);
	await freshPage(page, '/discover');
	await page.getByRole('button', { name: '#programming' }).click();
	await expect(page).toHaveURL(/q=programming/);
	await expect(page.getByRole('heading', { name: 'Rust release notes' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Local servers guide' })).toHaveCount(0);
});

test('News filters the loaded stories live, including by tag', async ({ page }) => {
	await page.route('**/api/hub/news*', (route) => route.fulfill({
		json: {
			groups: [
				{ key: 'a', title: 'Rust release notes', items: [ITEMS[0]], latest: 1793620000 },
				{ key: 'b', title: 'Local servers guide', items: [ITEMS[1]], latest: 1793620000 }
			],
			topics: []
		}
	}));
	await freshPage(page, '/news');
	const rust = page.getByRole('region', { name: 'Rust release notes' });
	const servers = page.getByRole('region', { name: 'Local servers guide' });
	await expect(rust).toBeVisible();

	await page.getByRole('searchbox', { name: 'Filter news' }).fill('selfhosted');
	await expect(servers).toBeVisible();
	await expect(rust).toHaveCount(0);
});

test('Library offers the same live filter', async ({ page }) => {
	await freshPage(page, '/library');
	const filter = page.getByRole('searchbox', { name: 'Filter library' });
	await expect(filter).toBeVisible();
	await filter.fill('knitting');
	await expect(page).toHaveURL(/q=knitting/);
});
