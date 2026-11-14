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
