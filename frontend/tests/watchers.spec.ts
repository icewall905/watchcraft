import { test, expect, type Page } from '@playwright/test';
import { freshPage } from './helpers';

// Website watchers on the Sources page, against mocked /api/hub endpoints.
const health = (status: string, extra: Record<string, unknown> = {}) => ({
	status, signals: [], checks: 10, success_rate: 1, empty_rate: 0, consecutive_failures: 0, typical_count: 20, trend: 'steady', ...extra
});
const base = { enabled: true, last_success: 1000000000, last_error: '', last_checked: 1000000000, item_count: 20, curator_ids: [], last_repair: null };
const listing = {
	...base, id: 901, provider: 'html_listing', name: 'Example blog', url: 'https://example.org/blog', configurable: true,
	config: { item_selector: 'article.post' },
	health: health('degraded', { success_rate: 0.85, trend: 'worsening', signals: [{ key: 'structure_change', message: 'Only 2 items extracted where 20 are typical.' }] })
};
const pageWatch = { ...base, id: 902, provider: 'page_watch', name: 'Release notes', url: 'https://example.org/notes', configurable: true, config: {}, health: health('healthy') };
const sitemap = { ...base, id: 903, provider: 'sitemap', name: 'Docs sitemap', url: 'https://example.org/sitemap.xml', configurable: true, config: {}, health: health('broken', { success_rate: 0, consecutive_failures: 3 }) };
const sample = [{ title: 'Hello world', url: 'https://example.org/blog/hello', published_at: 1700000000, excerpt: 'First post excerpt.' }];

async function mockSources(page: Page) {
	await page.route('**/api/hub/sources', (route) =>
		route.request().method() === 'GET' ? route.fulfill({ json: [listing, pageWatch, sitemap] }) : route.fallback());
	await page.route('**/api/hub/sources/901/watcher', async (route) => {
		if (route.request().method() === 'GET') {
			return route.fulfill({ json: { config: listing.config, fields: ['allowed_patterns', 'auto_repair', 'date_selector', 'ignore_selectors', 'ignored_patterns', 'item_selector', 'link_selector', 'strip_query', 'title_selector'],
				revisions: [{ id: 2, source_id: 901, created_at: 1000000000, config: listing.config, reason: 'edit', item_count: 20, sample },
					{ id: 1, source_id: 901, created_at: 999990000, config: {}, reason: 'original', item_count: 18, sample }] } });
		}
		return route.fallback();
	});
}

test('health status shows a text label, summary and signals', async ({ page }) => {
	await mockSources(page);
	await freshPage(page, '/sources');
	const row = page.locator('li.row').filter({ hasText: 'Example blog' });
	await expect(row.getByText('Degraded', { exact: true })).toBeVisible();
	await expect(row.getByText('success 85% · typical 20 items · worsening')).toBeVisible();
	await expect(row.getByText('Only 2 items extracted where 20 are typical.')).toBeVisible();
	await expect(page.locator('li.row').filter({ hasText: 'Release notes' }).getByText('Healthy', { exact: true })).toBeVisible();
	await expect(page.locator('li.row').filter({ hasText: 'Docs sitemap' }).getByText('Broken', { exact: true })).toBeVisible();
	await expect(row.getByRole('button', { name: 'Repair' })).toHaveClass(/primary/);
	await expect(page.locator('li.row').filter({ hasText: 'Release notes' }).getByRole('button', { name: 'Repair' })).not.toHaveClass(/primary/);
});

test('rules dry run shows samples; failed save offers Save anyway', async ({ page }) => {
	await mockSources(page);
	const bodies: Record<string, unknown>[] = [];
	await page.route('**/api/hub/sources/901/watcher/test', async (route) => {
		const body = route.request().postDataJSON();
		bodies.push(body);
		if (body.config.item_selector === 'div[') return route.fulfill({ status: 422, json: { detail: 'item_selector: invalid CSS selector "div["' } });
		return route.fulfill({ json: { config: body.config, ok: true, count: 12, sample, error: '' } });
	});
	const saves: Record<string, unknown>[] = [];
	await page.route('**/api/hub/sources/901/watcher', async (route) => {
		if (route.request().method() !== 'PUT') return route.fallback();
		const body = route.request().postDataJSON();
		saves.push(body);
		const test = { ok: false, count: 0, sample: [], error: 'No items matched article.card' };
		return route.fulfill({ json: body.force ? { saved: true, test, source: { ...listing, config: body.config } } : { saved: false, test } });
	});
	await freshPage(page, '/sources');
	const row = page.locator('li.row').filter({ hasText: 'Example blog' });
	await row.getByRole('button', { name: 'Watcher rules' }).click();
	const item = row.getByRole('textbox', { name: 'Item selector' });
	await expect(item).toHaveValue('article.post');
	await row.getByRole('textbox', { name: 'Only these paths' }).fill('/blog/*\n/news/*');
	await row.getByRole('button', { name: 'Test rules' }).click();
	await expect(row.getByText('Dry run passed: 12 items extracted.')).toBeVisible();
	await expect(row.getByText('Hello world')).toBeVisible();
	await expect(row.getByText('First post excerpt.')).toBeVisible();
	expect(bodies[0]).toEqual({ config: { item_selector: 'article.post', allowed_patterns: ['/blog/*', '/news/*'] } });

	await item.fill('div[');
	await row.getByRole('button', { name: 'Test rules' }).click();
	await expect(row.getByRole('alert').filter({ hasText: 'invalid CSS selector' })).toBeVisible();

	await item.fill('article.card');
	await row.getByRole('button', { name: 'Save', exact: true }).click();
	await expect(row.getByText('Live test failed: No items matched article.card')).toBeVisible();
	await expect(row.getByText(/Not saved/)).toBeVisible();
	await row.getByRole('button', { name: 'Save anyway' }).click();
	await expect(row.getByRole('button', { name: 'Save anyway' })).toBeHidden();
	expect(saves.map((s) => s.force)).toEqual([false, true]);
	expect(saves[1]).toMatchObject({ reason: 'edit', config: { item_selector: 'article.card' } });
	await row.getByText('Rule history (2)').click();
	await expect(row.getByText('Original rules', { exact: false })).toBeVisible();
	await expect(row.getByRole('button', { name: 'Restore' })).toHaveCount(1);
	const restores: { force?: boolean }[] = [];
	await page.route('**/api/hub/sources/901/watcher/rollback/1', async (route) => {
		const body = route.request().postDataJSON();
		restores.push(body);
		const ok = !!body.force;
		await route.fulfill({ json: ok ? { saved: true, test: { ok: false, count: 0, sample: [], error: 'The rules extracted nothing.' }, source: listing }
			: { saved: false, test: { ok: false, count: 0, sample: [], error: 'The rules extracted nothing.' } } });
	});
	await row.getByRole('button', { name: 'Restore' }).click();
	await expect(row.getByText(/Not restored/)).toBeVisible();
	await row.getByRole('button', { name: 'Restore anyway' }).click();
	await expect(row.getByRole('button', { name: 'Restore anyway' })).toBeHidden();
	expect(restores.map((r) => !!r.force)).toEqual([false, true]);
});

test('repair shows steps, proposals and applies one', async ({ page }) => {
	await mockSources(page);
	await page.route('**/api/hub/sources/901/repair', (route) => route.fulfill({ json: {
		health: listing.health,
		current: { config: listing.config, ok: false, count: 0, sample: [], error: 'No items matched article.post' },
		proposals: [{ config: { item_selector: 'li.entry', link_selector: 'a.title' }, ok: true, count: 19, sample, error: '', comparison: { typical_count: 20, current_count: 0, overlap: 0.8 } }],
		alternates: [{ provider: 'rss', url: 'https://example.org/feed.xml', name: 'Example feed', count: 10, sample, validated: true }],
		steps: [{ step: 'revalidate', ok: false, detail: 'Current rules extracted nothing.' }, { step: 'alternatives', ok: true, detail: 'Found 1 feed.' },
			{ step: 'reinspect', ok: true, detail: 'Inspected the page structure; 1 rule set(s) to test.' }, { step: 'test', ok: true, detail: '1 proposal passed.' }]
	} }));
	let applied: Record<string, unknown> | null = null;
	await page.route('**/api/hub/sources/901/watcher', async (route) => {
		if (route.request().method() !== 'PUT') return route.fallback();
		applied = route.request().postDataJSON();
		return route.fulfill({ json: { saved: true, test: { ok: true, count: 19, sample, error: '' }, source: { ...listing, config: (applied as { config: object }).config, health: health('healthy') } } });
	});
	await freshPage(page, '/sources');
	const row = page.locator('li.row').filter({ hasText: 'Example blog' });
	await row.getByRole('button', { name: 'Repair' }).click();
	await expect(row.getByText('Re-tested the current rules')).toBeVisible();
	await expect(row.getByText('Current rules extracted nothing.')).toBeVisible();
	await expect(row.getByText('typical 20 · now 0 · overlap 80%', { exact: false })).toBeVisible();
	await expect(row.getByText('item_selector: li.entry · link_selector: a.title')).toBeVisible();
	await expect(row.getByRole('button', { name: 'Add as source' })).toBeVisible();
	await row.getByRole('button', { name: 'Apply' }).click();
	await expect(row.getByText('Applied.')).toBeVisible();
	expect(applied).toEqual({ config: { item_selector: 'li.entry', link_selector: 'a.title' }, reason: 'repair', force: false });
});

test('page diff ops render with +/- markers', async ({ page }) => {
	await mockSources(page);
	await page.route('**/api/hub/sources/902/revisions', (route) => route.fulfill({ json: [
		{ id: 2, source_id: 902, observed_at: 1000000001, title: 'Notes', body: 'Release two is out.\n\nShared paragraph.',
			diff: { ops: [{ op: 'removed', text: 'Release one is out.' }, { op: 'added', text: 'Release two is out.' }], added_chars: 19, removed_chars: 19, truncated: false } },
		{ id: 1, source_id: 902, observed_at: 1000000000, title: 'Notes', body: 'Release one is out.\n\nShared paragraph.', diff: null }
	] }));
	await freshPage(page, '/sources');
	const row = page.locator('li.row').filter({ hasText: 'Release notes' });
	await row.getByRole('button', { name: 'View history' }).click();
	await row.getByText('Page changed', { exact: false }).click();
	await expect(row.getByText('+19 / −19 characters')).toBeVisible();
	await expect(row.locator('.diff li.added')).toHaveText(/^\+\s*Release two is out\.$/);
	await expect(row.locator('.diff li.removed')).toHaveText(/^−\s*Release one is out\.$/);
	await row.getByText('Full observed text').first().click();
	await expect(row.getByText('Shared paragraph.').first()).toBeVisible();
});

test('sitemap changes list new and updated URLs', async ({ page }) => {
	await mockSources(page);
	await page.route('**/api/hub/sources/903/changes', (route) => route.fulfill({ json: {
		total: 120, new: 2, updated: 1,
		changes: [{ url: 'https://example.org/docs/new-page', lastmod: '2026-10-30', first_seen: 1000000000, changed_at: 1000000000, change: 'new' },
			{ url: 'https://example.org/docs/old-page', lastmod: '2026-10-29', first_seen: 999000000, changed_at: 999999000, change: 'updated' }]
	} }));
	await freshPage(page, '/sources');
	const row = page.locator('li.row').filter({ hasText: 'Docs sitemap' });
	await row.getByRole('button', { name: 'View changes' }).click();
	await expect(row.getByText('120 URLs tracked · 2 new · 1 updated')).toBeVisible();
	await expect(row.getByRole('link', { name: 'https://example.org/docs/new-page' })).toBeVisible();
	await expect(row.locator('.tag.updated')).toHaveText('Updated');
});
