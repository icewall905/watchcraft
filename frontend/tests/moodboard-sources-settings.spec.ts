import { test, expect } from '@playwright/test';
import { freshPage } from './helpers';

test('mood board image sources can be toggled and saved', async ({ page }) => {
	await freshPage(page, '/settings?tab=search');
	await expect(page.getByRole('heading', { name: 'Mood Board image sources' })).toBeVisible();
	const reddit = page.getByRole('switch', { name: /Reddit/ });
	const before = await reddit.getAttribute('aria-checked');
	const saved = page.waitForRequest((r) => r.url().includes('/api/settings/moodboard_sources') && r.method() === 'PUT');
	await reddit.click();
	await page.getByRole('button', { name: 'Save image sources' }).click();
	const body = (await saved).postDataJSON();
	expect(body.reddit).toBe(before !== 'true');
	await expect(reddit).toHaveAttribute('aria-checked', before === 'true' ? 'false' : 'true');
	await reddit.click();
	await page.getByRole('button', { name: 'Save image sources' }).click();
	await expect(page.getByRole('button', { name: 'Save image sources' })).toHaveCount(0);
});

test('Pinterest scraping is off by default, warns about its terms and shows a self-pause', async ({ page }) => {
	await page.route('**/api/hub/boards/pinterest/status', (route) => route.fulfill({ json: { enabled: false, blocked: true, blocked_until: Math.floor(Date.now() / 1000) + 86400, reason: 'Pinterest answered with a web page (login or captcha) instead of results', last_block_at: Math.floor(Date.now() / 1000), last_reason: 'captcha', hour_left: 15, day_left: 80, images_left: 400 } }));
	await freshPage(page, '/settings?tab=search');
	const toggle = page.getByRole('switch', { name: /Pinterest/ });
	await expect(toggle).toHaveAttribute('aria-checked', 'false');
	await expect(page.getByText(/robots\.txt and terms of service forbid/)).toBeVisible();
	await expect(page.getByRole('status').filter({ hasText: 'Pinterest paused itself' })).toContainText('login or captcha');
	const saved = page.waitForRequest((r) => r.url().includes('/api/settings/moodboard_sources') && r.method() === 'PUT');
	await toggle.click();
	await page.getByRole('button', { name: 'Save image sources' }).click();
	expect((await saved).postDataJSON().pinterest).toBe(true);
	await toggle.click();
	await page.getByRole('button', { name: 'Save image sources' }).click();
	await expect(page.getByRole('button', { name: 'Save image sources' })).toHaveCount(0);
});
