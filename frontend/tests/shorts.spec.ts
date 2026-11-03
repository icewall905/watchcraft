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
test('empty profile can switch to its populated profile and start a feed with one click', async ({ page, request }) => {
 const owner = await (await request.post('/api/profiles', { data: { name: 'Evening Shorts' } })).json();
 const first = (await (await request.get('/api/profiles')).json())[0];
 await fixture(page);
 await page.route('**/api/shorts/feeds', r => r.fulfill({ json: r.request().headers()['x-profile-id'] === String(owner.id) ? feeds : [] }));
 await freshPage(page, '/shorts');
 await expect(page.getByText(`No Shorts feeds for ${first.name} yet.`)).toBeVisible();
 await page.getByRole('group', { name: 'Choose Shorts profile' }).getByRole('button', { name: /Evening Shorts/ }).click();
 const play = page.getByRole('button', { name: /Science clips/ });
 await expect(play).toContainText('▶ Play');
 await expect(play).toContainText('2 ready locally');
 await page.screenshot({ path: '/tmp/watchcraft-shorts-profile-picker.png', fullPage: true });
 await play.click();
 await expect(page.locator('video')).toHaveAttribute('src', '/api/video/short000001/file');
 await page.getByRole('button', { name: '← Feeds' }).click();
 await page.getByRole('group', { name: 'Choose Shorts profile' }).getByRole('button', { name: first.name, exact: false }).click();
 await expect(page.getByText(`No Shorts feeds for ${first.name} yet.`)).toBeVisible();
 await expect(page.getByRole('button', { name: /Science clips/ })).toHaveCount(0);
});

test('returning to the picker refreshes newly created feeds without navigation', async ({ page }) => {
 await fixture(page);
 let available = false;
 await page.route('**/api/shorts/feeds', r => r.fulfill({ json: available ? feeds : [] }));
 await freshPage(page, '/shorts');
 await expect(page.getByText(/No Shorts feeds for/)).toBeVisible();
 available = true;
 await page.evaluate(() => window.dispatchEvent(new Event('focus')));
 await expect(page.getByRole('button', { name: /Science clips/ })).toBeVisible();
 await page.getByRole('button', { name: /Science clips/ }).click();
 await expect(page.locator('video')).toHaveAttribute('src', '/api/video/short000001/file');
});

test('late background refresh cannot overwrite a newer list or interrupt playback', async ({ page }) => {
 await fixture(page);
 await freshPage(page, '/shorts');
 await expect(page.getByRole('button', { name: /Science clips/ })).toBeVisible();
 let release!: () => void, started!: () => void;
 const gate = new Promise<void>(r => release = r), requestStarted = new Promise<void>(r => started = r);
 let delayed = true;
 await page.route('**/api/shorts/feeds', async r => {
  if (delayed) { delayed = false; started(); await gate; await r.fulfill({ json: [] }); }
  else await r.fulfill({ json: feeds });
 });
 await page.evaluate(() => window.dispatchEvent(new Event('focus')));
 await requestStarted;
 await page.getByRole('button', { name: 'Refresh feeds', exact: true }).click();
 await expect(page.getByRole('button', { name: /Science clips/ })).toBeVisible();
 await page.getByRole('button', { name: /Science clips/ }).click();
 release();
 await expect(page.locator('video')).toHaveAttribute('src', '/api/video/short000001/file');
 await expect(page.getByRole('heading', { name: 'Choose a feed' })).toHaveCount(0);
});

test('locked profile feeds remain hidden until PIN unlock and cancel retains the original profile', async ({ page, request }) => {
 const owner = await (await request.post('/api/profiles', { data: { name: 'Locked Shorts', pin: '2468' } })).json();
 const first = (await (await request.get('/api/profiles')).json())[0];
 await request.post(`/api/profiles/${owner.id}/lock`);
 await fixture(page);
 const queried: string[] = [];
 await page.route('**/api/shorts/feeds', r => {
  const id = r.request().headers()['x-profile-id']; queried.push(id);
  return r.fulfill({ json: id === String(owner.id) ? feeds : [] });
 });
 await freshPage(page, '/shorts');
 await page.getByRole('group', { name: 'Choose Shorts profile' }).getByRole('button', { name: /Locked Shorts/ }).click();
 await expect(page.getByRole('dialog', { name: 'Unlock Locked Shorts', exact: true })).toBeVisible();
 expect(queried).not.toContain(String(owner.id));
 await page.getByRole('button', { name: 'Cancel', exact: true }).click();
 await expect(page.getByText(`No Shorts feeds for ${first.name} yet.`)).toBeVisible();
 await page.getByRole('group', { name: 'Choose Shorts profile' }).getByRole('button', { name: /Locked Shorts/ }).click();
 await page.getByLabel('Profile PIN', { exact: true }).fill('2468');
 await page.getByRole('button', { name: 'Unlock profile', exact: true }).click();
 await expect(page.getByRole('button', { name: /Science clips/ })).toBeVisible();
 expect(queried).toContain(String(owner.id));
});

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
test('the description stays hidden until the screen is tapped and hides again on the next clip', async ({ page }) => {
	await fixture(page);
	await freshPage(page, '/shorts');
	await page.getByRole('button', { name: /Science clips/ }).click();
	const first = page.getByRole('heading', { name: 'short000001' });
	await expect(first).toBeHidden();
	await page.locator('video').click();
	await expect(first).toBeVisible();
	await expect(page.getByText('A local clip')).toBeVisible();
	await page.locator('video').click();
	await expect(first).toBeHidden();
	await page.locator('video').click();
	await page.keyboard.press('ArrowDown');
	await expect(page.locator('video')).toHaveAttribute('src', '/api/video/short000002/file');
	await expect(page.getByRole('heading', { name: 'short000002' })).toBeHidden();
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
	await page.locator('video').click();
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
	await page.locator('video').click();
	await expect(page.getByRole('heading', { name: 'short000003' })).toBeVisible();
	await expect(page.locator('video')).toHaveAttribute('src', '/api/video/short000003/file');
});

test.describe('phone local playback', () => {
 test.use({ hasTouch: true, viewport: { width: 390, height: 844 } });
 test('phone swipes advance through local files and pagination excludes loaded clips', async ({ page }) => {
 async function swipe(from: number, to: number) {
  await page.locator('video').evaluate((el, [from, to]) => {
   const touch = (y: number) => new Touch({ identifier: 1, target: el, clientX: 200, clientY: y });
   el.dispatchEvent(new TouchEvent('touchstart', { touches: [touch(from)], changedTouches: [touch(from)], bubbles: true }));
   el.dispatchEvent(new TouchEvent('touchend', { touches: [], changedTouches: [touch(to)], bubbles: true }));
  }, [from, to]);
 }
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
 await swipe(600, 200);
 await expect(page.locator('video')).toHaveAttribute('src', '/api/video/short000002/file');
 expect(excluded).toEqual(['', 'short000001']);
 await swipe(200, 600);
 await expect(page.locator('video')).toHaveAttribute('src', '/api/video/short000001/file');
});

});


test('repeat visits show cached feeds during a slow refresh and keep them on failure', async ({ page }) => {
 await fixture(page);
 await freshPage(page, '/shorts');
 await expect(page.getByRole('button', { name: /Science clips/ })).toBeVisible();
 await page.getByRole('link', { name: 'Leave Shorts', exact: true }).click();
 let release!: () => void;
 const gate = new Promise<void>(resolve => release = resolve);
 await page.route('**/api/shorts/feeds', async route => {
  await gate;
  await route.fulfill({ status: 503, json: { detail: 'Feeds temporarily unavailable' } });
 });
 await page.getByRole('link', { name: 'Shorts', exact: true }).first().click();
 await expect(page.getByRole('button', { name: /Science clips/ })).toBeVisible();
 await expect(page.getByText('Loading feeds…', { exact: true })).toHaveCount(0);
 release();
 await expect(page.getByRole('alert')).toBeVisible();
 await expect(page.getByRole('button', { name: /Science clips/ })).toBeVisible();
});

test('manual refresh retains feed choices until fresh counts arrive', async ({ page }) => {
 await fixture(page);
 await freshPage(page, '/shorts');
 await expect(page.getByRole('button', { name: /Science clips/ })).toContainText('2 ready locally');
 let release!: () => void;
 const gate = new Promise<void>(resolve => release = resolve);
 await page.route('**/api/shorts/feeds', async route => {
  await gate;
  await route.fulfill({ json: [{ ...feeds[0], ready_count: 9 }] });
 });
 await page.getByRole('button', { name: 'Refresh feeds', exact: true }).click();
 await expect(page.getByRole('button', { name: /Science clips/ })).toContainText('2 ready locally');
 await expect(page.getByText('Loading feeds…', { exact: true })).toHaveCount(0);
 release();
 await expect(page.getByRole('button', { name: /Science clips/ })).toContainText('9 ready locally');
});

test('a cached populated profile is cleared while another profile loads', async ({ page, request }) => {
 const other = await (await request.post('/api/profiles', { data: { name: 'Private feed isolation' } })).json();
 await fixture(page);
 await freshPage(page, '/shorts');
 await expect(page.getByRole('button', { name: /Science clips/ })).toBeVisible();
 let release!: () => void, started!: () => void;
 const gate = new Promise<void>(resolve => release = resolve);
 const pending = new Promise<void>(resolve => started = resolve);
 await page.route('**/api/shorts/feeds', async route => {
  if (route.request().headers()['x-profile-id'] === String(other.id)) {
   started();
   await gate;
   await route.fulfill({ json: [] });
  } else await route.fulfill({ json: feeds });
 });
 await page.getByRole('group', { name: 'Choose Shorts profile' }).getByRole('button', { name: /Private feed isolation/ }).click();
 await pending;
 await expect(page.getByRole('button', { name: /Science clips/ })).toHaveCount(0);
 await expect(page.getByText('Loading feeds…', { exact: true })).toBeVisible();
 release();
 await expect(page.getByText('No Shorts feeds for Private feed isolation yet.', { exact: true })).toBeVisible();
});


test('focus and polling do not supersede a slow initial feed request', async ({ page }) => {
 await fixture(page);
 let release!: () => void;
 const gate = new Promise<void>(resolve => release = resolve);
 let requests = 0;
 await page.route('**/api/shorts/feeds', async route => {
  requests++;
  await gate;
  await route.fulfill({ json: feeds });
 });
 await page.clock.install();
 await freshPage(page, '/shorts');
 await expect.poll(() => requests).toBe(1);
 await page.evaluate(() => window.dispatchEvent(new Event('focus')));
 await page.clock.fastForward(31000);
 expect(requests).toBe(1);
 release();
 await expect(page.getByRole('button', { name: /Science clips/ })).toBeVisible();
});

test('main navigation remains available while the initial feed request is pending', async ({ page }) => {
 await fixture(page);
 let release!: () => void;
 const gate = new Promise<void>(resolve => release = resolve);
 await page.route('**/api/shorts/feeds', async route => {
  await gate;
  await route.fulfill({ json: feeds });
 });
 await freshPage(page, '/shorts');
 await expect(page.getByText('Loading feeds…', { exact: true })).toBeVisible();
 const back = page.getByRole('link', { name: '← Back to WatchCraft', exact: true });
 await expect(back).toBeVisible();
 await back.click();
 await expect(page).toHaveURL('/');
 release();
});

test('main navigation remains available over blocked playback and playback errors', async ({ page }) => {
 await fixture(page);
 await page.addInitScript(() => { HTMLMediaElement.prototype.play = async function () { throw new Error('Autoplay blocked'); }; });
 await freshPage(page, '/shorts');
 await page.getByRole('button', { name: /Science clips/ }).click();
 await expect(page.getByRole('button', { name: '▶ Tap to play', exact: true })).toBeVisible();
 const back = page.getByRole('link', { name: '← Back to WatchCraft', exact: true });
 await expect(back).toBeVisible();
 await page.locator('video').dispatchEvent('error');
 await expect(page.getByRole('alert')).toBeVisible();
 await back.click();
 await expect(page).toHaveURL('/');
});

test('phone navigation leaves empty availability and does not overlap the picker', async ({ page }) => {
 await page.setViewportSize({ width: 390, height: 600 });
 await fixture(page);
 await page.route(/\/api\/shorts\?/, route => route.fulfill({ json: { items: [], next_offset: null } }));
 await freshPage(page, '/shorts');
 const navigation = page.getByRole('navigation', { name: 'Shorts navigation', exact: true });
 const heading = page.getByRole('heading', { name: 'Choose a feed', exact: true });
 await expect(heading).toBeVisible();
 const navBounds = await navigation.boundingBox(), headingBounds = await heading.boundingBox();
 expect(headingBounds!.y).toBeGreaterThanOrEqual(navBounds!.y + navBounds!.height);
 await page.screenshot({ path: '/tmp/watchcraft-shorts-navigation-phone.png', fullPage: true });
 await page.getByRole('button', { name: /Science clips/ }).click();
 await expect(page.getByText('No local clips ready yet.', { exact: true })).toBeVisible();
 await page.getByRole('button', { name: '← Feeds', exact: true }).click();
 await page.getByRole('button', { name: /Science clips/ }).click();
 await page.getByRole('link', { name: '← Back to WatchCraft', exact: true }).click();
 await expect(page).toHaveURL('/');
});

test('first visit shows profile-owned category choices before slow availability counts', async ({ page, request }) => {
 const profile = (await (await request.get('/api/profiles')).json())[0];
 await fixture(page);
 await page.route('**/api/categories', route => route.fulfill({ json: [
  { ...feeds[0], profile_id: profile.id },
  { ...feeds[1], profile_id: profile.id + 999 }
 ] }));
 let release!: () => void;
 const gate = new Promise<void>(resolve => release = resolve);
 await page.route('**/api/shorts/feeds', async route => {
  await gate;
  await route.fulfill({ json: [{ ...feeds[0], ready_count: 7 }] });
 });
 await freshPage(page, '/shorts');
 const choice = page.getByRole('button', { name: /Science clips/ });
 await expect(choice).toContainText('Checking local availability…');
 await expect(choice).not.toContainText('0 ready locally');
 await expect(page.getByText('Loading feeds…', { exact: true })).toHaveCount(0);
 await expect(page.getByRole('button', { name: /Cooking clips/ })).toHaveCount(0);
 release();
 await expect(choice).toContainText('7 ready locally');
 await expect(choice).not.toContainText('Checking local availability…');
});

test('provisional choices allow playback and authoritative empty feeds remove them', async ({ page, request }) => {
 const profile = (await (await request.get('/api/profiles')).json())[0];
 await fixture(page);
 await page.route('**/api/categories', route => route.fulfill({ json: [{ ...feeds[0], profile_id: profile.id }] }));
 let release!: () => void;
 const gate = new Promise<void>(resolve => release = resolve);
 await page.route('**/api/shorts/feeds', async route => {
  await gate;
  await route.fulfill({ json: [] });
 });
 await freshPage(page, '/shorts');
 await page.getByRole('button', { name: /Science clips/ }).click();
 await expect(page.locator('video')).toHaveAttribute('src', '/api/video/short000001/file');
 release();
 await page.getByRole('button', { name: '← Feeds', exact: true }).click();
 await expect(page.getByText(/No Shorts feeds for/)).toBeVisible();
 await expect(page.getByRole('button', { name: /Science clips/ })).toHaveCount(0);
});
