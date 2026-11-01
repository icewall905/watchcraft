import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, readFileSync, rmSync } from 'node:fs';
import { join, basename } from 'node:path';
import { tmpdir } from 'node:os';
import { freshPage } from './helpers';
let assets: string;
test.beforeAll(() => {
 assets = mkdtempSync(join(tmpdir(), 'watchcraft-shorts-media-'));
 for (const [id, width] of [['short000001', 180], ['short000002', 200]] as const) {
 mkdirSync(join(assets, id));
 execFileSync('ffmpeg', ['-v', 'error', '-f', 'lavfi', '-i', `testsrc2=size=${width}x320:rate=10`, '-f', 'lavfi', '-i', 'sine=frequency=440', '-t', '5', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-g', '10', '-c:a', 'aac', '-f', 'dash', join(assets, id, 'index.mpd')]);
 }
});
test.afterAll(() => { if (assets) rmSync(assets, { recursive: true, force: true }); });
test('Shorts decodes real DASH video and advances to the next clip', async ({ page }) => {
 const items = ['short000001', 'short000002'].map((id, i) => ({ id: i+1, category_id: 1, score: 90, summary: 'Local DASH acceptance', video: { id, title: `Playback clip ${i+1}`, channel_name: 'Local fixture', duration_s: 5, is_short: true } }));
 await page.route('**/api/shorts*', r => r.fulfill({ json: { items, total: 2, next_offset: null } }));
 await page.route('**/api/video/short00000*/*', r => {
  const url = new URL(r.request().url());
  const file = basename(url.pathname), id = url.pathname.split('/')[3];
  if (file !== 'manifest.mpd' && !/^(init|chunk)-stream[0-9].*\.m4s$/.test(file)) return r.continue();
  return r.fulfill({ body: readFileSync(join(assets, id, file === 'manifest.mpd' ? 'index.mpd' : file)), contentType: file === 'manifest.mpd' ? 'application/dash+xml' : 'video/mp4' });
 });
 await freshPage(page, '/shorts');
 await expect.poll(() => page.locator('video').evaluate((v: HTMLVideoElement) => v.currentTime), { timeout: 15000 }).toBeGreaterThan(.2);
 expect(await page.locator('video').evaluate((v: HTMLVideoElement) => v.videoWidth)).toBe(180);
 expect(await page.locator('video').evaluate((v: HTMLVideoElement) => v.videoHeight)).toBe(320);
 await expect(page.locator('video')).toHaveCSS('object-fit', 'contain');
 await page.screenshot({ path: '/tmp/watchcraft-shorts-playback.png' });
 await page.keyboard.press('ArrowDown');
 await expect(page.getByRole('heading', { name: 'Playback clip 2' })).toBeVisible();
 await expect.poll(() => page.locator('video').evaluate((v: HTMLVideoElement) => v.videoWidth), { timeout: 15000 }).toBe(200);
 await expect.poll(() => page.locator('video').evaluate((v: HTMLVideoElement) => v.currentTime), { timeout: 15000 }).toBeGreaterThan(.2);
 await expect(page.getByRole('alert')).toHaveCount(0);
});
