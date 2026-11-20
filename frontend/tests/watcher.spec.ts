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
		json: watch && (update.unread || new URL(route.request().url()).searchParams.get('unread') !== 'true')
			&& (new URL(route.request().url()).searchParams.get('kind') === 'all'
				|| new URL(route.request().url()).searchParams.get('kind') === (update.watch_id === 0 ? 'test' : 'watch')) ? [update] : []
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

	await page.route('**/api/hub/watcher/watches/1/retry', (route) => {
		expect(route.request().method()).toBe('POST');
		watch = { ...watch, report: {
			checked_at: Date.now() / 1000, summary: 'The AI found no confirmed match in the search results.',
			result_count: 1, samples: [{ title: 'A release preview', url: 'https://example.test/preview', snippet: 'A preview only.' }],
			decisions: [{ condition: plan.terms[0], matched: false, reason: 'The source announces a future release.', title: '', url: '' }]
		} };
		return route.fulfill({ json: { ok: true } });
	});
	await page.getByRole('button', { name: 'Retry now' }).click();
	await expect(page.getByRole('status').filter({ hasText: 'Check queued.' })).toBeVisible();
	await page.getByText('Last check details', { exact: true }).click();
	await expect(page.getByText('The source announces a future release.')).toBeVisible();
	await expect(page.getByRole('link', { name: 'A release preview' })).toHaveAttribute('href', 'https://example.test/preview');

	await page.goto('/today');
	const banner = page.getByRole('link', { name: /The Watcher · 1 new update/ });
	await expect(banner).toBeVisible();
	await banner.click();
	await expect(page).toHaveURL(/\/watcher#watch-updates$/);
	await page.getByRole('button', { name: 'Mark as read' }).click();
	await expect(page.getByRole('button', { name: 'Mark as read' })).toHaveCount(0);
	await page.goto('/today');
	await expect(banner).toHaveCount(0);

	await page.route('**/api/hub/watcher/updates/test', (route) => {
		Object.assign(update, { watch_id: 0, text: 'This is a test notification from The Watcher.', url: '', unread: true });
		return route.fulfill({ json: update });
	});
	await page.goto('/watcher');
	await expect(page.getByRole('button', { name: 'Send test notification' })).toHaveCount(0);
	await page.goto('/settings?tab=watcher-notifications');
	await page.getByRole('button', { name: 'Send test notification' }).click();
	await expect(page.getByRole('button', { name: 'Mark test as read' })).toBeVisible();
	await page.goto('/today');
	await expect(banner).toContainText('This is a test notification from The Watcher.');
	await banner.click();
	await expect(page).toHaveURL(/\/settings\?tab=watcher-notifications$/);
	await page.getByRole('button', { name: 'Mark test as read' }).click();
	await expect(page.getByRole('button', { name: 'Mark test as read' })).toHaveCount(0);
	await page.goto('/watcher');
	await expect(page.getByText('No updates yet.')).toBeVisible();
});

test('mobile Settings opens Android controls through the Notifications section picker', async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await page.addInitScript(() => {
		Object.defineProperty(navigator, 'userAgent', { value: 'WatchCraftAndroid/0.1.21 WatcherNtfy/2' });
	});
	await page.route('**/api/hub/watcher/android', (route) => route.fulfill({ json: { configured: true } }));
	await freshPage(page, '/settings');
	const picker = page.getByLabel('Settings section', { exact: true });
	await expect(picker).toBeVisible();
	await picker.selectOption({ label: 'Notifications' });
	await expect(page.getByRole('heading', { name: 'Notifications', exact: true })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Watcher notifications' })).toBeVisible();
	await expect(page.getByRole('button', { name: 'Send test notification' })).toBeVisible();
	await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBe(0);
	await page.goto('/settings?tab=notify');
	await expect(page).toHaveURL(/\/settings\?tab=watcher-notifications$/);
	await expect(picker).toHaveValue('watcher-notifications');
});
