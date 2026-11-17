import { test, expect, type Page, type Route } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const VIDEO = 'retryvideo1';
let media: Buffer;

test.beforeAll(() => {
	const file = join(mkdtempSync(join(tmpdir(), 'bt-retry-')), 'retry.mp4');
	execFileSync('ffmpeg', ['-loglevel', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=160x90:r=5',
		'-t', '60', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-g', '5', '-movflags', '+faststart', file]);
	media = readFileSync(file);
});

async function serve(route: Route) {
	const range = /bytes=(\d+)-(\d*)/.exec((await route.request().allHeaders())['range'] ?? '');
	if (!range) return route.fulfill({ status: 200, contentType: 'video/mp4', headers: { 'Accept-Ranges': 'bytes' }, body: media });
	const start = Number(range[1]), end = range[2] ? Math.min(Number(range[2]), media.length - 1) : media.length - 1;
	return route.fulfill({ status: 206, contentType: 'video/mp4', body: media.subarray(start, end + 1),
		headers: { 'Accept-Ranges': 'bytes', 'Content-Range': `bytes ${start}-${end}/${media.length}` } });
}

/** No saved copy, DASH always refused; the progressive stream fails `fails()` times, then works. */
async function mockVideo(page: Page, failing: () => boolean) {
	await page.addInitScript(() => localStorage.setItem('bt.profile', '1'));
	await page.route(`**/api/video/${VIDEO}`, (r) => r.fulfill({ json: { id: VIDEO, title: 'Retry test video', duration_s: 60, chapters: [], in_categories: [] } }));
	await page.route(`**/api/video/${VIDEO}/file`, (r) => r.fulfill({ status: 404, body: '' }));
	await page.route(`**/api/video/${VIDEO}/manifest.mpd*`, (r) => r.fulfill({ status: 503, body: '' }));
	await page.route(`**/api/video/${VIDEO}/fallback`, (r) => (failing() ? r.fulfill({ status: 503, body: '' }) : serve(r)));
	await page.route(`**/api/video/${VIDEO}/sponsorblock`, (r) => r.fulfill({ json: [] }));
	await page.route(`**/api/video/${VIDEO}/captions`, (r) => r.fulfill({ json: [] }));
}

test('a failing stream spins and retries instead of showing the error card', async ({ page }) => {
	let fails = 2;
	await mockVideo(page, () => fails-- > 0);
	await page.goto(`/watch/${VIDEO}`);
	await expect(page.getByText(/^(Reconnecting…|Having trouble reaching the video\. Retrying…)$/)).toBeVisible({ timeout: 10000 });
	await expect.poll(() => page.evaluate(() => document.querySelector('video')!.currentTime), { timeout: 20000 }).toBeGreaterThan(0.5);
	await expect(page.getByRole('heading', { name: "Can't play this video" })).toHaveCount(0);
});

test('a stream that breaks mid-play resumes where it was', async ({ page }) => {
	let broken = false;
	await mockVideo(page, () => broken);
	await page.goto(`/watch/${VIDEO}`);
	await expect.poll(() => page.evaluate(() => document.querySelector('video')?.readyState ?? 0), { timeout: 15000 }).toBeGreaterThan(0);
	await page.evaluate(() => { const v = document.querySelector('video')!; v.muted = true; v.currentTime = 20; });
	await expect.poll(() => page.evaluate(() => document.querySelector('video')!.currentTime), { timeout: 15000 }).toBeGreaterThan(20.5);
	broken = true;
	// Break the running stream: the element errors out like a dropped proxy connection.
	await page.evaluate(() => { const v = document.querySelector('video')!; v.src = '/api/video/retryvideo1/fallback?broken=1'; });
	await expect(page.getByText('Reconnecting…')).toBeVisible({ timeout: 5000 });
	broken = false;
	await expect.poll(() => page.evaluate(() => document.querySelector('video')!.currentTime), { timeout: 20000 }).toBeGreaterThan(20.5);
	await expect(page.getByRole('heading', { name: "Can't play this video" })).toHaveCount(0);
});
