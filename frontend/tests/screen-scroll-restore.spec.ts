import { test, expect, type Page, type APIRequestContext } from '@playwright/test';
import { freshPage, resetBackend } from './helpers';

// Back from a video lands where the user was on The Screen and stays there: no late jumps while data arrives,
// and scrolling away by hand is never undone.

async function seed(request: APIRequestContext) {
	await resetBackend(request);
	let first = 0;
	for (let c = 0; c < 10; c++) {
		const cat = await (await request.post('/api/categories', { data: { name: `Scroll test ${c}`, brief: 'x' } })).json();
		first ||= cat.id;
		// Category 0 gets enough videos for its own page to scroll.
		for (let v = 0; v < (c === 0 ? 30 : 3); v++) {
			await request.post('/api/candidates', { data: { video_id: `s${c}v${String(v).padStart(2, '0')}aaaaaaaa`.slice(0, 11), category_id: cat.id } });
		}
	}
	return first;
}

const scrollY = (page: Page) => page.evaluate(() => Math.round(window.scrollY));

async function roundTrip(page: Page, path: string, feed: string, phone = false) {
	await freshPage(page, path);
	const cards = page.locator('main a[href^="/watch/"]');
	await expect(cards.nth(10)).toBeVisible({ timeout: 10000 });
	// Slow the feed from now on: on the way back the old code showed a short skeleton and lost the position.
	await page.route(feed, async (route) => { await new Promise((r) => setTimeout(r, 3000)); await route.continue(); });
	// Somewhere mid-page (not simply the bottom), then a card that is on screen there.
	await page.evaluate(() => window.scrollTo(0, Math.round((document.documentElement.scrollHeight - window.innerHeight) * 0.6)));
	const index = await page.evaluate(() => [...document.querySelectorAll('main a[href^="/watch/"]')].findIndex((a) => {
		const r = a.getBoundingClientRect();
		const mid = (r.top + r.bottom) / 2;
		return r.height > 0 && mid > 80 && mid < window.innerHeight - 20 && r.left >= 0 && r.right <= window.innerWidth;
	}));
	expect(index).toBeGreaterThan(-1);
	const target = cards.nth(index);
	const before = await scrollY(page);
	// The position at the moment of leaving (Playwright may nudge the card into view before clicking).
	await page.evaluate(() => addEventListener('click', () => ((window as unknown as { leftAt: number }).leftAt = Math.round(window.scrollY)), { capture: true, once: true }));
	expect(before).toBeGreaterThan(300);
	expect(before).toBeLessThan(await page.evaluate(() => document.documentElement.scrollHeight - window.innerHeight - 200));
	if (phone) await target.tap();
	else await target.click();
	await expect(page).toHaveURL(/\/watch\//);
	const y = await page.evaluate(() => (window as unknown as { leftAt: number }).leftAt);
	expect(Math.abs(y - before)).toBeLessThan(150);
	// Desktop leaves with the player's own Back (Escape), the others with the browser's.
	if (phone || path !== '/') await page.goBack();
	else await page.keyboard.press('Escape');
	await expect(page).toHaveURL(new RegExp(`${path}$`));
	await expect.poll(() => scrollY(page), { timeout: 3000 }).toBe(y);
	// Stays put while anything late (focus restore, shelves loading) settles.
	for (let i = 0; i < 8; i++) { await page.waitForTimeout(500); expect(await scrollY(page)).toBe(y); }
	// Browsing up by hand right after coming back is never pulled back down.
	await page.goForward();
	await expect(page).toHaveURL(/\/watch\//);
	await page.goBack();
	await expect.poll(() => scrollY(page), { timeout: 3000 }).toBe(y);
	if (phone) await page.evaluate(() => window.scrollBy(0, -250));
	else await page.mouse.wheel(0, -250);
	await expect.poll(() => scrollY(page)).toBeLessThan(y - 100);
	const up = await scrollY(page);
	await page.waitForTimeout(3500);
	expect(await scrollY(page)).toBe(up);
}

test('screen: back from a video keeps the scroll position (desktop)', async ({ page, request }) => {
	await seed(request);
	await page.setViewportSize({ width: 1200, height: 500 });
	await roundTrip(page, '/', '**/api/feed/home*');
});

test('category page: back from a video keeps the scroll position', async ({ page, request }) => {
	const id = await seed(request);
	await page.setViewportSize({ width: 1200, height: 500 });
	await roundTrip(page, `/category/${id}`, '**/api/feed/category/**');
});

test.describe('phone', () => {
	test.use({ viewport: { width: 390, height: 760 }, hasTouch: true, isMobile: true });
	test('screen: back from a video keeps the scroll position (phone)', async ({ page, request }) => {
		await seed(request);
		await roundTrip(page, '/', '**/api/feed/home*', true);
	});
});
