import { test, expect, type Page } from '@playwright/test';
import { freshPage } from './helpers';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

// The Commons redesign: Today/Hot/New/Top/Saved views, kind chips, card shapes, top replies, "also on" chips.
const thread = { id: 1, name: 'Retro computing', interests: 'Amiga', instructions: '', avoid: '', strictness: 'balanced', child_friendly: false, enabled: true, last_run: 1700000000, last_status: 'ok', error: '', new_count: 0, pick_count: 6, source_count: 2 };
const post = (id: number, title: string, extra: Record<string, unknown> = {}) => ({ pick_id: id, post_id: id + 100, thread_id: 1, thread_name: 'Retro computing', platform: 'lemmy', community: `c/r${id}`, author: 'ann', title, excerpt: `About ${title}`, url: `https://example.org/p/${id}`, link_url: '', domain: '', media: [], score: 12, comments: 3, published_at: Math.floor(Date.now() / 1000) - 3600, why: 'Matches Amiga', relevance: 80, saved: false, verdict: '', nsfw: false, spoiler: false, fun: 0, kind: '', wildcard: false, top: [], elsewhere: [], velocity: 1, badges: [], ...extra });

const today = [
	post(1, 'Which Amiga should I buy?', { kind: 'question', top: [{ author: 'kim', body: 'An A1200 with a CF card, every time.', score: 40 }] }),
	post(2, 'My cat sat on the floppy drive', { kind: 'humor', fun: 92, wildcard: true, badges: ['hot'], top: [{ author: 'lee', body: 'Purrfect sector alignment', score: 99 }, { author: 'max', body: 'Classic', score: 4 }] }),
	post(3, 'Commodore history piece', { kind: 'deepdive', link_url: 'https://history.example/c64', domain: 'history.example', elsewhere: [{ platform: 'hackernews', community: 'HN', url: 'https://news.example/1', comments: 212 }] }),
	post(4, 'Plain text post', { kind: 'news' })
];

async function setup(page: Page) {
	const calls: URLSearchParams[] = [];
	await page.route('**/api/hub/commonroom/**', async (route) => {
		const url = new URL(route.request().url()), path = url.pathname.replace('/api/hub/commonroom', '');
		if (path === '/threads') return route.fulfill({ json: { threads: [thread], platforms: [], child: false } });
		if (path === '/visit') return route.fulfill({ json: { boundary: 50 } });
		if (path === '/feed') {
			calls.push(url.searchParams);
			const mode = url.searchParams.get('mode'), kind = url.searchParams.get('kind');
			if (mode === 'today') return route.fulfill({ json: { posts: kind ? today.filter((p) => p.kind === kind) : today, next_cursor: '', has_more: false, building: false, mode, end: true } });
			if (mode === 'saved') return route.fulfill({ json: { posts: [post(9, 'Starred keeper', { saved: true })], next_cursor: '', has_more: false, building: false, mode, end: true } });
			if (mode === 'hot') return route.fulfill({ json: { posts: [post(5, 'Hot thing', { badges: ['hot'] })], next_cursor: '', has_more: false, building: false, mode, end: true } });
			return route.fulfill({ json: { posts: [], next_cursor: '', has_more: false, building: false } });
		}
		return route.fulfill({ json: { ok: true } });
	});
	return calls;
}

test('Today is the default view, mixes card shapes and ends with a way onward', async ({ page }) => {
	const calls = await setup(page);
	await freshPage(page, '/common-room');
	await expect(page.getByRole('tab', { name: 'Today' })).toHaveAttribute('aria-selected', 'true');
	await expect(page.locator('[data-variant="question"]')).toContainText('Top answer');
	await expect(page.locator('[data-variant="question"]')).toContainText('An A1200 with a CF card');
	const take = page.locator('[data-variant="take"]');
	await expect(take).toContainText('Purrfect sector alignment');
	await expect(take).toContainText('Classic'); // two replies on a take
	await expect(take).toContainText('🔥 Hot');
	await expect(take).toContainText('🃏 Wildcard');
	const link = page.locator('[data-variant="link"]');
	await expect(link.getByRole('link', { name: /HN · 💬 212/ })).toHaveAttribute('href', 'https://news.example/1');
	await expect(page.locator('[data-variant="text"]')).toContainText('Plain text post');
	await expect(page.getByRole('heading', { name: "That's today." })).toBeVisible();
	expect(calls.every((q) => q.get('mode') === 'today' && !q.get('section'))).toBe(true);
	await page.getByRole('button', { name: /What's hot/ }).click();
	await expect(page.getByRole('tab', { name: 'Hot' })).toHaveAttribute('aria-selected', 'true');
	await expect(page.getByRole('link', { name: 'Hot thing' })).toBeVisible();
});

test('kind chips filter, Saved lists starred posts, and the view is remembered', async ({ page }) => {
	const calls = await setup(page);
	await freshPage(page, '/common-room');
	await page.getByRole('button', { name: /Questions/ }).click();
	await expect.poll(() => calls.at(-1)?.get('kind')).toBe('question');
	await expect(page.locator('.card')).toHaveCount(1);
	await page.getByRole('button', { name: /Questions/ }).click(); // toggles off
	await expect(page.locator('.card')).toHaveCount(4);
	await page.getByRole('tab', { name: 'Saved' }).click();
	await expect(page.getByRole('link', { name: 'Starred keeper' })).toBeVisible();
	await page.reload();
	await expect(page.getByRole('tab', { name: 'Saved' })).toHaveAttribute('aria-selected', 'true');
});

test('the new controls fit a phone without sideways page scroll', async ({ page }) => {
	await setup(page);
	await page.setViewportSize({ width: 375, height: 812 });
	await freshPage(page, '/common-room');
	await expect(page.locator('.card').first()).toBeVisible();
	expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test('a saved post video plays on the card and with controls in the post view', async ({ page }) => {
	await setup(page);
	const dir = mkdtempSync(join(tmpdir(), 'crv-'));
	execFileSync('ffmpeg', ['-v', 'error', '-f', 'lavfi', '-i', 'testsrc=size=160x284:rate=10', '-t', '1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', join(dir, 'v.mp4')]);
	await page.route('**/api/hub/commonroom/posts/107/video/0', (route) => route.fulfill({ body: readFileSync(join(dir, 'v.mp4')), contentType: 'video/mp4' }));
	const vid = post(7, 'Therapy dog timelapse', { kind: 'oddity', media: [{ kind: 'video', url: 'https://v.redd.it/x/HLSPlaylist.m3u8', src: '/api/hub/commonroom/posts/107/video/0', poster: '', w: 720, h: 1280 }] });
	await page.route('**/api/hub/commonroom/feed*', (route) => route.fulfill({ json: { posts: [vid], next_cursor: '', has_more: false, building: false, mode: 'today', end: true } }));
	await page.route('**/api/hub/commonroom/posts/107', (route) => route.fulfill({ json: { ...vid, body: '' } }));
	await page.route('**/api/hub/commonroom/posts/107/comments*', (route) => route.fulfill({ json: { comments: [], url: vid.url, fetched_at: 0, supported: false } }));
	await freshPage(page, '/common-room');
	const card = page.locator('[data-variant="media"] video');
	await expect(card).toHaveAttribute('src', '/api/hub/commonroom/posts/107/video/0');
	expect(await card.evaluate((v: HTMLVideoElement) => v.muted && v.loop && !v.controls)).toBe(true);
	await page.getByRole('link', { name: 'Therapy dog timelapse' }).click();
	const full = page.getByRole('dialog').locator('video');
	await expect(full).toHaveAttribute('src', '/api/hub/commonroom/posts/107/video/0');
	expect(await full.evaluate((v: HTMLVideoElement) => v.controls)).toBe(true);
});
