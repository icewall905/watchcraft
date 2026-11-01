import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { copyFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { freshPage } from './helpers';
const endpoint = process.env.WC_LIVE_STT, clip = process.env.WC_STT_TEST_CLIP;
const backend = process.env.BT_E2E_BACKEND, data = process.env.BT_E2E_DATA;
const ID = 'livestt001a';
test.skip(!endpoint || !clip || !backend || !data, 'explicit local speech-server acceptance probe');
test('real household STT generates a persistent visible subtitle track', async ({ page }) => {
 test.setTimeout(180_000);
 const dir = join(data!, 'media', ID.slice(0, 2)); mkdirSync(dir, { recursive: true });
 copyFileSync(clip!, join(dir, `${ID}.mp4`));
 execFileSync(join(backend!, '.venv/bin/python'), ['-c', `
from bt.db import session
from bt.models import Download, Video, Setting
with session() as s:
 s.merge(Video(id='${ID}', title='Live speech acceptance'))
 s.merge(Download(video_id='${ID}', status='done', path='${ID.slice(0,2)}/${ID}.mp4', height=360))
 s.merge(Setting(key='stt', value=dict(base_url='${endpoint}', model='Qwen/Qwen3-ASR-1.7B', api_key='', language='', subtitles_enabled=True)))
 s.commit()
`], { cwd: backend, env: { ...process.env, BT_DATA_DIR: data! } });
 await page.route(`**/api/video/${ID}`, r => r.fulfill({ json: { id: ID, title: 'Live speech acceptance', duration_s: 15, chapters: [], in_categories: [] } }));
 await page.route(`**/api/video/${ID}/captions`, r => r.fulfill({ json: [] }));
 await page.route(`**/api/video/${ID}/sponsorblock`, r => r.fulfill({ json: [] }));
 await freshPage(page, '/'); await page.goto(`/watch/${ID}`);
 await page.getByRole('button', { name: 'Subs', exact: true }).click();
 await page.getByRole('button', { name: 'Generate subtitles using STT' }).click();
 await expect(page.locator('video track')).toHaveAttribute('src', /subtitles\/generated.vtt/, { timeout: 150_000 });
 await page.locator('video').evaluate((v: HTMLVideoElement) => { v.pause(); v.currentTime = 3; });
 await expect.poll(() => page.locator('video').evaluate((v: HTMLVideoElement) => Array.from(v.textTracks[0]?.activeCues ?? []).map(c => (c as VTTCue).text).join(' '))).toMatch(/\w{3}/);
 await expect.poll(() => page.locator('video').evaluate((v: HTMLVideoElement) => (v.textTracks[0]?.activeCues?.[0] as VTTCue)?.line)).toBe(65);
 await page.mouse.move(0, 0); await page.waitForTimeout(4500);
 await page.screenshot({ path: '/tmp/watchcraft-live-stt-subtitles.png' });
 const vtt = await (await page.request.get(`/api/video/${ID}/subtitles/generated.vtt`)).text();
 expect(vtt).toContain('WEBVTT'); expect(vtt).toContain('-->');
});
