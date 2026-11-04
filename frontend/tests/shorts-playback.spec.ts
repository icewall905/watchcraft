import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { freshPage, resetBackend } from './helpers';
const data = process.env.BT_E2E_DATA, backend = process.env.BT_E2E_BACKEND;
test.skip(!data || !backend, 'requires disposable backend');
test('configured feeds play real local files and never request a stream', async ({ page, request }) => {
	await resetBackend(request);
	await request.post('/api/agent/pause');
	const profiles = await (await request.get('/api/profiles')).json();
	const headers = { 'X-Profile-Id': String(profiles[0].id) };
	const category = await (await request.post('/api/categories', { headers, data: { kind: 'shorts', name: 'Local acceptance', brief: 'Local short science clips', enabled: false } })).json();
	const dir = join(data!, 'media', 'sh');
	mkdirSync(dir, { recursive: true });
	for (const [id, width] of [['shortloc001', 180], ['shortloc002', 200]] as const) {
		execFileSync('ffmpeg', ['-y', '-v', 'error', '-f', 'lavfi', '-i', `testsrc2=size=${width}x320:rate=10`, '-t', '30', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', join(dir, `${id}.mp4`)]);
		execFileSync(join(backend!, '.venv/bin/python'), ['-c', `
from bt.db import init_db,session
from bt.models import Video,Download,Candidate
init_db()
with session() as s:
 s.merge(Video(id='${id}',title='Local ${id}',duration_s=30,is_short=True))
 s.merge(Download(video_id='${id}',status='done',path='sh/${id}.mp4',progress=1.0))
 s.add(Candidate(video_id='${id}',category_id=${category.id},status='accepted',score=90))
 s.commit()
`], { cwd: backend, env: { ...process.env, BT_DATA_DIR: data! } });
	}
	const forbidden: string[] = [];
	page.on('request', r => { if (/manifest\.mpd|\/proxy|googlevideo/.test(r.url()))
		forbidden.push(r.url()); });
	await page.route('**/api/video/*/subtitles', r => r.fulfill({ json: { enabled: true, status: 'ready', progress: 100, error: '', track: { url: `/api/video/${r.request().url().split('/').at(-2)}/subtitles/generated.vtt` } } }));
 await page.route('**/api/video/*/subtitles/generated.vtt', r => r.fulfill({ contentType: 'text/vtt', body: 'WEBVTT\n\n00:00:00.000 --> 00:00:30.000\nSound off, captions stay readable across this\nlong two-line sentence without covering controls\n' }));
 await freshPage(page, '/shorts');
	await page.getByRole('button', { name: /Local acceptance/ }).click();
	await expect.poll(() => page.locator('video').evaluate((v: HTMLVideoElement) => v.currentTime), { timeout: 15000 }).toBeGreaterThan(.2);
	await expect.poll(() => page.locator('video').evaluate((v: HTMLVideoElement) => v.videoWidth)).toBe(180);
	await expect(page.getByLabel('Subtitles')).toContainText('Sound off, captions stay readable');
 expect(await page.locator('video').evaluate((v: HTMLVideoElement) => v.muted)).toBe(true);
 await page.screenshot({ path: '/tmp/watchcraft-local-shorts-playback.png' });
 await page.setViewportSize({ width: 390, height: 844 });
 await expect(page.getByLabel('Subtitles')).toBeVisible();
 await page.screenshot({ path: '/tmp/watchcraft-local-reel-phone.png' });
 const captionBox = await page.getByLabel('Subtitles').boundingBox();
 const actsBox = await page.locator('.acts').boundingBox();
 expect(captionBox!.y + captionBox!.height).toBeLessThan(actsBox!.y);
 await page.locator('video').click({ position: { x: 150, y: 300 } });
 await expect(page.locator('.meta.show')).toBeVisible();
 const detailCaptionBox = await page.getByLabel('Subtitles').boundingBox();
 const metaBox = await page.locator('.meta.show').boundingBox();
 expect(detailCaptionBox!.y + detailCaptionBox!.height).toBeLessThan(metaBox!.y);
 await page.screenshot({ path: '/tmp/watchcraft-local-reel-phone-details.png' });
	await page.keyboard.press('ArrowDown');
	await expect.poll(() => page.locator('video').evaluate((v: HTMLVideoElement) => v.videoWidth)).toBe(200);
	await expect.poll(() => page.locator('video').evaluate((v: HTMLVideoElement) => v.currentTime)).toBeGreaterThan(.2);
	await page.getByRole('button', { name: '← Feeds' }).click();
	await expect(page.locator('video')).toHaveCount(0);
	expect(forbidden).toEqual([]);
});
