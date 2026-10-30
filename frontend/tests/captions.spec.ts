import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { join } from 'node:path';
import { mkdirSync } from 'node:fs';
import { freshPage } from './helpers';

const backend = process.env.BT_E2E_BACKEND;
const data = process.env.BT_E2E_DATA;
test.skip(!backend || !data, 'needs scripts/e2e.sh');
const ID = 'subtest0001';

test('captions hold both lines until the next block', async ({ page }) => {
	const mediaDir = join(data!, 'media', ID.slice(0, 2));
	mkdirSync(mediaDir, { recursive: true });
	execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=320x180:r=5',
		'-t', '12', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', join(mediaDir, `${ID}.mp4`)]);
	execFileSync(join(backend!, '.venv/bin/python'), ['-c', `
from bt.db import session
from bt.models import Download, Video
with session() as s:
    s.merge(Video(id='${ID}', title='Subtitle test clip'))
    s.merge(Download(video_id='${ID}', status='done', path='${ID.slice(0, 2)}/${ID}.mp4', height=180))
    s.commit()
`], { cwd: backend, env: { ...process.env, BT_DATA_DIR: data! } });
	const vtt = execFileSync(join(backend!, '.venv/bin/python'), ['-c', `
import json
from bt.captions import two_line_vtt
rows = [(0,5000,'First line'),(2000,7000,'First line\\nSecond line'),(4000,9000,'Second line\\nThird line'),(6000,11000,'Third line\\nFourth line')]
raw = json.dumps({'events':[{'tStartMs':start,'dDurationMs':end-start,'segs':[{'utf8':value}]} for start,end,value in rows]})
print(two_line_vtt(raw, 'json3'))
`], { cwd: backend }).toString();
	await page.route('**/api/settings', async (route) => {
		const response = await route.fetch();
		const settings = await response.json();
		await route.fulfill({ json: { ...settings, player: { ...settings.player, captions_lang: 'en' } } });
	});
	await page.route(`**/api/video/${ID}`, route => route.fulfill({ json: {
		id: ID, title: 'Subtitle test clip', duration_s: 12, chapters: [], in_categories: []
	} }));
	await page.route(`**/api/video/${ID}/sponsorblock`, route => route.fulfill({ json: [] }));
	await page.route(`**/api/video/${ID}/captions`, route => route.fulfill({ json: [
		{ key: 'en', lang: 'en', name: 'English', auto: true, url: `/api/video/${ID}/captions/en.vtt` }
	] }));
	await page.route(`**/api/video/${ID}/captions/en.vtt*`, route => route.fulfill({ contentType: 'text/vtt', body: vtt }));
	await freshPage(page, '/');
	await page.goto(`/watch/${ID}`);
	const video = page.locator('video');
	await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.textTracks[0]?.cues?.length)).toBe(2);
	await expect(page.locator('video track')).toHaveAttribute('src', /layout=two-line-v1/);
	const seekTo = async (time: number) => {
		await video.evaluate((v: HTMLVideoElement, time) => { v.pause(); v.currentTime = time; }, time);
		await expect.poll(() => video.evaluate((v: HTMLVideoElement, time) => !v.seeking && Math.abs(v.currentTime - time) < 0.1, time)).toBe(true);
	};
	await seekTo(1);
	const activeText = () => video.evaluate((v: HTMLVideoElement) =>
		Array.from(v.textTracks[0]?.activeCues ?? []).map(c => (c as VTTCue).text));
	await expect.poll(activeText).toEqual(['First line\nSecond line']);
	await seekTo(3);
	await expect.poll(activeText).toEqual(['First line\nSecond line']);
	await seekTo(5);
	await expect.poll(activeText).toEqual(['Third line\nFourth line']);
	await video.evaluate((v: HTMLVideoElement) => v.play());
	await expect(page.locator('.watch')).toHaveClass(/hide-cursor/);
	await page.screenshot({ path: '/tmp/watchcraft-subtitles-preview.png' });
});
