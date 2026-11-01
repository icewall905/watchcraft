import { expect, test, type Page } from '@playwright/test';
import { freshPage, resetBackend } from './helpers';
const feeds = [{ id: 5, name: 'Science clips', emoji: '🔬', kind: 'shorts', enabled: true, ready_count: 2, pending_count: 3, error_count: 1 }, { id: 6, name: 'Cooking clips', emoji: '🍳', kind: 'shorts', enabled: false, ready_count: 1, pending_count: 0, error_count: 0 }];
const item = (id: string, cat = 5) => ({ id: 1, category_id: cat, status: 'accepted', score: 90, summary: 'A local clip', video: { id, title: id, channel_name: 'Fixture', duration_s: 30, is_short: true, offline: true } });
async function fixture(page: Page) {
	await page.addInitScript(() => { HTMLMediaElement.prototype.play = async function () { }; HTMLMediaElement.prototype.load = function () { }; });
	await page.route('**/api/shorts/feeds', r => r.fulfill({ json: feeds }));
	await page.route(/\/api\/shorts\?/, r => { const url = new URL(r.request().url()); const cat = Number(url.searchParams.get('category_id')); return r.fulfill({ json: { items: cat === 5 ? [item('short000001'), item('short000002')] : [item('short000003', 6)], next_offset: null } }); });
	await page.route('**/api/history', r => r.fulfill({ json: { ok: true } }));
}
test.beforeAll(async ({ request }) => resetBackend(request));
test('opens a feed picker, selects isolated local files and clears playback on return', async ({ page }) => {
	await fixture(page);
	const forbidden: string[] = [];
	page.on('request', r => { if (/manifest\.mpd|\/proxy|googlevideo/.test(r.url()))
		forbidden.push(r.url()); });
	await freshPage(page, '/shorts');
	await expect(page.getByRole('heading', { name: 'Choose a feed' })).toBeVisible();
	await expect(page.locator('video')).toHaveCount(0);
	await expect(page.getByRole('button', { name: /Science clips/ })).toContainText('2 ready locally');
	await page.getByRole('button', { name: /Science clips/ }).click();
	await expect(page.locator('video')).toHaveAttribute('src', '/api/video/short000001/file');
	await page.keyboard.press('ArrowDown');
	await expect(page.locator('video')).toHaveAttribute('src', '/api/video/short000002/file');
	await page.getByRole('button', { name: '← Feeds' }).click();
	await expect(page.locator('video')).toHaveCount(0);
	await page.getByRole('button', { name: /Cooking clips/ }).click();
	await expect(page.locator('video')).toHaveAttribute('src', '/api/video/short000003/file');
	expect(forbidden).toEqual([]);
});
test('empty or failed local availability never falls back to streaming', async ({ page }) => {
	await fixture(page);
	await page.route(/\/api\/shorts\?/, r => r.fulfill({ json: { items: [], next_offset: null } }));
	await freshPage(page, '/shorts');
	await page.getByRole('button', { name: /Science clips/ }).click();
	await expect(page.getByText('No local clips ready yet.')).toBeVisible();
	await expect(page.locator('video')).toHaveCount(0);
	await expect(page.getByText(/3 waiting for local files; 1 download errors/)).toBeVisible();
});
test('missing local files show an availability action without remote fallback', async ({ page }) => {
	await fixture(page);
	await freshPage(page, '/shorts');
	await page.getByRole('button', { name: /Science clips/ }).click();
	await page.locator('video').dispatchEvent('error');
	await expect(page.getByRole('alert')).toContainText('local file is unavailable');
	await expect(page.getByRole('button', { name: 'Refresh availability' })).toBeVisible();
});
test('late feed response cannot open playback after returning to feed picker', async ({ page }) => {
	await fixture(page);
	let release!: () => void;
	const gate = new Promise<void>(r => release = r);
	await page.route(/\/api\/shorts\?/, async (r) => { await gate; await r.fulfill({ json: { items: [item('short000001')], next_offset: null } }); });
	await freshPage(page, '/shorts');
	await page.getByRole('button', { name: /Science clips/ }).click();
	await page.getByRole('button', { name: '← Feeds' }).click();
	release();
	await expect(page.getByRole('heading', { name: 'Choose a feed' })).toBeVisible();
	await expect(page.locator('video')).toHaveCount(0);
});
test('a delayed dislike preserves the clip selected after swiping', async ({ page }) => {
	await fixture(page);
	let release!: () => void;
	const gate = new Promise<void>(r => release = r);
	await page.route('**/api/feedback', async (r) => { await gate; await r.fulfill({ json: { ok: true } }); });
	await freshPage(page, '/shorts');
	await page.getByRole('button', { name: /Science clips/ }).click();
	await page.getByRole('button', { name: 'Not for me' }).click();
	await page.keyboard.press('ArrowDown');
	await expect(page.locator('video')).toHaveAttribute('src', '/api/video/short000002/file');
	release();
	await expect(page.getByRole('button', { name: 'Not for me' })).toBeEnabled();
	await expect(page.getByRole('heading', { name: 'short000002' })).toBeVisible();
	await expect(page.locator('video')).toHaveAttribute('src', '/api/video/short000002/file');
});
test('refresh invalidates pending pagination and loads availability anew', async ({ page }) => {
	await fixture(page);
	let calls = 0, release!: () => void;
	const gate = new Promise<void>(r => release = r);
	await page.route(/\/api\/shorts\?/, async (r) => { calls++; if (calls === 2) {
		await gate;
		await r.fulfill({ json: { items: [item('short000002')], next_offset: null } });
	}
	else
		await r.fulfill({ json: { items: [item(calls > 2 ? 'short000003' : 'short000001')], next_offset: 1 } }); });
	await freshPage(page, '/shorts');
	await page.getByRole('button', { name: /Science clips/ }).click();
	await page.keyboard.press('ArrowDown');
	await expect.poll(() => calls).toBe(2);
	await page.locator('video').dispatchEvent('error');
	await page.getByRole('button', { name: 'Refresh availability' }).click();
	await expect(page.locator('video')).toHaveAttribute('src', '/api/video/short000003/file');
	release();
	await expect(page.getByRole('heading', { name: 'short000003' })).toBeVisible();
	await expect(page.locator('video')).toHaveAttribute('src', '/api/video/short000003/file');
});

test('phone swipes advance through local files and pagination excludes loaded clips', async ({ page }) => {
 await fixture(page);
 const excluded: string[] = [];
 await page.route(/\/api\/shorts\?/, r => {
  const ids = new URL(r.request().url()).searchParams.get('exclude_ids') || '';
  excluded.push(ids);
  return r.fulfill({json: {items: [item(ids ? 'short000002' : 'short000001')], next_offset: ids ? null : 1}});
 });
 await freshPage(page, '/shorts');
 await page.getByRole('button', {name: /Science clips/}).click();
 await expect(page.locator('video')).toHaveAttribute('src', '/api/video/short000001/file');
 await page.locator('video').dispatchEvent('touchstart', {touches: [{clientX: 200, clientY: 600}]});
 await page.locator('video').dispatchEvent('touchend', {changedTouches: [{clientX: 205, clientY: 200}]});
 await expect(page.locator('video')).toHaveAttribute('src', '/api/video/short000002/file');
 expect(excluded).toEqual(['', 'short000001']);
 await page.locator('video').dispatchEvent('touchstart', {touches: [{clientX: 200, clientY: 200}]});
 await page.locator('video').dispatchEvent('touchend', {changedTouches: [{clientX: 200, clientY: 600}]});
 await expect(page.locator('video')).toHaveAttribute('src', '/api/video/short000001/file');
});
