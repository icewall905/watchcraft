import { test, expect } from '@playwright/test';
import { freshPage } from './helpers';

test.beforeEach(async ({ page }) => { page.on('pageerror', error => { throw error; }); });

const item = (id: number, title: string, type = 'news') => ({
	id, url: `https://example.test/${id}`, type, title, summary: `A short note about ${title}.`, author: '', publisher: 'Example Journal',
	image: '', local_image: false, published_at: null, first_seen: 1, provider: 'test', meta: {}, enrichment: {},
	state: { saved: false, favorite: false, read_later: false, archived: false, dismissed: false, read_at: null, notes: '', tags: [], rating: 0 },
	score: 60, reason: '', curator_names: [], verdict: { tier: 'feature', quality: 70, relevance: 65, final: 68, reasons: ['Matches your interests'], judged_by: 'rules', overruled: '', judged_at: 1 }
});
const edition = () => ({
	day: '2026-11-03', built_at: 1, updated_at: 1, intro: 'A measured overview [1].', intro_refs: [{ n: 1, item_id: 1 }], set_aside: 2, curated: true,
	sections: [
		{ key: 'lead', title: 'Lead', entries: [{ item_id: 1, item: item(1, 'A thoughtful lead'), sources: 3, summary: 'The day’s central thread.' }] },
		{ key: 'stories', title: 'Stories', entries: [{ item_id: 2, item: item(2, 'A second story'), sources: 2, summary: 'A concise summary.' }] },
		{ key: 'reads', title: 'Reads', entries: [{ item_id: 3, item: item(3, 'A useful read', 'article') }] },
		{ key: 'memes', title: 'Memes', entries: [{ item_id: 4, item: { ...item(4, 'A small joke', 'meme'), meme: { spoiler: 'A small joke', sensitive: false, community: 'quiet-humor' } } }] },
		{ key: 'research', title: 'Research', curator_ids: [8], curators: [{ id: 8, name: 'Materials', subject: 'new materials', report_at: 1 }] },
		{ key: 'alerts', title: 'Alerts', count: 2 }
	]
});

test('Today assembles the edition, citations, reasons and curator actions', async ({ page }) => {
	const data = edition();
	await page.route('**/api/hub/items/1/overrule', route => route.fulfill({ json: { tier: 'feature', reasons: ['you promoted it'] } }));
	await page.route('**/api/hub/today', (route) => route.fulfill({ json: data }));
	await freshPage(page, '/today');
	await expect(page.getByText('A measured overview')).toBeVisible();
	await expect(page.getByRole('link', { name: 'Source [1]' })).toHaveAttribute('href', '/item/1');
	await expect(page.getByRole('heading', { name: 'A thoughtful lead' })).toBeVisible();
	await expect(page.getByText('+2 sources')).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Stories' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'A useful read' })).toBeVisible();
	await expect(page.getByRole('link', { name: 'Read the report' })).toHaveAttribute('href', '/research/8');
	await expect(page.getByRole('link', { name: 'Review alerts' })).toHaveAttribute('href', '/alerts');
	await page.getByRole('button', { name: 'Why?' }).first().click();
	await expect(page.getByText('Matches your interests')).toBeVisible();
	await page.getByRole('button', { name: 'Keep A thoughtful lead' }).click();
	await expect(page.getByRole('status').filter({ hasText: 'Kept' })).toBeVisible();
});

test('Today degrades calmly when no introduction or sections are ready', async ({ page }) => {
	await page.route('**/api/hub/today', (route) => route.fulfill({ json: { ...edition(), intro: '', intro_refs: [], sections: [], set_aside: 0, curated: false } }));
	await freshPage(page, '/today');
	await expect(page.getByText('A little of what you follow, gathered in one place.')).toBeVisible();
	await expect(page.getByRole('status').filter({ hasText: 'Your first edition is taking shape' })).toBeVisible();
});

test('Today gives a retry path when the edition request fails', async ({ page }) => {
	await page.route('**/api/hub/today', (route) => route.fulfill({ status: 503, json: { detail: 'Edition unavailable' } }));
	await freshPage(page, '/today');
	await expect(page.getByRole('alert')).toContainText('Today could not be loaded');
	await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
});

test('Paper and Dusk editions fit desktop and phone reading widths', async ({ page, request }) => {
	await page.route('**/api/hub/today', (route) => route.fulfill({ json: edition() }));
	try {
		for (const theme of ['paper', 'dusk']) {
			await request.put('/api/settings/appearance', { data: { theme } });
			for (const width of [1440, 390]) {
				await page.setViewportSize({ width, height: 1000 });
				await freshPage(page, '/today');
				await expect(page.getByRole('heading', { name: 'A thoughtful lead' })).toBeVisible();
				await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
				expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
				await page.screenshot({ path: `/tmp/watchcraft-today-${theme}-${width}.png`, fullPage: true });
			}
		}
	} finally { await request.put('/api/settings/appearance', { data: { theme: 'projector' } }); }
});


test('unavailable introduction sources preserve citation numbering', async ({ page }) => {
	const data = { ...edition(), intro: 'A missing source [1]. A visible source [2].', intro_refs: [{ n: 1, item_id: null }, { n: 2, item_id: 2 }] };
	await page.route('**/api/hub/today', route => route.fulfill({ json: data }));
	await freshPage(page, '/today');
	await expect(page.getByRole('link', { name: 'Source [1]' })).toHaveCount(0);
	await expect(page.getByRole('link', { name: 'Source [2]' })).toHaveAttribute('href', '/item/2');
});
