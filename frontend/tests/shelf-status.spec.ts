import { test, expect } from '@playwright/test';
import { freshPage } from './helpers';

const shelf = () => ({
	as_of: 1793865600,
	articles: { ready: 74, target: 100, fresh_24h: 12, publishers: 31, stories: 52, needs_refill: true, reason: 'Some interests could use more recent coverage.', interests: [
		{ id: 1, name: 'Climate and energy', ready: 18, target: 25, fresh_24h: 4, publishers: 9, needs_refill: true, last_success: 1793862000, next_run: 1793869200 }
	] },
	videos: { ready: 46, target: 60, pending: 2, failed: 1, fresh_7d: 8, creators: 17, needs_refill: true, reason: 'A few more videos would keep your shelf varied.', categories: [
		{ id: 2, name: 'Woodworking', emoji: '🪵', kind: 'watch', ready: 11, target: 15, fresh_7d: 3, creators: 6, pending: 2, needs_refill: true }
	] },
	maintenance: { last_checked_at: 1793865000, next_check_at: 1793869200, collectors_queued: [1], status: 'working' }
});

const edition = { day: '2026-11-05', curated: false, sections: [], intro: '', intro_refs: [], set_aside: 0 };
async function mocks(page: import('@playwright/test').Page, status: number = 200) {
	await page.route(/\/api\/hub\/shelf(?:\?.*)?$/, route => route.fulfill({ status, json: status === 200 ? shelf() : { detail: 'Unavailable' } }));
	await page.route(/\/api\/hub\/today(?:\?.*)?$/, route => route.fulfill({ json: edition }));
}

test('Today shows article and video shelf freshness with links and detail', async ({ page }) => {
	await mocks(page);
	await page.setViewportSize({ width: 390, height: 844 });
	await freshPage(page, '/today');
	const panel = page.getByRole('region', { name: 'Your shelf' });
	// Folded by default: a one-line glance, details on demand.
	await expect(panel.getByText('74 articles · 46 videos')).toBeVisible();
	await expect(panel.getByText('12 new in 24 hours · 31 publishers')).toBeHidden();
	await panel.getByRole('button', { name: 'Your shelf' }).click();
	await expect(panel.getByRole('button', { name: 'Your shelf' })).toHaveAttribute('aria-expanded', 'true');
	await expect(panel.getByText('74', { exact: true })).toBeVisible();
	await expect(panel.getByText('12 new in 24 hours · 31 publishers')).toBeVisible();
	await expect(panel.getByText('46', { exact: true })).toBeVisible();
	await expect(panel.getByText('8 published or found this week · 17 creators')).toBeVisible();
	await expect(panel.getByText('Refilling')).toBeVisible();
	await expect(panel.getByText('1 saved files or captions need attention.')).toBeVisible();
	await expect(panel.getByText('2 videos awaiting preparation.')).toBeVisible();
	await expect(panel.getByRole('link', { name: 'Browse' }).first()).toHaveAttribute('href', '/news');
	await expect(panel.getByRole('link', { name: 'Home videos' })).toHaveAttribute('href', '/');
	await panel.getByText('See interests and variety').click();
	await expect(panel.getByText('Climate and energy')).toBeVisible();
	await expect(panel.getByText('🪵 Woodworking')).toBeVisible();
	expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
	await page.screenshot({ path: '/tmp/wc-shelf-390.png', fullPage: true });
	await page.setViewportSize({ width: 1440, height: 900 });
	const box = await panel.boundingBox();
	const heading = await page.getByRole('heading', { level: 1 }).boundingBox();
	expect(box!.x).toBeGreaterThan(heading!.x + 400); // sits beside the masthead on wide screens
	await page.screenshot({ path: '/tmp/wc-shelf-1440.png' });
});

test('shelf request failure stays local and offers a retry', async ({ page }) => {
	await mocks(page, 503);
	await freshPage(page, '/today');
	await expect(page.getByRole('heading', { name: 'Your shelf' })).toBeVisible();
	await page.getByRole('button', { name: 'Your shelf' }).click();
	await expect(page.getByText('The shelf overview is unavailable right now.')).toBeVisible();
	await expect(page.getByText('Your first edition is taking shape')).toBeVisible();
	await expect(page.getByRole('button', { name: 'Try again' })).toHaveCount(1);
});

test('shelf data clears on profile change and an old delayed response cannot return', async ({ page, request }) => {
	const other = await (await request.post('/api/profiles', { data: { name: 'Shelf privacy profile' } })).json();
	let blockOriginal = false;
	let held = false;
	let release: (() => void) | undefined;
	await page.route(/\/api\/hub\/shelf(?:\?.*)?$/, async route => {
		const profileId = route.request().headers()['x-profile-id'];
		if (profileId === String(other.id)) {
			const data = shelf();
			data.articles.ready = 28;
			await route.fulfill({ json: data });
			return;
		}
		if (blockOriginal) {
			held = true;
			await new Promise<void>(resolve => { release = resolve; });
		}
		await route.fulfill({ json: shelf() }).catch(() => {});
	});
	await page.route(/\/api\/hub\/today(?:\?.*)?$/, route => route.fulfill({ json: edition }));
	try {
		await freshPage(page, '/today');
		const panel = page.getByRole('region', { name: 'Your shelf' });
		await panel.getByRole('button', { name: 'Your shelf' }).click();
		await expect(panel.getByText('74', { exact: true })).toBeVisible();
		blockOriginal = true;
		await page.evaluate(() => window.dispatchEvent(new Event('focus')));
		await expect.poll(() => held).toBe(true);
		await page.getByRole('button', { name: 'Switch profile' }).click();
		await page.getByRole('button', { name: 'Shelf privacy profile', exact: true }).click();
		await expect(panel.getByText('74', { exact: true })).toHaveCount(0);
		await expect(panel.getByText('28', { exact: true })).toBeVisible();
		release?.();
		await expect(panel.getByText('74', { exact: true })).toHaveCount(0);
	} finally {
		release?.();
		await request.delete(`/api/profiles/${other.id}`);
	}
});
