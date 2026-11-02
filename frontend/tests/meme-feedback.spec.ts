import { test, expect } from '@playwright/test';
import { freshPage } from './helpers';

// Thumbs on a meme teach the curators that actually found it.
const state = { saved: false, favorite: false, read_later: false, archived: false, dismissed: false, read_at: null, notes: '', tags: [], rating: 0 };

const meme = (id: number, title: string, curatorIds: number[]) => ({
	id, url: `https://example.org/${id}`, type: 'meme', title, summary: '', author: '', publisher: 'mastodon.social',
	image: '', local_image: false, published_at: 1793620000, first_seen: 1793620000, provider: 'mastodon',
	meta: { tags: ['memes'], platform: 'mastodon' }, enrichment: {}, state: { ...state }, score: 60, reason: '',
	curator_names: curatorIds.length ? ['Meme Tracker'] : [], curator_ids: curatorIds,
	meme: { format_id: null, format_name: 'Reaction', community: 'mastodon.social', language: 'en', sensitive: false, spoiler: '', dup_of: null }
});

test('meme thumbs teach the curator that found it', async ({ page }) => {
	const posts: unknown[] = [];
	await page.route('**/api/hub/memes/items*', (route) => route.fulfill({
		json: { items: [meme(1, 'Spongebob reaction', [5]), meme(2, 'Manually added meme', [])], total: 2 }
	}));
	await page.route('**/api/hub/memes/facets*', (route) => route.fulfill({ json: { communities: [], languages: [] } }));
	await page.route('**/api/hub/topics*', (route) => route.fulfill({ json: [] }));
	await page.route('**/api/hub/items/*/curator-feedback', async (route) => {
		posts.push(route.request().postDataJSON());
		await route.fulfill({ json: { curator_id: 5, verdict: 'more', added: ['spongebob'], tuned_up: ['spongebob'], tuned_down: [] } });
	});

	await freshPage(page, '/memes?tab=examples');
	const up = page.getByRole('button', { name: /More memes like this: Spongebob reaction/ });
	await expect(up).toBeVisible();
	await up.click();
	await expect(up).toHaveAttribute('aria-pressed', 'true');
	expect(posts).toEqual([{ curator_id: 5, verdict: 'more' }]);

	// A meme nobody curated has nothing to teach, so it offers no thumbs.
	await expect(page.getByRole('button', { name: /More memes like this: Manually added meme/ })).toHaveCount(0);
});

test('a thumbs down hides the meme and keeps it hidden', async ({ page }) => {
	const posts: unknown[] = [];
	const states: unknown[] = [];
	await page.route('**/api/hub/memes/items*', (route) => route.fulfill({ json: { items: [meme(1, 'Spongebob reaction', [5])], total: 1 } }));
	await page.route('**/api/hub/memes/facets*', (route) => route.fulfill({ json: { communities: [], languages: [] } }));
	await page.route('**/api/hub/topics*', (route) => route.fulfill({ json: [] }));
	await page.route('**/api/hub/items/*/curator-feedback', async (route) => {
		posts.push(route.request().postDataJSON());
		await route.fulfill({ json: { curator_id: 5, verdict: 'less', added: ['spongebob'], tuned_up: [], tuned_down: ['spongebob'] } });
	});
	await page.route('**/api/hub/items/*/state', async (route) => {
		states.push(route.request().postDataJSON());
		await route.fulfill({ json: { ...state, dismissed: true } });
	});

	await freshPage(page, '/memes?tab=examples');
	const card = page.getByRole('button', { name: 'Open meme: Spongebob reaction' });
	await expect(card).toBeVisible();
	await page.getByRole('button', { name: /Fewer memes like this: Spongebob reaction/ }).click();
	// Gone from the feed, and the item itself is dismissed so it cannot come back.
	await expect(card).toHaveCount(0);
	expect(posts).toEqual([{ curator_id: 5, verdict: 'less' }]);
	expect(states).toEqual([{ dismissed: true }]);
	await expect(page.getByRole('button', { name: 'Undo' })).toBeVisible();
});

test('the meme drawer thumbs down closes the drawer and hides the meme', async ({ page }) => {
	const states: unknown[] = [];
	await page.route('**/api/hub/memes/items*', (route) => route.fulfill({ json: { items: [meme(1, 'Spongebob reaction', [5])], total: 1 } }));
	await page.route('**/api/hub/memes/facets*', (route) => route.fulfill({ json: { communities: [], languages: [] } }));
	await page.route('**/api/hub/topics*', (route) => route.fulfill({ json: [] }));
	await page.route('**/api/hub/memes/items/1/similar', (route) => route.fulfill({ json: { items: [] } }));
	await page.route('**/api/hub/items/*/curator-feedback', (route) => route.fulfill({
		json: { curator_id: 5, verdict: 'less', added: ['spongebob'], tuned_up: [], tuned_down: ['spongebob'] }
	}));
	await page.route('**/api/hub/items/*/state', async (route) => {
		states.push(route.request().postDataJSON());
		await route.fulfill({ json: { ...state, dismissed: true } });
	});

	await freshPage(page, '/memes?tab=examples');
	await page.getByRole('button', { name: 'Open meme: Spongebob reaction' }).click();
	const drawer = page.getByRole('dialog', { name: 'Meme detail' });
	await drawer.getByRole('button', { name: '👎 Less like this' }).click();
	await expect(drawer).toHaveCount(0);
	await expect(page.getByRole('button', { name: 'Open meme: Spongebob reaction' })).toHaveCount(0);
	expect(states).toEqual([{ dismissed: true }]);
});

test('a thumbs down still hides the meme when there is nothing to learn, and asks why', async ({ page }) => {
	const posts: unknown[] = [];
	const states: unknown[] = [];
	await page.route('**/api/hub/memes/items*', (route) => route.fulfill({ json: { items: [meme(1, 'Spongebob reaction', [5])], total: 1 } }));
	await page.route('**/api/hub/memes/facets*', (route) => route.fulfill({ json: { communities: [], languages: [] } }));
	await page.route('**/api/hub/topics*', (route) => route.fulfill({ json: [] }));
	await page.route('**/api/hub/items/*/curator-feedback', async (route) => {
		const body = route.request().postDataJSON();
		posts.push(body);
		if (!body.terms) {
			await route.fulfill({ status: 422, json: { detail: 'Nothing distinctive in this finding to learn from.' } });
			return;
		}
		await route.fulfill({ json: { curator_id: 5, verdict: 'less', added: body.terms, tuned_up: [], tuned_down: body.terms } });
	});
	await page.route('**/api/hub/items/*/state', async (route) => {
		states.push(route.request().postDataJSON());
		await route.fulfill({ json: { ...state, dismissed: true } });
	});

	await freshPage(page, '/memes?tab=examples');
	await page.getByRole('button', { name: /Fewer memes like this: Spongebob reaction/ }).click();
	// Hidden even though the curator could not learn anything from the title.
	await expect(page.getByRole('button', { name: 'Open meme: Spongebob reaction' })).toHaveCount(0);
	expect(states).toEqual([{ dismissed: true }]);
	// And it asks what to avoid instead of leaving it unexplained.
	const dialog = page.getByRole('dialog', { name: 'What should I avoid?' });
	await expect(dialog).toBeVisible();
	await dialog.getByRole('textbox', { name: 'What to avoid' }).fill('spongebob');
	await dialog.getByRole('button', { name: 'Learn this' }).click();
	await expect(dialog).toHaveCount(0);
	expect(posts.at(-1)).toEqual({ curator_id: 5, verdict: 'less', terms: ['spongebob'] });
});
