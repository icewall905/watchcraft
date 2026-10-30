import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const fixtureDir = mkdtempSync(join(tmpdir(), 'wc-voice-browser-'));
const recording = join(fixtureDir, 'microphone.wav');
test.use({
	permissions: ['microphone'],
	launchOptions: { args: ['--autoplay-policy=no-user-gesture-required', '--use-fake-device-for-media-stream',
		'--use-fake-ui-for-media-stream', `--use-file-for-fake-audio-capture=${recording}`] }
});
test.beforeAll(() => {
	execFileSync('ffmpeg', ['-y', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
		'sine=frequency=440:duration=1.5', '-af', 'apad=pad_dur=3', '-ar', '48000', '-ac', '1', recording]);
});
test.afterAll(() => rmSync(fixtureDir, { recursive: true, force: true }));

test.beforeEach(async ({ page }) => {
	await page.addInitScript(() => {
		const tracks: MediaStreamTrack[] = [];
		(window as unknown as { voiceTracks: MediaStreamTrack[] }).voiceTracks = tracks;
		const getUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
		navigator.mediaDevices.getUserMedia = async constraints => {
			const stream = await getUserMedia(constraints); tracks.push(...stream.getTracks()); return stream;
		};
	});
	await page.route('**/api/search?*', route => route.fulfill({ json: [] }));
});

const tracksEnded = async (page: import('@playwright/test').Page) => page.evaluate(() =>
	(window as unknown as { voiceTracks: MediaStreamTrack[] }).voiceTracks.length > 0 &&
	(window as unknown as { voiceTracks: MediaStreamTrack[] }).voiceTracks.every(track => track.readyState === 'ended'));

test('TV remote starts voice search, silence finishes it, and the phrase searches videos', async ({ page }) => {
	let bytes = 0, type = '';
	await page.route('**/api/speech/transcribe', route => {
		bytes = route.request().postDataBuffer()?.length || 0;
		type = route.request().headers()['content-type'];
		return route.fulfill({ json: { text: 'woodworking ideas' } });
	});
	await page.goto('/tv?mode=tv');
	const mic = page.getByRole('button', { name: 'Voice search', exact: true });
	await mic.focus(); await page.keyboard.press('Enter');
	await expect(page.getByRole('dialog', { name: 'Voice search' })).toBeVisible();
	await expect(page.getByRole('status').filter({ hasText: 'Listening' })).toBeVisible();
	await page.screenshot({ path: '/tmp/watchcraft-voice-preview.png' });
	await expect(page).toHaveURL(/\/search\?q=woodworking%20ideas/, { timeout: 15000 });
	await expect(page.getByRole('searchbox', { name: 'Search YouTube' })).toHaveValue('woodworking ideas');
	await expect(page.getByRole('dialog', { name: 'Voice search' })).toHaveCount(0);
	expect(bytes).toBeGreaterThan(1000); expect(type).toContain('audio/webm');
	await expect.poll(() => tracksEnded(page)).toBe(true);
});

test('Back cancels recording and releases the microphone without uploading', async ({ page }) => {
	let uploads = 0;
	await page.route('**/api/speech/transcribe', route => { uploads++; return route.fulfill({ json: { text: 'unexpected' } }); });
	await page.goto('/search');
	await page.locator('form.bar').getByRole('button', { name: 'Voice search' }).click();
	await expect(page.getByRole('button', { name: 'Done', exact: true })).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(page.getByRole('dialog', { name: 'Voice search' })).toHaveCount(0);
	await expect.poll(() => tracksEnded(page)).toBe(true);
	expect(uploads).toBe(0);
	await expect(page.getByRole('searchbox', { name: 'Search YouTube' })).toHaveValue('');
});

test('denied microphone access shows a useful error', async ({ page }) => {
	await page.addInitScript(() => {
		navigator.mediaDevices.getUserMedia = () => Promise.reject(new DOMException('Denied', 'NotAllowedError'));
	});
	await page.goto('/search');
	await page.locator('form.bar').getByRole('button', { name: 'Voice search' }).click();
	await expect(page.getByRole('alert')).toContainText('Microphone access was denied');
	await page.getByRole('button', { name: 'Cancel', exact: true }).click();
	await expect(page.getByRole('dialog', { name: 'Voice search' })).toHaveCount(0);
});

test('STT failures keep typed search text and offer retry', async ({ page }) => {
	await page.route('**/api/speech/transcribe', route => route.fulfill({ status: 503, json: { detail: 'Cannot reach the speech-to-text service.' } }));
	await page.goto('/search?q=keep%20this');
	await page.locator('form.bar').getByRole('button', { name: 'Voice search' }).click();
	await expect(page.getByRole('button', { name: 'Done', exact: true })).toBeFocused();
	await page.waitForTimeout(500);
	await page.keyboard.press('Enter');
	await expect(page.getByRole('alert')).toContainText('Cannot reach the speech-to-text service');
	await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
	await expect(page.getByRole('searchbox', { name: 'Search YouTube' })).toHaveValue('keep this');
	await expect.poll(() => tracksEnded(page)).toBe(true);
});

test('Find voice input searches the local library', async ({ page }) => {
	await page.route('**/api/speech/transcribe', route => route.fulfill({ json: { text: 'local recipe' } }));
	await page.goto('/find');
	await page.getByRole('button', { name: 'Voice search', exact: true }).click();
	await expect(page.getByRole('button', { name: 'Done', exact: true })).toBeVisible();
	await page.waitForTimeout(500);
	await page.getByRole('button', { name: 'Done', exact: true }).click();
	await expect(page.getByRole('searchbox', { name: 'Search', exact: true })).toHaveValue('local recipe');
	await expect(page).toHaveURL(/\/find\?q=local\+recipe/);
});

test('TV header voice search updates an already open search page', async ({ page }) => {
	await page.route('**/api/speech/transcribe', route => route.fulfill({ json: { text: 'new phrase' } }));
	await page.goto('/tv?mode=tv');
	await expect(page.locator('header.topbar').getByRole('button', { name: 'Voice search' })).toBeVisible();
	await page.goto('/search?q=old%20phrase');
	await page.locator('header.topbar').getByRole('button', { name: 'Voice search' }).click();
	await expect(page.getByRole('button', { name: 'Done', exact: true })).toBeVisible();
	await page.waitForTimeout(500);
	await page.getByRole('button', { name: 'Done', exact: true }).click();
	await expect(page.getByRole('searchbox', { name: 'Search YouTube' })).toHaveValue('new phrase');
	await expect(page).toHaveURL(/\/search\?q=new%20phrase/);
});

test('desktop search dialog transcribes a phrase into Find', async ({ page }) => {
	await page.route('**/api/speech/transcribe', route => route.fulfill({ json: { text: 'saved knitting patterns' } }));
	await page.goto('/');
	await page.getByRole('button', { name: 'Search everything', exact: true }).click();
	await page.getByRole('dialog', { name: 'Search everything' }).getByRole('button', { name: 'Voice search' }).click();
	await expect(page.getByRole('button', { name: 'Done', exact: true })).toBeVisible();
	await page.waitForTimeout(500);
	await page.getByRole('button', { name: 'Done', exact: true }).click();
	await expect(page).toHaveURL(/\/find\?q=saved(\+|%20)knitting(\+|%20)patterns/);
	await expect(page.getByRole('searchbox', { name: 'Search', exact: true })).toHaveValue('saved knitting patterns');
});
