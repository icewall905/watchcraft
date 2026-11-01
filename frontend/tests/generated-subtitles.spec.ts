import { test, expect, type Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { join } from 'node:path';
import { mkdirSync } from 'node:fs';
import { freshPage } from './helpers';
const backend = process.env.BT_E2E_BACKEND, data = process.env.BT_E2E_DATA;
test.skip(!backend || !data, 'needs scripts/e2e.sh');
const ID = 'gensub00001';
const track = { key: 'generated', lang: 'en', name: 'Generated (STT)', auto: false, url: `/api/video/${ID}/subtitles/generated.vtt` };
test.beforeAll(() => {
 const dir = join(data!, 'media', ID.slice(0, 2)); mkdirSync(dir, { recursive: true });
 execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=320x180:r=5', '-t', '12', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', join(dir, `${ID}.mp4`)]);
 execFileSync(join(backend!, '.venv/bin/python'), ['-c', `
from bt.db import session
from bt.models import Download, Video
with session() as s:
 s.merge(Video(id='${ID}', title='Generated subtitle clip'))
 s.merge(Download(video_id='${ID}', status='done', path='${ID.slice(0, 2)}/${ID}.mp4', height=180))
 s.commit()
`], { cwd: backend, env: { ...process.env, BT_DATA_DIR: data! } });
});
async function player(page: Page, youtube = true, captionsGate?: Promise<void>) {
 await page.route(`**/api/video/${ID}`, r => r.fulfill({ json: { id: ID, title: 'Generated subtitle clip', duration_s: 12, chapters: [], in_categories: [] } }));
 await page.route(`**/api/video/${ID}/sponsorblock`, r => r.fulfill({ json: [] }));
 await page.route(`**/api/video/${ID}/captions`, async r => { await captionsGate; return youtube ? r.fulfill({ json: [{ key: 'en', lang: 'en', name: 'YouTube English', auto: true, url: `/api/video/${ID}/captions/en.vtt` }] }) : r.fulfill({ status: 503, json: { detail: 'Unavailable' } }); });
 await page.route('**/*.vtt*', r => r.fulfill({ contentType: 'text/vtt', body: 'WEBVTT\n\n00:00:00.000 --> 00:00:10.000\nAlternative words\n' }));
 await freshPage(page, '/'); await page.goto(`/watch/${ID}`);
 await expect(page.getByRole('button', { name: 'Subs', exact: true })).toBeEnabled();
 await page.locator('video').evaluate((v: HTMLVideoElement) => v.pause());
 await page.getByRole('button', { name: 'Subs', exact: true }).click();
}
test('generate alternative, progress, switch to YouTube/off, and retain after reload', async ({ page }) => {
 let status = 'idle', posts = 0;
 await page.route(`**/api/video/${ID}/subtitles`, async r => {
  if (r.request().method() === 'POST') { posts++; status = 'running'; }
  await r.fulfill({ json: { enabled: true, status, progress: status === 'running' ? 45 : 100, error: '', track: status === 'ready' ? track : null, timing_approximate: true } });
 });
 await player(page);
 await page.getByRole('button', { name: 'Generate subtitles using STT' }).click();
 await expect(page.getByRole('status')).toContainText('45%'); expect(posts).toBe(1);
 status = 'ready';
 await expect(page.locator('video track')).toHaveAttribute('src', /subtitles\/generated.vtt/, { timeout: 7000 });
 await page.getByRole('button', { name: 'Subs', exact: true }).click();
 await expect(page.getByText('Generated subtitles use approximate timing.')).toBeVisible();
 await page.getByRole('button', { name: 'YouTube English (auto)' }).click();
 await expect(page.locator('video track')).toHaveAttribute('src', /captions\/en.vtt/);
 await page.getByRole('button', { name: 'Subs', exact: true }).click();
 await page.getByRole('button', { name: 'Off', exact: true }).click();
 await expect(page.locator('video track')).toHaveCount(0);
 await page.reload(); await page.locator('video').evaluate((v: HTMLVideoElement) => v.pause());
 await page.getByRole('button', { name: 'Subs', exact: true }).click();
 await page.getByRole('button', { name: /Generated \(STT\)/ }).click();
 await expect(page.locator('video track')).toHaveAttribute('src', /generated.vtt/); expect(posts).toBe(1);
});
test('retry failed generation with unavailable YouTube captions', async ({ page }) => {
 let posts = 0;
 await page.route(`**/api/video/${ID}/subtitles`, r => {
  if (r.request().method() === 'POST') posts++;
  return r.fulfill({ json: { enabled: true, status: posts ? 'ready' : 'failed', progress: posts ? 100 : 0, error: posts ? '' : 'STT server unavailable', track: posts ? track : null } });
 });
 await player(page, false); await expect(page.getByRole('alert')).toContainText('STT server unavailable');
 await page.getByRole('button', { name: 'Retry subtitle generation' }).click();
 await expect(page.locator('video track')).toHaveAttribute('src', /generated.vtt/); expect(posts).toBe(1);
});
test('save and test speech settings draft with masked saved key', async ({ page }) => {
 let submitted: any;
 await page.route('**/api/settings', async r => {
  const response = await r.fetch(), settings = await response.json();
  await r.fulfill({ json: { ...settings, stt: { base_url: 'http://stt:8000/v1', model: 'old', api_key: '********', language: '', subtitles_enabled: false } } });
 });
 await page.route('**/api/settings/stt', async r => { submitted = r.request().postDataJSON(); await r.fulfill({ json: { ...submitted, api_key: '********' } }); });
 await page.route('**/api/speech/test', async r => { expect(r.request().postDataJSON().model).toBe('qwen-asr'); await r.fulfill({ json: { ok: true } }); });
 await freshPage(page, '/settings?tab=player');
 const section = page.locator('section.sec').filter({ has: page.getByRole('heading', { name: 'Generated subtitles', exact: true }) });
 await expect(section.getByLabel('Speech recognition API key', { exact: true })).toHaveAttribute('type', 'password');
 await section.getByLabel('Speech recognition model', { exact: true }).fill('qwen-asr');
 await section.getByRole('button', { name: 'Test speech connection' }).click();
 await expect(section.getByRole('status')).toContainText('connection works');
 await section.getByRole('button', { name: 'Save changes' }).click();
 await expect(section.getByRole('button', { name: 'Save changes' })).toBeDisabled();
 expect(submitted.api_key).toBe('********'); expect(submitted.model).toBe('qwen-asr');
});


test('delayed YouTube defaults cannot override a requested generated alternative', async ({ page }) => {
 let release!: () => void;
 const gate = new Promise<void>(resolve => (release = resolve));
 let state = 'idle';
 await page.route('**/api/settings', async r => {
  const response = await r.fetch(), settings = await response.json();
  await r.fulfill({ json: { ...settings, player: { ...settings.player, captions_lang: 'en' } } });
 });
 await page.route(`**/api/video/${ID}/subtitles`, async r => {
  if (r.request().method() === 'POST') state = 'running';
  await r.fulfill({ json: { enabled: true, status: state, progress: 40, error: '', track: state === 'ready' ? track : null, timing_approximate: true } });
 });
 try {
  await player(page, true, gate);
  await page.getByRole('button', { name: 'Generate subtitles using STT' }).click();
 } finally { release(); }
 await expect(page.getByRole('button', { name: 'YouTube English (auto)' })).toBeVisible();
 state = 'ready';
 await expect(page.locator('video track')).toHaveAttribute('src', /subtitles\/generated.vtt/, { timeout: 7000 });
 await page.locator('video').evaluate((v: HTMLVideoElement) => { v.currentTime = 1; });
 await expect.poll(() => page.locator('video').evaluate((v: HTMLVideoElement) =>
  Array.from(v.textTracks[0]?.activeCues ?? []).map(c => (c as VTTCue).text))).toEqual(['Alternative words']);
 await page.screenshot({ path: '/tmp/watchcraft-generated-subtitles.png' });
});
