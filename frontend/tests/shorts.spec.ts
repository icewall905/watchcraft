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

// Exercise the real component lifecycle with a deterministic player. The manifest-abort
// tests cannot distinguish successful attachment from the page silently dropping its video.
async function playerFixture(page: import('@playwright/test').Page, options: { failFirst?: boolean; attachDelay?: number; loadDelay?: number; holdFirst?: boolean; holdAttach?: boolean } = {}) {
	await page.addInitScript((options) => {
		(window as any).__shorts = { events: [], active: 0, maxActive: 0, loads: 0, options };
		HTMLMediaElement.prototype.play = async function () { (window as any).__shorts.events.push(['play']); };
	}, options);
	await page.route('**/_app/immutable/chunks/*.js', async (route) => {
		const response = await route.fetch();
		const source = await response.text();
		if (!source.includes('shaka.util.DataViewWriter')) return route.fulfill({ response });
		return route.fulfill({
		contentType: 'application/javascript', body: `
		const state = window.__shorts;
		const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
		class Player {
		 static isBrowserSupported() { return true; }
		 async attach(el) {
		  state.events.push(['attach', el.isConnected]);
		  state.active++; state.maxActive = Math.max(state.maxActive, state.active);
		  if (state.options.holdAttach) await new Promise(resolve => {state.releaseAttach = resolve;});
		  await wait(state.options.attachDelay || 0);
		 }
		 getNetworkingEngine() { return {registerRequestFilter: filter => {
		  for (const uri of ['/api/video/short000001/manifest.mpd', 'https://media.example/manifest.mpd', '/api/video/short000001/segment']) {
		   const request = {uris: [uri], headers: {}}; filter(0, request); state.events.push(['headers', {uri, headers: request.headers}]);
		  }
		 }}; }
		 configure() {}
		 async load(url) {
		  const attempt = ++state.loads;
		  state.events.push(['load', url]);
		  if (attempt === 1 && state.options.holdFirst) await new Promise(resolve => {state.releaseFirst = resolve;});
		  await wait(state.options.loadDelay || 0);
		  if (state.options.failFirst && attempt === 1) throw new Error('Fixture manifest failed');
		  state.events.push(['loaded', url]);
		 }
		 async destroy() { await wait(20); state.active--; state.events.push(['destroy']); }
		}
		const stub = {Player, polyfill: {installAll() {}}};
		export default stub; export const s = {default: stub};`
	});
	});
}

async function playerState(page: import('@playwright/test').Page) {
	return page.evaluate(() => (window as any).__shorts as { events: [string, unknown?][]; active: number; maxActive: number; loads: number });
}

test('a failed manifest keeps the video mounted and retry loads the current short', async ({ page }) => {
	await playerFixture(page, { failFirst: true });
	await page.route('**/api/shorts*', route => route.fulfill({ json: { items: SHORTS, next_offset: null } }));
	await freshPage(page, '/shorts');
	await expect(page.getByRole('alert')).toContainText('Fixture manifest failed');
	await expect(page.locator('video')).toHaveCount(1);
	await page.getByRole('button', { name: 'Retry playback' }).click();
	await expect(page.getByRole('alert')).toHaveCount(0);
	await expect.poll(async () => (await playerState(page)).events.filter(e => e[0] === 'play').length).toBe(1);
	const state = await playerState(page);
	expect(state.loads).toBe(2);
	expect(state.maxActive).toBe(1);
	const headers = state.events.filter(e => e[0] === 'headers').map(e => e[1] as { uri: string; headers: Record<string, string> });
	expect(headers.find(e => e.uri.endsWith('/manifest.mpd') && e.uri.startsWith('/'))?.headers['X-Profile-Id']).toBeTruthy();
	expect(headers.filter(e => e.uri.startsWith('https:') || e.uri.endsWith('/segment')).every(e => !e.headers['X-Profile-Id'])).toBe(true);
	expect(state.events.filter(e => e[0] === 'attach').every(e => e[1] === true)).toBe(true);
});

test('rapid navigation serializes attachment and ignores stale load completion', async ({ page }) => {
	await playerFixture(page, { attachDelay: 80, holdFirst: true });
	await page.route('**/api/shorts*', route => route.fulfill({ json: { items: SHORTS, next_offset: null } }));
	await freshPage(page, '/shorts');
	await expect.poll(async () => (await playerState(page)).loads).toBe(1);
	await page.keyboard.press('ArrowDown');
	await expect(page.getByRole('heading', { name: 'Second short' })).toBeVisible();
	await expect.poll(async () => (await playerState(page)).events.filter(e => e[0] === 'play').length).toBe(1);
	await page.evaluate(() => (window as any).__shorts.releaseFirst());
	await expect.poll(async () => (await playerState(page)).events.filter(e => e[0] === 'loaded').length).toBe(2);
	let state = await playerState(page);
	expect(state.maxActive).toBe(1);
	expect(state.events.filter(e => e[0] === 'load').at(-1)?.[1]).toContain('short000002');
	await page.getByRole('link', { name: 'Leave shorts' }).click();
	await expect.poll(async () => (await playerState(page)).active).toBe(0);
	state = await playerState(page);
	expect(state.events.filter(e => e[0] === 'play')).toHaveLength(1);
});

test('leaving during attachment destroys the pending player without loading or playing it', async ({ page }) => {
	await playerFixture(page, { holdAttach: true });
	await page.route('**/api/shorts*', route => route.fulfill({ json: { items: SHORTS, next_offset: null } }));
	await freshPage(page, '/shorts');
	await expect.poll(async () => (await playerState(page)).active).toBe(1);
	await page.getByRole('link', { name: 'Leave shorts' }).click();
	await expect(page.getByRole('region', { name: 'Shorts feed' })).toHaveCount(0);
	await page.evaluate(() => (window as any).__shorts.releaseAttach());
	await expect.poll(async () => (await playerState(page)).active).toBe(0);
	const state = await playerState(page);
	expect(state.loads).toBe(0);
	expect(state.events.filter(e => e[0] === 'play')).toHaveLength(0);
});

test('a delayed dislike removes its original item after a swipe, and removing the last destroys playback', async ({ page }) => {
	await playerFixture(page);
	let release!: () => void;
	const gate = new Promise<void>(resolve => { release = resolve; });
	let requested = false;
	await page.route('**/api/shorts*', route => route.fulfill({ json: { items: SHORTS, next_offset: null } }));
	await page.route('**/api/feedback', async route => { requested = true; await gate; await route.fulfill({ json: { id: 1 } }); });
	await freshPage(page, '/shorts');
	await expect.poll(async () => (await playerState(page)).events.some(e => e[0] === 'play')).toBe(true);
	await page.getByRole('button', { name: 'Not for me' }).click();
	await expect.poll(() => requested).toBe(true);
	await page.keyboard.press('ArrowDown');
	release();
	await expect(page.getByRole('heading', { name: 'Second short' })).toBeVisible();
	await expect(page.getByRole('button', { name: 'Not for me' })).toBeEnabled();
	await page.keyboard.press('ArrowUp');
	await expect(page.getByRole('heading', { name: 'Second short' })).toBeVisible();
	await page.getByRole('button', { name: 'Not for me' }).click();
	await expect(page.getByRole('heading', { name: 'Shorts are hidden' })).toBeVisible();
	await expect.poll(async () => (await playerState(page)).active).toBe(0);
});

test('disliking the last loaded short fetches a replacement when more remain', async ({ page }) => {
	await playerFixture(page);
	let disliked = false;
	await page.route('**/api/shorts*', route => route.fulfill({ json: { items: [SHORTS[disliked ? 1 : 0]], next_offset: disliked ? null : 1 } }));
	await page.route('**/api/feedback', route => { disliked = true; return route.fulfill({ json: { id: 1 } }); });
	await freshPage(page, '/shorts');
	await expect(page.getByRole('heading', { name: 'First short' })).toBeVisible();
	await page.getByRole('button', { name: 'Not for me' }).click();
	await expect(page.getByRole('heading', { name: 'Second short' })).toBeVisible();
	await expect.poll(async () => (await playerState(page)).events.filter(e => e[0] === 'load').at(-1)?.[1]).toContain('short000002');
});

test('advancing past the loaded boundary fetches and plays the next short', async ({ page }) => {
	await playerFixture(page);
	let requests = 0;
	await page.route('**/api/shorts*', route => {
		const url = new URL(route.request().url());
		if (!url.searchParams.get('exclude_ids')) return route.fulfill({ json: { items: [SHORTS[0]], next_offset: 1 } });
		requests++;
		expect(url.searchParams.get('offset')).toBe('0');
		expect(url.searchParams.get('exclude_ids')).toContain('short000001');
		return route.fulfill({ json: { items: [SHORTS[1]], next_offset: null } });
	});
	await freshPage(page, '/shorts');
	await expect(page.getByRole('heading', { name: 'First short' })).toBeVisible();
	await page.keyboard.press('ArrowDown');
	await expect(page.getByRole('heading', { name: 'Second short' })).toBeVisible();
	await expect.poll(async () => (await playerState(page)).events.filter(e => e[0] === 'load').at(-1)?.[1]).toContain('short000002');
	expect(requests).toBe(1);
});

test('the shorts feed swipes between accepted shorts and records a dislike', async ({ page }) => {
	let disliked: { video_id?: string; rating?: number } | null = null;
	await page.route('**/api/shorts*', (route) =>
		route.fulfill({ json: { items: SHORTS, total: SHORTS.length, next_offset: null } }));
	// Network failures must leave the navigation controls available.
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
