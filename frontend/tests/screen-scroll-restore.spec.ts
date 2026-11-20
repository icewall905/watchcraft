import { test, expect } from '@playwright/test';
import { freshPage, resetBackend } from './helpers';

// Back from a video lands where the user was on The Screen, even though the rows load after the page mounts.
test('screen: back from a video keeps the scroll position', async ({ page, request }) => {
	await resetBackend(request);
	for (let c = 0; c < 6; c++) {
		const cat = await (await request.post('/api/categories', { data: { name: `Scroll test ${c}`, brief: 'x' } })).json();
		for (let v = 0; v < 3; v++) await request.post('/api/candidates', { data: { video_id: `scr${c}${v}aaaaaa`.slice(0, 11), category_id: cat.id } });
	}
	await page.setViewportSize({ width: 1200, height: 500 });
	await freshPage(page, '/');
	const cards = page.locator('main a[href^="/watch/"]');
	await expect(cards.last()).toBeVisible({ timeout: 10000 });
	// Slow the feed on the way back (past the focus-restore window) so the page is still short when SvelteKit restores scroll.
	await page.route('**/api/feed/home*', async (route) => { await new Promise((r) => setTimeout(r, 3000)); await route.continue(); });
	// A card mid-page, so the old position is not simply the bottom of the page.
	const target = cards.nth(Math.floor((await cards.count()) / 2));
	await target.scrollIntoViewIfNeeded();
	const y = await page.evaluate(() => window.scrollY);
	expect(y).toBeLessThan(await page.evaluate(() => document.documentElement.scrollHeight - window.innerHeight - 100));
	expect(y).toBeGreaterThan(300);
	await target.click();
	await expect(page).toHaveURL(/\/watch\//);
	await page.goBack();
	await expect(page).toHaveURL(/\/$/);
	await expect.poll(() => page.evaluate(() => window.scrollY), { timeout: 8000 }).toBeGreaterThan(y - 5);
	expect(await page.evaluate(() => window.scrollY)).toBeLessThan(y + 5);
});
