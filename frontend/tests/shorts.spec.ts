import { expect, test } from '@playwright/test';
import { freshPage, resetBackend } from './helpers';

const video = (id: string, title: string, duration: number) => ({
	id, title, channel_id: 'UC1', channel_name: 'Clips', duration_s: duration, published_at: null,
	view_count: 0, like_count: 0, thumbnail: '', is_short: true, is_live: false
});

const candidate = (id: number, v: ReturnType<typeof video>, score: number) => ({
	id, category_id: 5, status: 'accepted', source: 'youtube', score, summary: `why ${v.title}`,
	reasons: [], red_flags: [], tags: [], error: '', created_at: '', judged_at: null, video: v
});

const SHORTS = [
	candidate(1, video('short000001', 'First short', 30), 91),
	candidate(2, video('short000002', 'Second short', 25), 88)
];

test.beforeAll(async ({ request }) => { await resetBackend(request); });

test('the shorts feed swipes between accepted shorts and records a dislike', async ({ page }) => {
	let disliked: { video_id?: string; rating?: number } | null = null;
	await page.route('**/api/shorts*', (route) =>
		route.fulfill({ json: { items: SHORTS, total: SHORTS.length, next_offset: null } }));
	// No real manifest: the player fails, which is the autoplay-blocked path the overlay covers.
	await page.route('**/api/video/*/manifest.mpd*', (route) => route.abort());
	await page.route('**/api/feedback', async (route) => {
		disliked = route.request().postDataJSON();
		await route.fulfill({ json: { id: 1 } });
	});

	await freshPage(page, '/shorts');
	await expect(page.getByRole('heading', { name: 'First short' })).toBeVisible();

	// Arrow down moves to the next short.
	await page.keyboard.press('ArrowDown');
	await expect(page.getByRole('heading', { name: 'Second short' })).toBeVisible();

	// A dislike goes through the normal feedback path, so the curator learns from it.
	await page.getByRole('button', { name: 'Not for me' }).click();
	await expect.poll(() => disliked).not.toBeNull();
	expect(disliked!.rating).toBe(-1);
	expect(disliked!.video_id).toBe('short000002');
});

test('an empty shorts feed names the setting that is keeping it empty', async ({ page }) => {
	// Shorts are hidden by default, so an empty feed is a setting rather than a bug and must say so.
	await page.route('**/api/shorts*', (route) =>
		route.fulfill({ json: { items: [], total: 0, next_offset: null } }));
	await freshPage(page, '/shorts');
	await expect(page.getByRole('heading', { name: 'Shorts are hidden' })).toBeVisible();
	await expect(page.getByRole('link', { name: 'Open Settings → Extras' })).toBeVisible();
});

/** Dispatch a real touch gesture on the feed, the way a phone would. */
async function swipe(page: import('@playwright/test').Page, fromY: number, toY: number, on?: string) {
	await page.evaluate(([from, to, selector]) => {
		const el = (selector ? document.querySelector(selector) : document.querySelector('.feed')) as HTMLElement;
		const touch = (y: number) =>
			new Touch({ identifier: 1, target: el, clientX: 195, clientY: y } as TouchInit);
		el.dispatchEvent(new TouchEvent('touchstart', {
			touches: [touch(from)], changedTouches: [touch(from)], bubbles: true
		}));
		el.dispatchEvent(new TouchEvent('touchend', {
			touches: [], changedTouches: [touch(to)], bubbles: true
		}));
	}, [fromY, toY, on ?? ''] as const);
}

test.describe('on a phone', () => {
	test.use({ hasTouch: true, viewport: { width: 390, height: 844 } });

	test('a swipe up and down moves between shorts', async ({ page }) => {
		await page.route('**/api/shorts*', (route) =>
			route.fulfill({ json: { items: SHORTS, total: SHORTS.length, next_offset: null } }));
		await page.route('**/api/video/*/manifest.mpd*', (route) => route.abort());
		await freshPage(page, '/shorts');
		await expect(page.getByRole('heading', { name: 'First short' })).toBeVisible();

		await swipe(page, 700, 300); // up
		await expect(page.getByRole('heading', { name: 'Second short' })).toBeVisible();

		await swipe(page, 300, 700); // down
		await expect(page.getByRole('heading', { name: 'First short' })).toBeVisible();
	});

	test('a sideways drag is not a swipe', async ({ page }) => {
		await page.route('**/api/shorts*', (route) =>
			route.fulfill({ json: { items: SHORTS, total: SHORTS.length, next_offset: null } }));
		await page.route('**/api/video/*/manifest.mpd*', (route) => route.abort());
		await freshPage(page, '/shorts');
		await expect(page.getByRole('heading', { name: 'First short' })).toBeVisible();
		await page.evaluate(() => {
			const el = document.querySelector('.feed') as HTMLElement;
			const touch = (x: number) => new Touch({ identifier: 1, target: el, clientX: x, clientY: 400 } as TouchInit);
			el.dispatchEvent(new TouchEvent('touchstart', { touches: [touch(100)], changedTouches: [touch(100)], bubbles: true }));
			el.dispatchEvent(new TouchEvent('touchend', { touches: [], changedTouches: [touch(350)], bubbles: true }));
		});
		await expect(page.getByRole('heading', { name: 'First short' })).toBeVisible();
	});

	test('a touch that starts on a control does not swipe', async ({ page }) => {
		await page.route('**/api/shorts*', (route) =>
			route.fulfill({ json: { items: SHORTS, total: SHORTS.length, next_offset: null } }));
		await page.route('**/api/video/*/manifest.mpd*', (route) => route.abort());
		await freshPage(page, '/shorts');
		await expect(page.getByRole('heading', { name: 'First short' })).toBeVisible();
		await swipe(page, 700, 300, '[aria-label="Not for me"]');
		await expect(page.getByRole('heading', { name: 'First short' })).toBeVisible();
	});
});
