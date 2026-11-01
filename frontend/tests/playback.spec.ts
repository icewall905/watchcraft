import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { resetBackend } from './helpers';

const VIDEO = 'castvideo01';

test('phone pairs, sends one-tap playback to a persistent browser receiver and controls it', async ({ browser, request }) => {
	await resetBackend(request);
	// A regular faststart MP4 with a keyframe every second, so the receiver can seek within it.
	const file = join(mkdtempSync(join(tmpdir(), 'bt-cast-')), 'cast.mp4');
	execFileSync('ffmpeg', ['-loglevel', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=160x90:r=5',
		'-t', '30', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-g', '5', '-movflags', '+faststart', file]);
	const media = readFileSync(file);
	const phoneContext = await browser.newContext({ viewport: { width: 390, height: 844 } });
	const screenContext = await browser.newContext();
	const phone = await phoneContext.newPage();
	const screen = await screenContext.newPage();
	let registration: { id: string; token: string; pair_code: string } | null = null;
	try {
		await phoneContext.route('**/api/feed/home*', route => route.fulfill({ json: [{
			kind: 'category', category_id: 999, title: 'Test picks', emoji: '▶', color: '#7c5cff', items: [{
				id: 1, category_id: 999, score: 85, summary: 'Test', video: {
					id: VIDEO, title: 'Cast test video', channel_name: 'Test', duration_s: 30
				}
			}]
		}] }));
		// Real media playback on the receiver; no external YouTube calls needed.
		await screenContext.route(`**/api/video/${VIDEO}`, route => route.fulfill({ json: {
			id: VIDEO, title: 'Cast test video', duration_s: 30, chapters: [], in_categories: []
		} }));
		// Byte ranges like the real file endpoint, so the receiver can seek.
		await screenContext.route(`**/api/video/${VIDEO}/file`, async route => {
			const range = /bytes=(\d+)-(\d*)/.exec((await route.request().allHeaders())['range'] ?? '');
			if (route.request().method() === 'HEAD' || !range) return route.fulfill({
				status: 200, contentType: 'video/mp4', headers: { 'Accept-Ranges': 'bytes' },
				body: route.request().method() === 'HEAD' ? Buffer.alloc(0) : media
			});
			const start = Number(range[1]), end = range[2] ? Math.min(Number(range[2]), media.length - 1) : media.length - 1;
			return route.fulfill({ status: 206, contentType: 'video/mp4', body: media.subarray(start, end + 1),
				headers: { 'Accept-Ranges': 'bytes', 'Content-Range': `bytes ${start}-${end}/${media.length}` } });
		});
		await screenContext.route(`**/api/video/${VIDEO}/sponsorblock`, route => route.fulfill({ json: [] }));
		await screenContext.route(`**/api/video/${VIDEO}/captions`, route => route.fulfill({ json: [] }));
		await phone.goto('/');
		await screen.goto('/');
		await screen.getByRole('button', { name: 'Play on', exact: true }).click();
		await screen.getByLabel('Screen name', { exact: true }).fill('Living room test');
		await screen.getByRole('button', { name: 'Register this screen', exact: true }).click();
		await expect(screen.getByRole('heading', { name: 'This screen: Living room test' })).toBeVisible();
		// The screen shows the pairing code that senders must enter once.
		await expect(screen.getByText(/Pairing code to enter on senders:/)).toBeVisible();
		registration = await screen.evaluate(() => JSON.parse(localStorage.getItem('bt.playback.receiver')!));
		expect(registration!.pair_code).toMatch(/^\d{6}$/);
		await screen.getByRole('button', { name: 'Done', exact: true }).click();
		await screen.reload();

		// Pairing is required before the phone may send anything to this screen.
		await phone.getByRole('button', { name: 'Play on', exact: true }).click();
		await phone.getByRole('button', { name: /Living room test/ }).click();
		await expect(phone.getByLabel('Pairing code', { exact: true })).toBeVisible();
		await phone.getByLabel('Pairing code', { exact: true }).fill(registration!.pair_code);
		await phone.getByRole('button', { name: 'Pair', exact: true }).click();
		await expect(phone.getByText(/Tap any video to play it on this screen/)).toBeVisible();
		await phone.getByRole('button', { name: 'Done', exact: true }).click();

		await phone.getByRole('link', { name: 'Cast test video', exact: true }).evaluate(a => a.setAttribute('href', '/watch/castvideo01'));
		await phone.getByRole('link', { name: 'Cast test video', exact: true }).click();
		await expect(screen).toHaveURL(/\/watch\/castvideo01\?cast=/);
		await expect(phone).not.toHaveURL(/\/watch\//);
		await expect.poll(() => screen.locator('.watch video').evaluate((v: HTMLVideoElement) => !v.paused && v.currentTime > 0)).toBe(true);
		const firstCast = screen.url();
		await phone.getByRole('link', { name: 'Cast test video', exact: true }).click();
		await expect.poll(() => screen.url()).not.toBe(firstCast);
		await expect.poll(() => screen.locator('.watch video').evaluate((v: HTMLVideoElement) => !v.paused)).toBe(true);
		await phone.getByRole('button', { name: 'Play on', exact: true }).click();
		await phone.getByRole('button', { name: 'Pause', exact: true }).click();
		await expect.poll(() => screen.locator('.watch video').evaluate((v: HTMLVideoElement) => v.paused)).toBe(true);
		await phone.getByRole('button', { name: 'Resume', exact: true }).click();
		await expect.poll(() => screen.locator('.watch video').evaluate((v: HTMLVideoElement) => !v.paused)).toBe(true);
		// The phone shows the screen's progress and can move it forward, back or to a dragged position.
		const slider = phone.getByRole('slider', { name: 'Playback position' });
		await expect(slider).toBeVisible();
		await expect(phone.getByText(/\/ 0:30$/)).toBeVisible();
		await phone.getByRole('button', { name: 'Forward 10 seconds' }).click();
		await expect.poll(() => screen.locator('.watch video').evaluate((v: HTMLVideoElement) => v.currentTime)).toBeGreaterThan(9);
		await expect(phone.getByRole('button', { name: 'Back 10 seconds' })).toBeEnabled();
		await slider.fill('25');
		await expect.poll(() => screen.locator('.watch video').evaluate((v: HTMLVideoElement) => v.currentTime)).toBeGreaterThan(24);
		await phone.getByRole('button', { name: 'Back 10 seconds' }).click();
		await expect.poll(() => screen.locator('.watch video').evaluate((v: HTMLVideoElement) => v.currentTime)).toBeLessThan(22);
		await phone.getByRole('button', { name: 'Stop', exact: true }).click();
		await expect(screen).toHaveURL(/\/$/);
		await phone.getByRole('button', { name: 'This device', exact: true }).click();
		await expect(phone.getByRole('dialog')).toHaveCount(0);
		// A sender can drop its own pairing; the screen then asks for the code again.
		await phone.getByRole('button', { name: 'Play on', exact: true }).click();
		await phone.getByRole('button', { name: /Living room test/ }).click();
		await phone.getByRole('button', { name: 'Play on', exact: true }).click();
		await phone.getByRole('button', { name: 'Forget this screen', exact: true }).click();
		await phone.getByRole('button', { name: /Living room test/ }).click();
		await expect(phone.getByLabel('Pairing code', { exact: true })).toBeVisible();
	} finally {
		if (registration) await request.delete(`/api/playback/targets/${registration.id}`, {
			headers: { 'X-Receiver-Token': registration.token }
		});
		await phoneContext.close();
		await screenContext.close();
	}
});

test('administrators manage screens and pairings from Settings', async ({ browser, request }) => {
	await resetBackend(request);
	const admin = { 'X-Profile-Id': '1' };
	const screen = await (await request.post('/api/playback/targets', { data: { name: 'Study' } })).json();
	const guest = await (await request.post('/api/profiles', { data: { name: 'Guest' } })).json();
	await request.post(`/api/playback/targets/${screen.id}/pair`,
		{ data: { code: screen.pair_code }, headers: { 'X-Profile-Id': String(guest.id) } });
	const context = await browser.newContext();
	const page = await context.newPage();
	const heartbeat = () => request.post(`/api/playback/targets/${screen.id}/heartbeat`,
		{ data: {}, headers: { 'X-Receiver-Token': screen.token } });
	try {
		// Two profiles exist, so pick the administrator instead of the "Who's watching?" chooser.
		await page.goto('/settings?tab=screens');
		await page.evaluate(() => { localStorage.clear(); localStorage.setItem('bt.profile', '1'); });
		await page.reload();
		await expect(page.getByRole('heading', { name: 'Screens' })).toBeVisible();
		await expect(page.getByText('Study', { exact: true })).toBeVisible();
		// The administrator sees who may send to the screen and revokes one profile.
		await page.getByRole('button', { name: 'Unpair Guest from Study' }).click();
		await expect(page.getByRole('button', { name: 'Unpair Guest from Study' })).toHaveCount(0);
		// A new code replaces the one the screen showed, so anyone who saw it cannot pair.
		await page.getByRole('button', { name: 'New pairing code' }).click();
		await expect.poll(async () => (await (await heartbeat()).json()).pair_code).not.toBe(screen.pair_code);
		// Removing the screen unregisters it from the sender side; its credentials stop working.
		await page.getByRole('button', { name: 'Remove screen' }).click();
		await page.getByRole('button', { name: 'Press again to remove' }).click();
		await expect(page.getByText('No screens are registered yet.')).toBeVisible();
		expect((await heartbeat()).status()).toBe(403);
	} finally {
		await request.delete(`/api/playback/targets/${screen.id}/registration`, { headers: admin });
		await request.delete(`/api/profiles/${guest.id}`, { headers: admin });
		await context.close();
	}
});
