import { test, expect } from '@playwright/test';
import { freshPage } from './helpers';

test('Meme Radar is in navigation and renders its empty states', async ({ page }) => {
	await freshPage(page, '/');
	const nav = page.getByRole('navigation', { name: 'Main', exact: true });
	await expect(nav.getByRole('link', { name: 'Meme Radar' })).toBeVisible();
	await nav.getByRole('link', { name: 'Meme Radar' }).click();
	await expect(page.getByRole('heading', { name: 'Meme Radar', level: 1 })).toBeVisible();
	for (const t of ['Explore', 'Trends', 'Emerging', 'Formats', 'Saved', 'History', 'Set aside'])
		await expect(page.getByRole('tab', { name: t, exact: true })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'No memes yet' })).toBeVisible();
	await page.getByRole('tab', { name: 'Trends', exact: true }).click();
	await expect(page.getByRole('heading', { name: 'Nothing counted trending yet' })).toBeVisible();
	await page.getByRole('tab', { name: 'Explore', exact: true }).click();
	await expect(page.getByRole('heading', { name: 'No memes yet' })).toBeVisible();
	await expect(page.getByRole('link', { name: 'Create a Meme Tracker' })).toHaveAttribute('href', '/curators/new?template=meme-radar');
});

test('Explore loads facets once and protects sensitive image previews', async ({ page }) => {
	let topicCalls = 0;
	await page.route('**/api/hub/items/999/assets/image', (route) => route.fulfill({ contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1ZkAAAAASUVORK5CYII=', 'base64') }));
	await page.route('**/api/hub/topics', async (route) => { topicCalls++; await route.fulfill({ json: [] }); });
	await page.route('**/api/hub/memes/facets', (route) => route.fulfill({ json: {
		communities: [{ key: '#linux', count: 1 }], languages: [{ key: 'en', count: 1 }]
	} }));
	await page.route('**/api/hub/memes/items?*', (route) => route.fulfill({ json: { total: 1, items: [{
		id: 999, title: 'Sensitive example', summary: '', first_seen: 1, url: 'https://example.com/post',
		provider: 'mastodon', local_image: true, state: {}, meme: { sensitive: true, spoiler: 'Warning', community: '#linux' }
	}] } }));
	await freshPage(page, '/memes?tab=examples');
	await expect(page.getByLabel('Community filter').locator('option')).toHaveText(['All communities', '#linux (1)']);
	await expect(page.getByLabel('Language filter').locator('option')).toHaveText(['All languages', 'en (1)']);
	await expect(page.locator('.meme-card .shot')).toHaveClass(/sensitive/);
	await expect(page.locator('.meme-card img')).toHaveCSS('filter', 'blur(7px)');
	const callsAfterLoad = topicCalls;
	await page.getByRole('tab', { name: 'Saved', exact: true }).click();
	await expect(page.getByRole('tab', { name: 'Saved', exact: true })).toHaveAttribute('aria-selected', 'true');
	await expect.poll(() => topicCalls).toBe(callsAfterLoad);
});

test('stored meme video plays, seeks, and closes in the drawer', async ({ page }) => {
	const { execFileSync } = await import('node:child_process');
	const clip = execFileSync('ffmpeg', ['-v', 'error', '-f', 'lavfi', '-i', 'testsrc=size=96x96:rate=12',
		'-t', '4', '-pix_fmt', 'yuv420p', '-c:v', 'libx264', '-movflags', 'frag_keyframe+empty_moov', '-f', 'mp4', 'pipe:1']);
	let videoRequests = 0;
	await page.route('**/api/hub/memes/items?*', (route) => route.fulfill({ json: { total: 1, items: [{
		id: 998, title: 'Moving meme', summary: 'Video caption', first_seen: 1, url: 'https://example.com/clip',
		provider: 'mastodon', local_video: true, local_image: false, meta: { media: { type: 'video' } }, state: {}, meme: { community: '#memes' }
	}] } }));
	await page.route('**/api/hub/items/998/assets/video', async (route) => {
		videoRequests++;
		expect(route.request().headers()['x-profile-id']).toBeTruthy();
		await route.fulfill({ contentType: 'video/mp4', body: clip });
	});
	await page.route('**/api/hub/memes/items/998/similar', (route) => route.fulfill({ json: { items: [] } }));
	await freshPage(page, '/memes?tab=examples');
	await page.getByRole('button', { name: 'Open meme: Moving meme' }).click();
	await expect(page.getByRole('dialog', { name: 'Meme detail' })).toBeVisible();
	expect(videoRequests).toBe(0);
	await page.getByRole('button', { name: 'Play video', exact: true }).click();
	const video = page.getByLabel('Meme video', { exact: true });
	await expect.poll(() => video.evaluate((el: HTMLVideoElement) => el.currentTime)).toBeGreaterThan(.1);
	await video.evaluate((el: HTMLVideoElement) => { el.pause(); el.currentTime = 2; });
	await expect.poll(() => video.evaluate((el: HTMLVideoElement) => el.currentTime)).toBeGreaterThan(1.8);
	await page.getByRole('button', { name: 'Close', exact: true }).click();
	await expect(video).toHaveCount(0);
});

test('Refresh memes starts collection and reports the cooldown honestly', async ({ page }) => {
	let refreshes = 0;
	let sweeps = 0;
	await page.route('**/api/hub/memes/refresh', (route) => {
		refreshes++;
		return route.fulfill({ json: refreshes === 1
			? { started: 1, running: 0, recent: 0, next_at: null }
			: { started: 0, running: 0, recent: 1, next_at: Date.now() / 1000 + 300 } });
	});
	await page.route('**/api/hub/memes/sweep', (route) => {
		sweeps++; return route.fulfill({ json: { analyzed: 3, transitions: 1, formats: 2 } });
	});
	await freshPage(page, '/memes');
	await page.getByRole('button', { name: 'Refresh memes' }).click();
	await expect(page.getByText(/Looking for fresh memes/)).toBeVisible();
	expect(refreshes).toBe(1); await expect.poll(() => sweeps).toBe(1);
	await page.getByRole('button', { name: 'Refresh memes' }).click();
	await expect(page.getByText(/Your meme curators refreshed recently/)).toBeVisible();
	expect(refreshes).toBe(2);
});

test('Set aside lists meme reasons and promotes an item out of the list', async ({ page }) => {
	let promoted = false;
	await page.route('**/api/hub/today/set-aside?*', (route) => route.fulfill({ json: promoted ? { total: 0, items: [] } : {
		total: 1, items: [{ id: 991, title: 'Quietly set aside', summary: '', first_seen: 1, url: 'https://example.com/post',
			provider: 'mastodon', local_image: false, state: {}, curator_ids: [], verdict: { tier: 'set_aside', reasons: ['too little context'] } }]
	} }));
	await page.route('**/api/hub/items/991/overrule', async (route) => {
		promoted = true;
		await route.fulfill({ json: { tier: 'feature', overruled: 'promoted', reasons: ['you promoted it'] } });
	});
	await freshPage(page, '/memes?tab=set-aside');
	await expect(page.getByText('too little context')).toBeVisible();
	await page.getByRole('button', { name: 'Promote' }).click();
	await expect(page.getByText('Quietly set aside')).toHaveCount(0);
});

test('Explore automatically pages and stops on a duplicate-only page', async ({ page }) => {
	const offsets: number[] = [];
	await page.route('**/api/hub/memes/items?*', async (route) => {
		const offset = Number(new URL(route.request().url()).searchParams.get('offset'));
		offsets.push(offset);
		await route.fulfill({ json: { total: 100, items: Array.from({ length: 24 }, (_, i) => ({
			id: offset === 0 ? i + 1 : i + 25, title: `Fresh laugh ${offset === 0 ? i + 1 : i + 25}`,
			summary: '', first_seen: 1, url: 'https://example.com/meme', provider: 'mastodon', state: {}, local_image: false
		})) } });
	});
	await freshPage(page, '/memes');
	await expect(page.locator('.meme-card')).toHaveCount(24);
	await page.getByRole('button', { name: 'Load more memes' }).scrollIntoViewIfNeeded();
	await expect(page.locator('.meme-card')).toHaveCount(48);
	await page.getByRole('button', { name: 'Load more memes' }).scrollIntoViewIfNeeded();
	await expect.poll(() => offsets).toEqual([0, 24, 48]);
	await expect(page.locator('.meme-card')).toHaveCount(48);
	await page.waitForTimeout(400);
	expect(offsets).toEqual([0, 24, 48]);
	await expect(page.getByRole('button', { name: 'Load more memes' })).toBeEnabled();
});

test('hiding a loaded meme keeps the next page aligned with the source', async ({ page }) => {
	const offsets: number[] = [];
	let hidden = false;
	await page.route('**/api/hub/memes/items?*', async (route) => {
		const offset = Number(new URL(route.request().url()).searchParams.get('offset'));
		offsets.push(offset);
		const ids = Array.from({ length: 48 }, (_, i) => i + 1).filter((id) => !hidden || id !== 1);
		await route.fulfill({ json: { total: ids.length, items: ids.slice(offset, offset + 24).map((id) => ({
			id, title: `Laugh ${id}`, summary: '', first_seen: 1, url: 'https://example.com/meme', provider: 'mastodon',
			state: {}, curator_ids: [1], local_image: false
		})) } });
	});
	await page.route('**/api/hub/items/1/state', (route) => { hidden = true; return route.fulfill({ json: { dismissed: true } }); });
	await freshPage(page, '/memes');
	await expect(page.locator('.meme-card')).toHaveCount(24);
	await page.getByRole('button', { name: 'Fewer memes like this: Laugh 1', exact: true }).click();
	await page.getByRole('button', { name: 'Skip', exact: true }).click();
	await page.getByRole('button', { name: 'Load more memes' }).scrollIntoViewIfNeeded();
	await expect(page.locator('.meme-card')).toHaveCount(47);
	expect(offsets).toEqual([0, 23]);
	await expect(page.getByRole('button', { name: 'Open meme: Laugh 25', exact: true })).toHaveCount(1);
});

test('slow filter results cannot replace a newer search', async ({ page }) => {
	let release!: () => void;
	let started = false;
	const delayed = new Promise<void>((resolve) => { release = resolve; });
	await page.route('**/api/hub/memes/items?*', async (route) => {
		const q = new URL(route.request().url()).searchParams.get('q') ?? 'initial';
		if (q === 'old') { started = true; await delayed; }
		await route.fulfill({ json: { total: 1, items: [{ id: q === 'old' ? 701 : 702,
			title: `${q} result`, summary: '', first_seen: 1, url: 'https://example.com/meme', provider: 'mastodon', state: {} }] } });
	});
	await freshPage(page, '/memes');
	await page.getByText('Filter your feed', { exact: true }).click();
	await page.getByRole('searchbox', { name: 'Filter memes' }).fill('old');
	await expect.poll(() => started).toBe(true);
	await page.getByRole('searchbox', { name: 'Filter memes' }).fill('current');
	await expect(page.getByRole('button', { name: 'Open meme: current result', exact: true })).toBeVisible();
	release();
	await page.waitForTimeout(150);
	await expect(page.getByRole('button', { name: 'Open meme: old result', exact: true })).toHaveCount(0);
	await expect(page.getByRole('button', { name: 'Open meme: current result', exact: true })).toBeVisible();
});

test('cursor paging continues without offset drift when supplied by the server', async ({ page }) => {
	const requests: URL[] = [];
	await page.route('**/api/hub/memes/items?*', async (route) => {
		const url = new URL(route.request().url()); requests.push(url);
		const next = url.searchParams.get('cursor') === 'older-than-24';
		await route.fulfill({ json: { total: 25, has_more: !next, next_cursor: next ? null : 'older-than-24',
			items: Array.from({ length: next ? 1 : 24 }, (_, i) => ({ id: next ? 25 : i + 1, title: `Cursor laugh ${next ? 25 : i + 1}`,
				summary: '', first_seen: 1, url: 'https://example.com/meme', provider: 'mastodon', state: {} })) } });
	});
	await freshPage(page, '/memes');
	await expect(page.locator('.meme-card')).toHaveCount(24);
	await page.getByRole('button', { name: 'Load more memes' }).scrollIntoViewIfNeeded();
	await expect(page.locator('.meme-card')).toHaveCount(25);
	expect(requests[1].searchParams.get('cursor')).toBe('older-than-24');
	await expect(page.getByRole('button', { name: 'Load more memes' })).toHaveCount(0);
});

test('image memes stay readable in the desktop and mobile feed', async ({ page }) => {
	const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="700" height="620" viewBox="0 0 700 620">
		<rect width="700" height="620" fill="#f6e9cd"/>
		<text x="350" y="65" text-anchor="middle" font-family="sans-serif" font-weight="bold" font-size="30">ME: JUST ONE MORE MEME</text>
		<ellipse cx="350" cy="435" rx="190" ry="95" fill="#d89b61"/>
		<path d="M210 300 L225 150 L310 235 L390 235 L475 150 L490 300" fill="#d89b61"/>
		<ellipse cx="350" cy="320" rx="150" ry="125" fill="#e7b77d"/>
		<path d="M250 300 L310 310 M390 310 L450 300" stroke="#332b27" stroke-width="12"/>
		<path d="M330 350 L350 365 L370 350 Z" fill="#a65d5b"/>
		<text x="350" y="580" text-anchor="middle" font-family="sans-serif" font-weight="bold" font-size="30">ALSO ME, 47 MEMES LATER</text></svg>`;
	await page.route('**/api/hub/items/801/assets/image', (route) => route.fulfill({ contentType: 'image/svg+xml', body: svg }));
	await page.route('**/api/hub/memes/items?*', (route) => route.fulfill({ json: { total: 1, has_more: false, next_cursor: null,
		items: [{ id: 801, title: 'Just one more meme', summary: '', first_seen: Date.now() / 1000,
			url: 'https://example.com/meme', provider: 'mastodon', publisher: 'Cat memes', local_image: true,
			curator_ids: [1], state: {}, meme: { language: 'en', community: '#memes' } }] } }));
	await page.setViewportSize({ width: 1280, height: 1200 });
	await freshPage(page, '/memes');
	await expect(page.locator('.meme-card img')).toBeVisible();
	await expect.poll(() => page.locator('.meme-card img').evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true);
	expect((await page.getByRole('button', { name: 'More memes like this: Just one more meme' }).boundingBox())!.height).toBeLessThan(60);
	await page.screenshot({ path: '/tmp/watchcraft-memes-desktop.png', fullPage: true });
	await page.setViewportSize({ width: 390, height: 1100 });
	await expect(page.locator('.meme-card img')).toBeVisible();
	expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
	await page.screenshot({ path: '/tmp/watchcraft-memes-mobile.png', fullPage: true });
});
