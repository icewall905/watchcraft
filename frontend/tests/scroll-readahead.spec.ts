import { expect, test, type Page } from '@playwright/test';
import { freshPage, resetBackend } from './helpers';

// The Scroll: read-ahead (captions, file, next page) and the picker/player look on desktop, phone and remote.
const shots = process.env.SHOTS_DIR ?? 'test-results';
const feeds = [
	{ id: 5, name: 'Science clips', emoji: '🔬', color: '#6cc6ff', kind: 'shorts', enabled: true, ready_count: 42, pending_count: 8, error_count: 1, caption_ready_count: 40 },
	{ id: 6, name: 'Cooking clips', emoji: '🍳', color: '#ffb547', kind: 'shorts', enabled: true, ready_count: 17, pending_count: 0, error_count: 0, caption_ready_count: 17 },
	{ id: 7, name: 'Woodworking', emoji: '🪚', color: '#3ddc97', kind: 'shorts', enabled: false, ready_count: 0, pending_count: 12, error_count: 0, caption_ready_count: 0 }
];
const track = (id: string) => ({ enabled: true, status: 'ready', progress: 100, error: '', track: { url: `/api/video/${id}/subtitles/generated.vtt` } });
const item = (id: string, listed = true) => ({ id: 1, category_id: 5, status: 'accepted', score: 92, summary: 'How tardigrades survive the vacuum of space, explained in under a minute.',
	video: { id, title: `Tiny animals that survive space (${id})`, channel_name: 'Fixture Science', duration_s: 30, is_short: true, offline: true, ...(listed ? { subtitles: track(id) } : {}) } });
const poster = `<svg xmlns="http://www.w3.org/2000/svg" width="360" height="640"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#2b5876"/><stop offset="1" stop-color="#4e4376"/></linearGradient></defs><rect width="360" height="640" fill="url(#g)"/><circle cx="180" cy="300" r="90" fill="#6cc6ff" opacity=".45"/></svg>`;

async function fixture(page: Page, listed = true) {
	const status: string[] = [];
	await page.addInitScript(() => { HTMLMediaElement.prototype.play = async function () { }; HTMLMediaElement.prototype.load = function () { }; });
	await page.route('**/api/thumb/**', r => r.fulfill({ contentType: 'image/svg+xml', body: poster }));
	await page.route('**/api/video/*/subtitles', r => { const id = r.request().url().split('/').at(-2)!; status.push(id); return r.fulfill({ json: track(id) }); });
	await page.route('**/api/video/*/subtitles/generated.vtt', r => r.fulfill({ contentType: 'text/vtt', body: 'WEBVTT\n\n00:00:00.000 --> 00:00:30.000\nThey can dry out and wait for water\n' }));
	await page.route('**/api/shorts/feeds', r => r.fulfill({ json: feeds }));
	await page.route(/\/api\/shorts\?/, r => {
		const ex = new URL(r.request().url()).searchParams.get('exclude_ids');
		return r.fulfill({ json: ex ? { items: [item('short000003', listed)], next_offset: null } : { items: [item('short000001', listed), item('short000002', listed)], next_offset: 2 } });
	});
	await page.route('**/api/history', r => r.fulfill({ json: { ok: true } }));
	// Fixture ids have no file; a request left pending keeps the stage showing its poster instead of an error.
	await page.route('**/api/video/*/file', () => { });
	return status;
}
test.beforeAll(async ({ request }) => resetBackend(request));

test('listed caption tracks skip status calls and the next clip and page are read ahead', async ({ page }) => {
	const status = await fixture(page);
	const pages: string[] = [];
	page.on('request', r => { if (/\/api\/shorts\?/.test(r.url())) pages.push(new URL(r.url()).searchParams.get('exclude_ids') ?? ''); });
	await freshPage(page, '/shorts');
	await page.getByRole('button', { name: /Science clips/ }).click();
	await expect(page.locator('video')).toHaveAttribute('src', '/api/video/short000001/file');
	await expect(page.getByLabel('Subtitles')).toHaveText('They can dry out and wait for water');
	// The next page is requested while the first clip plays, not on the last swipe.
	await expect.poll(() => pages).toEqual(['', 'short000001,short000002']);
	await page.keyboard.press('ArrowDown');
	await expect(page.locator('video')).toHaveAttribute('src', '/api/video/short000002/file');
	await page.keyboard.press('ArrowDown');
	await expect(page.locator('video')).toHaveAttribute('src', '/api/video/short000003/file');
	expect(status).toEqual([]);
});

test('without listed tracks the next clip captions are prepared before the swipe', async ({ page }) => {
	const status = await fixture(page, false);
	await freshPage(page, '/shorts');
	await page.getByRole('button', { name: /Science clips/ }).click();
	await expect(page.getByLabel('Subtitles')).toHaveText('They can dry out and wait for water');
	await expect.poll(() => status).toContain('short000002');
	await page.keyboard.press('ArrowDown');
	await expect(page.locator('video')).toHaveAttribute('src', '/api/video/short000002/file');
	expect(status.filter(id => id === 'short000002')).toHaveLength(1);
});

test('desktop picker and player look', async ({ page }) => {
	await fixture(page);
	await freshPage(page, '/shorts');
	await expect(page.getByRole('button', { name: /Science clips/ })).toContainText('42 stored locally');
	await page.screenshot({ path: `${shots}/scroll-picker-desktop.png` });
	await page.getByRole('button', { name: /Science clips/ }).click();
	await expect(page.getByLabel('Subtitles')).toBeVisible();
	await page.locator('video').click();
	await expect(page.getByRole('heading', { name: /short000001/ })).toBeVisible();
	await page.waitForTimeout(600);
	await page.screenshot({ path: `${shots}/scroll-player-desktop.png` });
});

test('remote navigation reaches a feed card and starts it', async ({ page }) => {
	await fixture(page);
	await freshPage(page, '/shorts');
	await expect(page.getByRole('button', { name: /Science clips/ })).toBeVisible();
	await page.getByRole('button', { name: /Science clips/ }).focus();
	await page.keyboard.press('ArrowRight');
	await page.keyboard.press('ArrowLeft');
	await expect(page.getByRole('button', { name: /Science clips/ })).toBeFocused();
	await page.screenshot({ path: `${shots}/scroll-picker-tv-focus.png` });
	await page.keyboard.press('Enter');
	await expect(page.locator('video')).toHaveAttribute('src', '/api/video/short000001/file');
});

test.describe('phone', () => {
	test.use({ hasTouch: true, viewport: { width: 390, height: 844 } });
	test('phone picker and player look', async ({ page }) => {
		await fixture(page);
		await freshPage(page, '/shorts');
		await expect(page.getByRole('button', { name: /Cooking clips/ })).toContainText('17 stored locally');
		await page.screenshot({ path: `${shots}/scroll-picker-phone.png` });
		await page.getByRole('button', { name: /Science clips/ }).click();
		await expect(page.getByLabel('Subtitles')).toBeVisible();
		await page.waitForTimeout(600);
		await page.screenshot({ path: `${shots}/scroll-player-phone.png` });
		await page.locator('video').tap();
		const box = await page.getByRole('button', { name: 'Next clip' }).boundingBox();
		expect(box!.x + box!.width).toBeLessThanOrEqual(390);
	});
});
