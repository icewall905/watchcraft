import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { resetBackend } from './helpers';

const VIDEO = 'castvideo01';

test('phone pairs, sends one-tap playback to a persistent browser receiver and controls it', async ({ browser, request }) => {
	await resetBackend(request);
	const media = execFileSync('ffmpeg', ['-loglevel', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=160x90:r=5',
		'-t', '30', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', 'frag_keyframe+empty_moov', '-f', 'mp4', 'pipe:1']);
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
		await screenContext.route(`**/api/video/${VIDEO}/file`, route => route.fulfill({
			status: 200, contentType: 'video/mp4', body: route.request().method() === 'HEAD' ? Buffer.alloc(0) : media
		}));
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
		await phone.getByRole('button', { name: 'Stop', exact: true }).click();
		await expect(screen).toHaveURL(/\/$/);
		await phone.getByRole('button', { name: 'This device', exact: true }).click();
		await expect(phone.getByRole('dialog')).toHaveCount(0);
	} finally {
		if (registration) await request.delete(`/api/playback/targets/${registration.id}`, {
			headers: { 'X-Receiver-Token': registration.token }
		});
		await phoneContext.close();
		await screenContext.close();
	}
});
