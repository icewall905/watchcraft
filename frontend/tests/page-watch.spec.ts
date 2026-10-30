import { test, expect } from '@playwright/test';
import { freshPage } from './helpers';

test('page watcher history compares real retained observations', async ({ page, request }) => {
	const response = await request.post('/api/hub/sources', { data: { provider: 'page_watch', url: 'https://example.org/notes', name: 'Release notes' } });
	expect(response.ok()).toBeTruthy();
	const source = await response.json();
	await page.route(`**/api/hub/sources/${source.id}/revisions`, async route => {
		await route.fulfill({ json: [
			{ id: 2, source_id: source.id, observed_at: 1000000001, title: 'New title', body: 'Release two is available now' },
			{ id: 1, source_id: source.id, observed_at: 1000000000, title: 'Old title', body: 'Release one is available now' }
		] });
	});
	await page.route(`**/api/hub/sources/${source.id}/checks`, async route => {
		await route.fulfill({ json: [
			{ id: 2, checked_at: 1000000000, trigger: 'shared', status: 'ok', item_count: 2, error: '' },
			{ id: 1, checked_at: 1000000000, trigger: 'curator', status: 'partial', item_count: 2, error: 'Provider unavailable' }
		] });
	});
	try {
		await freshPage(page, '/sources');
		const row = page.locator('li.row').filter({ hasText: 'Release notes' });
		await row.getByRole('button', { name: 'Check history' }).click();
		await expect(row.getByText(/Partially succeeded.*Provider unavailable/)).toBeVisible();
		await expect(row.getByText(/reused source fetch/)).toBeVisible();
		await row.getByRole('button', { name: 'View history' }).click();
		await row.getByText('Page changed', { exact: false }).click();
		await expect(row.getByText('Title before:')).toBeVisible();
		await expect(row.locator('p').filter({ hasText: 'Title before:' })).toContainText('Old title');
		await expect(row.getByText('Before: one')).toBeVisible();
		await expect(row.getByText('After: two')).toBeVisible();
		await row.getByText('Full observed text').first().click();
		await expect(row.getByText('Release two is available now')).toBeVisible();
	} finally {
		await request.delete(`/api/hub/sources/${source.id}`);
	}
});

test('Scout explains a suggested page watcher', async ({ page }) => {
	let saved = false;
	await page.route('**/api/hub/scout/runs', async route => {
		const result = { id: 17, query: 'https://example.org/', status: 'complete', created_at: 1, finished_at: 1,
			events: [], suggestions: [{ provider: 'page_watch', url: 'https://example.org/', name: 'Changelog', count: 1,
				sample: [{ title: 'Changelog', url: 'https://example.org/' }], validated: true }], sitemaps: [], error: '' };
		if (route.request().method() === 'POST') saved = true;
		await route.fulfill({ json: route.request().method() === 'POST' ? result : saved ? [result] : [] });
	});
	await freshPage(page, '/sources');
	await page.getByRole('textbox', { name: 'Website or topic' }).fill('https://example.org/');
	await page.getByRole('button', { name: 'Find sources' }).click();
	await expect(page.getByText('Page text extracted')).toBeVisible();
	await expect(page.getByText('Follows changes to this page’s extracted text, not new articles.')).toBeVisible();
});
