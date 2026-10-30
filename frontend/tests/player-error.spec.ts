import { test, expect } from '@playwright/test';
import { freshPage } from './helpers';

test('opening the player directly handles an absent previous URL', async ({ page }) => {
	const errors: string[] = [];
	page.on('pageerror', e => errors.push(e.message));
	await page.route(/\/api\/video\/zzzzzzzzzzz.*/, r => r.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"Unavailable test video"}' }));
	await page.goto('/watch/zzzzzzzzzzz');
	await expect(page.getByRole('heading', { name: "Can't play this video" })).toBeVisible();
	expect(errors).toEqual([]);
});

// A video that cannot load shows an error card; Back and "Go back" must leave the player (no loop).
test('player error card: back leaves the player', async ({ page }) => {
	await page.route(/\/api\/video\/zzzzzzzzzzz.*/, (r) => r.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"YouTube blocked the request"}' }));
	await freshPage(page, '/');
	for (const how of ['Backspace', 'button'] as const) {
		await page.goto('/watch/zzzzzzzzzzz');
		await expect(page.getByRole('heading', { name: "Can't play this video" })).toBeVisible({ timeout: 15000 });
		if (how === 'Backspace') await page.keyboard.press('Backspace');
		else {
			await page.keyboard.press('ArrowRight');
			await expect.poll(() => page.evaluate(() => document.activeElement?.textContent?.trim())).toBe('Go back');
			await page.keyboard.press('Enter');
		}
		await expect(page).toHaveURL(/:\d+\/$/, { timeout: 5000 });
	}
});
