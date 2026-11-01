import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { join } from 'node:path';
import { expectFocus, freshPage, moveUntil } from './helpers';

// "Seen it" on a category card: the video disappears from the feed and is marked watched.
const DATA = process.env.BT_E2E_DATA;
const BACKEND = process.env.BT_E2E_BACKEND;

test.skip(!DATA || !BACKEND, 'needs scripts/e2e.sh (seeds candidates in the throwaway data dir)');

let catId = 0;
async function getCatId(request: import('@playwright/test').APIRequestContext) {
	if (!catId) {
		const cats = await (await request.get('/api/categories')).json();
		catId = cats.find((c: { name: string }) => c.name === 'Seen it test').id;
	}
	return catId;
}

test.beforeAll(() => {
	execFileSync(join(BACKEND!, '.venv/bin/python'), ['-c', `
from datetime import timedelta
from sqlmodel import select
from bt.db import init_db, session
from bt.models import Candidate, Category, Profile, Video, now
init_db()
with session() as s:
    if not s.exec(select(Profile)).first():
        s.add(Profile(name="Family"))
    s.commit()
    pid = s.exec(select(Profile)).first().id
    cat = s.exec(select(Category).where(Category.name == "Seen it test")).first()
    if not cat:
        s.add(Category(profile_id=pid, name="Seen it test", brief="b"))
        s.commit()
        cat = s.exec(select(Category).where(Category.name == "Seen it test")).one()
    for vid, title, ch in [("seenit00000", "Video the family has seen", "UCtest"),
                          ("other000000", "Video still fresh", "UCtest"),
                          ("badch000001", "Video from a disliked channel", "UCbad"),
                          ("boringvid000", "Video that is dull", "UCtest")]:
        s.merge(Video(id=vid, title=title, channel_id=ch,
                      channel_name="Test" if ch == "UCtest" else "Disliked", duration_s=600,
                      view_count=123456,
                      published_at=(now() - timedelta(days=3)) if vid == "other000000" else None))
        if not s.exec(select(Candidate).where(Candidate.category_id == cat.id,
                                             Candidate.video_id == vid)).first():
            s.add(Candidate(category_id=cat.id, video_id=vid, status="accepted", score=80, summary="x"))
    s.commit()
`], { cwd: BACKEND, env: { ...process.env, BT_DATA_DIR: DATA! } });
});

test('Seen it removes the card and keeps the rest', async ({ page, request }) => {
	const id = await getCatId(request);
	await freshPage(page, `/category/${id}`);
	await expect(page.getByRole('heading', { name: 'Seen it test' })).toBeVisible();
	await expect(page.getByRole('link', { name: 'Video the family has seen' })).toBeVisible();

	// Card actions stay hidden until the card is hovered or focused (no accidental clicks).
	// Whichever card holds keyboard focus shows its actions; every other card keeps them hidden.
	const idle = page.locator('.cand:not(:has(:focus)) .actions').first();
	await expect(idle).toHaveCSS('opacity', '0');
	const card = page.locator('.cand', { hasText: 'Video the family has seen' });
	await card.hover();
	const seenBtn = card.getByRole('button', { name: 'Seen it' });
	await expect(seenBtn).toHaveCSS('opacity', '1'); // hovering the card reveals them
	// the duration badge lifts above the hover summary overlay, not over its text
	const pos = await card.evaluate((el) => {
		const sum = el.querySelector('.sum') as HTMLElement;
		const dur = el.querySelector('.dur') as HTMLElement;
		return { sumTop: sum.getBoundingClientRect().top, durBottom: dur.getBoundingClientRect().bottom };
	});
	expect(pos.durBottom).toBeLessThanOrEqual(pos.sumTop + 30); // 30px = overlay's transparent gradient start
	await seenBtn.click();

	await expect(page.getByRole('link', { name: 'Video the family has seen' })).toHaveCount(0);
	await expect(page.getByRole('link', { name: 'Video still fresh' })).toBeVisible();
	await expect(page.getByText("Marked as seen. The AI won't suggest it again.")).toBeVisible();

	// server side: gone from the feed, watched in history
	const feed = await (await request.get(`/api/feed/category/${id}`)).json();
	expect(feed.map((c: { video: { id: string } }) => c.video.id)).not.toContain('seenit00000');
	expect(feed.map((c: { video: { id: string } }) => c.video.id)).toContain('other000000');
	const hist = await (await request.get('/api/history/seenit00000')).json();
	expect(hist.finished).toBe(true);
});

test("Don't like channel clears its cards and blocks it", async ({ page, request }) => {
	const id = await getCatId(request);
	await freshPage(page, `/category/${id}`);
	await expect(page.getByRole('link', { name: 'Video from a disliked channel' })).toBeVisible();

	const card = page.locator('.cand', { hasText: 'Video from a disliked channel' });
	await card.hover(); // the action strip is hover-revealed
	await card.getByRole('button', { name: "Don't like channel" }).click();

	await expect(page.getByRole('link', { name: 'Video from a disliked channel' })).toHaveCount(0);
	await expect(page.getByText('No more from Disliked. The AI will skip that channel.')).toBeVisible();
	// the other channel's cards are untouched (only cards not yet marked seen in test 1)
	await expect(page.getByRole('link', { name: 'Video still fresh' })).toBeVisible();

	const feed = await (await request.get(`/api/feed/category/${id}`)).json();
	expect(feed.some((c: { video: { id: string } }) => c.video.id === 'badch000001')).toBe(false);
	const chans = await (await request.get('/api/channels')).json();
	expect(chans.find((c: { id: string }) => c.id === 'UCbad')?.blocked).toBe(true);
});

test('remote reaches the action strip: Down from a card dives in', async ({ page, request }) => {
	const id = await getCatId(request);
	await freshPage(page, `/category/${id}`);
	// Wait for the actual card, then reach it with the remote. A broad /Video /
	// match can stop on the Video AI navigation entry while the page is loading.
	const card = page.getByRole('link', { name: 'Video still fresh', exact: true });
	await expect(card).toBeVisible();
	await moveUntil(page, 'ArrowDown', /Video still fresh/);
	await expect(card).toBeFocused();
	// Down dives into that card's action buttons
	await page.keyboard.press('ArrowDown');
	await expectFocus(page, /Why picked|Details|Seen it|Don't like channel|Save offline/);
	// Activating one works (opens the candidate drawer)
	await page.keyboard.press('Enter');
	await expect(page.getByRole('dialog')).toBeVisible();
	await page.keyboard.press('Backspace');
	await expect(page.getByRole('dialog')).toHaveCount(0);
});

test('Home: hover reveals actions, age next to views, Seen it works', async ({ page, request }) => {
	const id = await getCatId(request);
	// at this point only 'Video still fresh' is still an accepted suggestion in this category
	await freshPage(page, '/');
	const card = page.locator('.card', { hasText: 'Video still fresh' });
	await expect(card).toBeVisible();

	// age shows next to the view count
	const sub = card.locator('.sub');
	await expect(sub.getByText(/views/)).toBeVisible();
	await expect(sub.getByText(/ago/)).toBeVisible();

	// hover reveals the action strip
	await card.hover();
	const btn = card.getByRole('button', { name: 'Seen it' });
	await expect(btn).toHaveCSS('opacity', '1');
	await btn.click();

	await expect(page.getByText("Marked as seen. The AI won't suggest it again.")).toBeVisible();
	await expect(card).toHaveCount(0);
	const feed = await (await request.get(`/api/feed/category/${id}`)).json();
	expect(feed.map((c: { video: { id: string } }) => c.video.id)).not.toContain('other000000');
});

test('thumbnail rating opens the full feedback dialog and sends a dislike', async ({ page, request }) => {
	const id = await getCatId(request);
	await freshPage(page, `/category/${id}`);
	const card = page.locator('.cand', { hasText: 'Video that is dull' });
	await expect(card).toBeVisible();
	await card.hover();
	await card.getByRole('button', { name: 'Rate video' }).click();
	const dialog = page.getByRole('dialog', { name: 'How was it?' });
	await expect(dialog).toBeVisible();
	await expect(dialog.getByText('Video that is dull')).toBeVisible();
	await dialog.getByRole('button', { name: /Not for us/ }).click();
	await dialog.getByRole('button', { name: 'Boring', exact: true }).click();
	await dialog.getByLabel('Anything else? (optional)').fill('Too repetitive');
	await dialog.getByRole('button', { name: 'Send feedback' }).click();
	await expect(card).toHaveCount(0);
	await expect(page.getByText('Thanks. The AI will avoid videos like this.')).toBeVisible();
	const fb = await (await request.get('/api/feedback')).json();
	const mine = fb.find((f: { video_id: string }) => f.video_id === 'boringvid000');
	expect(mine?.rating).toBe(-1);
	expect(mine?.chips).toEqual(['Boring']);
	expect(mine?.text).toBe('Too repetitive');
	// the suggestion is dismissed, so it is gone from the feed
	const feed = await (await request.get(`/api/feed/category/${id}`)).json();
	expect(feed.some((c: { video: { id: string } }) => c.video.id === 'boringvid000')).toBe(false);
});

test('Continue watching Seen it and Boring stay dismissed after reload', async ({ page, request }) => {
	const id = await getCatId(request);
	execFileSync(join(BACKEND!, '.venv/bin/python'), ['-c', `
from sqlmodel import select
from bt.db import session
from bt.models import Profile, Video, WatchHistory
with session() as s:
    pid = s.exec(select(Profile)).first().id
    for vid, title in [('cwseen00002', 'Continue seen example'), ('cwboring002', 'Continue boring example'), ('cwkeep00002', 'Continue keep example')]:
        s.merge(Video(id=vid, title=title, duration_s=600))
        s.add(WatchHistory(profile_id=pid, video_id=vid, position_s=100, duration_s=600, category_id=${id}))
    s.commit()
`], { cwd: BACKEND, env: { ...process.env, BT_DATA_DIR: DATA! } });
	await freshPage(page, '/');
	for (const [title, action] of [['Continue seen example', 'Seen it'], ['Continue boring example', 'Rate video']]) {
		const card = page.locator('.card', { hasText: title });
		await expect(card).toBeVisible();
		await card.hover();
		await card.getByRole('button', { name: action }).click();
		if (action === 'Rate video') {
			const dialog = page.getByRole('dialog', { name: 'How was it?' });
			await dialog.getByRole('button', { name: /Not for us/ }).click();
			await dialog.getByRole('button', { name: 'Send feedback' }).click();
		}
		await expect(card).toHaveCount(0);
	}
	await page.reload();
	await expect(page.locator('.card', { hasText: 'Continue seen example' })).toHaveCount(0);
	await expect(page.locator('.card', { hasText: 'Continue boring example' })).toHaveCount(0);
	await expect(page.locator('.card', { hasText: 'Continue keep example' })).toBeVisible();
});

test('positive thumbnail feedback keeps the video and preserves its playlist', async ({ page, request }) => {
	const id = await getCatId(request);
	execFileSync(join(BACKEND!, '.venv/bin/python'), ['-c', `
from bt.db import session
from bt.models import Video, Candidate
with session() as s:
    s.merge(Video(id="goodvid0001", title="A good video", channel_id="UCgood", duration_s=600))
    s.add(Candidate(category_id=${id}, video_id="goodvid0001", status="accepted", score=90))
    s.commit()
`], { cwd: BACKEND, env: { ...process.env, BT_DATA_DIR: DATA! } });
	await freshPage(page, '/');
	const card = page.locator('.card').filter({ has: page.locator('a[href^="/watch/goodvid0001"]') }).first();
	await expect(card).toBeVisible();
	await card.hover();
	await card.getByRole('button', { name: 'Rate video' }).click();
	let dialog = page.getByRole('dialog', { name: 'How was it?' });
	await dialog.getByRole('button', { name: 'Skip' }).click();
	await expect(card).toBeVisible();
	await card.getByRole('button', { name: 'Rate video' }).click();
	dialog = page.getByRole('dialog', { name: 'How was it?' });
	await dialog.getByRole('button', { name: /Good/ }).click();
	await dialog.getByLabel('Anything else? (optional)').fill('More like this');
	await dialog.getByRole('button', { name: 'Send feedback' }).click();
	await expect(dialog).toHaveCount(0);
	await expect(card).toBeVisible();
	const fb = await (await request.get('/api/feedback')).json();
	const mine = fb.find((f: { video_id: string }) => f.video_id === 'goodvid0001');
	expect(mine?.rating).toBe(1);
	expect(mine?.category_id).toBe(id);
	expect(mine?.text).toBe('More like this');
});

for (const [action, id, title] of [
	['Seen it', 'cwseen00001', 'Continue seen test'],
	['Rate video', 'cwboring001', 'Continue boring test']
] as const) {
	test(`Continue watching: ${action} stays removed after a reload`, async ({ page, request }) => {
		execFileSync(join(BACKEND!, '.venv/bin/python'), ['-c', `
from bt.db import session
from bt.models import Video
with session() as s:
    s.merge(Video(id=${JSON.stringify(id)}, title=${JSON.stringify(title)}, duration_s=600))
    s.commit()
`], { cwd: BACKEND, env: { ...process.env, BT_DATA_DIR: DATA! } });
		const progress = { video_id: id, position_s: 100, duration_s: 600 };
		expect((await request.post('/api/history', { data: progress })).ok()).toBeTruthy();
		await freshPage(page, '/');
		const card = page.locator('.card').filter({ has: page.locator(`a[href^="/watch/${id}"]`) }).first();
		await expect(card).toBeVisible();
		await card.hover();
		await card.getByRole('button', { name: action }).click();
		if (action === 'Rate video') {
			const dialog = page.getByRole('dialog', { name: 'How was it?' });
			await dialog.getByRole('button', { name: /Not for us/ }).click();
			await dialog.getByRole('button', { name: 'Send feedback' }).click();
		}
		await expect(card).toHaveCount(0);
		// An old player tab may report progress after the button was clicked.
		await request.post('/api/history', { data: progress });
		await page.reload();
		await expect(page.locator(`a[href^="/watch/${id}"]`)).toHaveCount(0);
	});
}
