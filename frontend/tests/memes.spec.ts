import { test, expect } from '@playwright/test';
import { freshPage } from './helpers';

test('Meme Radar is in navigation and renders its empty states', async ({ page }) => {
	await freshPage(page, '/');
	const nav = page.getByRole('navigation', { name: 'Main', exact: true });
	await expect(nav.getByRole('link', { name: 'Meme Radar' })).toBeVisible();
	await nav.getByRole('link', { name: 'Meme Radar' }).click();
	await expect(page.getByRole('heading', { name: 'Meme Radar', level: 1 })).toBeVisible();
	for (const t of ['Trending', 'Emerging', 'Formats', 'Explore', 'Saved', 'History'])
		await expect(page.getByRole('tab', { name: t, exact: true })).toBeVisible();
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
