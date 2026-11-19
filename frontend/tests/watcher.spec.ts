import { test, expect } from '@playwright/test';
import { freshPage } from './helpers';

test('suggest a recurring check and follow a Front Page update', async ({ page }) => {
	const goal = 'Tell me when Qwen 4 Flash is released.';
	const plan = { mode: 'bing_news', source: 'Qwen 4 Flash release', terms: ['Qwen 4 Flash is released'], selector: '' };
	const update = { id: 1, watch_id: 1, text: 'Qwen 4 Flash released', url: 'https://example.test/release',
		created_at: Date.now() / 1000, unread: true };
	let watch: Record<string, unknown> | undefined;
	await page.route('**/api/hub/watcher/draft', (route) => route.fulfill({ json: { plan } }));
	await page.route('**/api/hub/watcher', (route) => {
		if (route.request().method() === 'POST') {
			expect(route.request().postDataJSON()).toEqual({ goal, interval: 37, plan });
			watch = { id: 1, goal, interval: 37, plan, enabled: true, completed: false, last_check: null, error: '' };
			return route.fulfill({ json: watch });
		}
		return route.fulfill({ json: watch ? [watch] : [] });
	});
	await page.route(/\/api\/hub\/watcher\/updates(?:\?.*)?$/, (route) => route.fulfill({
		json: watch && (update.unread || !new URL(route.request().url()).searchParams.has('unread')) ? [update] : []
	}));
	await page.route('**/api/hub/watcher/updates/1/read', (route) => {
		update.unread = false;
		return route.fulfill({ json: { ok: true } });
	});

	await freshPage(page, '/watcher');
	await page.getByLabel('What should I watch for?').fill(goal);
	await page.getByLabel('Check every').selectOption({ label: 'Custom' });
	await page.getByLabel('Minutes between checks').fill('37');
	await page.getByRole('button', { name: 'Suggest a check' }).click();
	await expect(page.getByLabel('Search query')).toHaveValue(plan.source);
	await page.getByRole('button', { name: 'Start watching' }).click();
	await expect(page.getByText('News search · 37 minutes · Active')).toBeVisible();

	await page.goto('/today');
	const banner = page.getByRole('link', { name: /The Watcher · 1 new update/ });
	await expect(banner).toBeVisible();
	await banner.click();
	await expect(page).toHaveURL(/\/watcher#watch-updates$/);
	await page.getByRole('button', { name: 'Mark as read' }).click();
	await expect(page.getByRole('button', { name: 'Mark as read' })).toHaveCount(0);
	await page.goto('/today');
	await expect(banner).toHaveCount(0);
});
