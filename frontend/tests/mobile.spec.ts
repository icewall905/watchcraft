import { test, expect } from '@playwright/test';
import { freshPage } from './helpers';

// Phone layout: a bottom bar instead of the sidebar, no sideways scrolling, and targets
// big enough to hit with a thumb.

const PHONE = { width: 390, height: 844 };

test.use({ viewport: PHONE });

test('the phone gets a bottom bar and no desktop sidebar', async ({ page }) => {
	await freshPage(page, '/');
	const bar = page.getByRole('navigation', { name: 'Primary' });
	await expect(bar).toBeVisible();
	for (const name of ['TV', 'Discover', 'Library', 'Alerts']) {
		await expect(bar.getByRole('link', { name, exact: true })).toBeVisible();
	}
	await expect(bar.getByRole('button', { name: 'More' })).toBeVisible();
	// The desktop sidebar is not rendered at this width.
	await expect(page.getByRole('navigation', { name: 'Main', exact: true })).toBeHidden();
});

test('pages do not scroll sideways on a phone', async ({ page }) => {
	for (const url of ['/', '/discover', '/memes', '/library', '/settings']) {
		await freshPage(page, url);
		const overflow = await page.evaluate(() => ({
			scroll: document.documentElement.scrollWidth,
			client: document.documentElement.clientWidth
		}));
		expect(overflow.scroll, `${url} overflows horizontally`).toBeLessThanOrEqual(overflow.client + 1);
	}
});

test('bottom bar targets are big enough to tap', async ({ page }) => {
	await freshPage(page, '/');
	const bar = page.getByRole('navigation', { name: 'Primary' });
	await expect(bar).toBeVisible();
	await expect(bar.getByRole('link', { name: 'TV', exact: true })).toBeVisible();
	const boxes = await bar.locator('a, button').evaluateAll((els) =>
		els.map((el) => {
			const r = el.getBoundingClientRect();
			return { w: Math.round(r.width), h: Math.round(r.height) };
		}));
	expect(boxes.length).toBeGreaterThan(3);
	for (const b of boxes) {
		expect(b.h, 'a bottom bar target is shorter than 40px').toBeGreaterThanOrEqual(40);
		expect(b.w, 'a bottom bar target is narrower than 40px').toBeGreaterThanOrEqual(40);
	}
});

test('the phone keeps the top bar controls reachable', async ({ page }) => {
	await freshPage(page, '/');
	await expect(page.getByRole('button', { name: 'Play on' })).toBeVisible();
	await expect(page.getByRole('button', { name: /Switch profile/ })).toBeVisible();
});
