import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { join } from 'node:path';

// The Statistics page: overview tiles, per-category table, daily activity table.
const DATA = process.env.BT_E2E_DATA;
const BACKEND = process.env.BT_E2E_BACKEND;

test.skip(!DATA || !BACKEND, 'needs scripts/e2e.sh (seeds candidates in the throwaway data dir)');

test.beforeAll(() => {
	execFileSync(join(BACKEND!, '.venv/bin/python'), ['-c', `
from sqlmodel import select
from bt.db import init_db, session
from bt.models import Candidate, Category, Download, Feedback, Profile, ReencodeJob, ReencodeResult, Video, WatchHistory, now
init_db()
with session() as s:
    if not s.exec(select(Profile)).first():
        s.add(Profile(name="Family", role="admin"))
    s.commit()
    pid = s.exec(select(Profile)).first().id
    cat = s.exec(select(Category).where(Category.name == "Stats test")).first()
    if not cat:
        s.add(Category(profile_id=pid, name="Stats test", brief="b"))
        s.commit()
        cat = s.exec(select(Category).where(Category.name == "Stats test")).one()
    for vid, title in [("statvid00001", "Watched on the stats test list"), ("statvid00002", "Still queued")]:
        s.merge(Video(id=vid, title=title, channel_id="UCtest", channel_name="Test", duration_s=600))
        if not s.exec(select(Candidate).where(Candidate.category_id == cat.id,
                                             Candidate.video_id == vid)).first():
            s.add(Candidate(category_id=cat.id, video_id=vid, status="accepted", score=75, judged_at=now()))
    if not s.exec(select(WatchHistory).where(WatchHistory.video_id == "statvid00001")).first():
        s.add(WatchHistory(profile_id=pid, video_id="statvid00001", category_id=cat.id,
                           finished=True, duration_s=600, position_s=600))
    s.merge(Video(id="statvid00003", title="A rejected stats video", channel_name="Stats channel", duration_s=300))
    if not s.exec(select(Candidate).where(Candidate.category_id == cat.id, Candidate.video_id == "statvid00003")).first():
        s.add(Candidate(category_id=cat.id, video_id="statvid00003", status="rejected", score=12,
                        summary="Too much sensationalism for this category", red_flags=["Sensationalism"], judged_at=now()))
    s.merge(Download(video_id="statvid00002", status="done", height=720, size_bytes=80_000_000, vcodec="h264 (NVENC)"))
    if not s.exec(select(ReencodeResult).where(ReencodeResult.video_id == "statvid00002")).first():
        s.add(ReencodeResult(video_id="statvid00002", outcome="saved", original_bytes=120_000_000, output_bytes=80_000_000))
    for i in range(45):
        vid = f"statpage{i:03}"
        s.merge(Video(id=vid, title=f"Pending stats pick {i:02}", duration_s=180))
        if not s.exec(select(Candidate).where(Candidate.category_id == cat.id, Candidate.video_id == vid)).first():
            s.add(Candidate(category_id=cat.id, video_id=vid, status="found"))
    other = s.exec(select(Profile).where(Profile.name == "Another stats profile")).first()
    if not other:
        other = Profile(name="Another stats profile")
        s.add(other)
    s.commit()
    second = s.exec(select(Category).where(Category.profile_id == other.id)).first()
    if not second:
        second = Category(profile_id=other.id, name="Another profile category with a deliberately long descriptive name that must stay inside the panel", brief="b")
        s.add(second)
    s.commit()
    if not s.exec(select(Candidate).where(Candidate.category_id == second.id, Candidate.video_id == "statvid00001")).first():
        s.add(Candidate(category_id=second.id, video_id="statvid00001", status="accepted", score=75, judged_at=now()))
    s.commit()
`], { cwd: BACKEND, env: { ...process.env, BT_DATA_DIR: DATA! } });
});

test.beforeEach(async ({ page }) => {
    // The throwaway library has no thumbnail cache; keep these UI tests offline.
    await page.route('**/api/thumb/**', route => route.fulfill({ status: 200, contentType: 'image/svg+xml',
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="160" height="90"><rect width="160" height="90" fill="#30284d"/></svg>' }));
});

test('stats page shows queue, category and daily numbers', async ({ page, request }) => {
	await page.goto('/stats');
	await page.evaluate(() => localStorage.clear());
	const profiles = await (await request.get('/api/profiles')).json();
	await page.evaluate((pid: string) => localStorage.setItem('bt.profile', pid), String(profiles[0].id));
	await page.reload();

	await expect(page.getByRole('heading', { name: 'Statistics' })).toBeVisible();
	// overview tiles render real numbers, not skeletons
	await expect(page.getByText('Ready in queues')).toBeVisible();
	await expect(page.getByText('Waiting to be judged')).toBeVisible();

	// per-category row: one watched, one still queued
	const row = page.locator('tr', { hasText: 'Stats test' });
	await expect(row).toBeVisible();
	await expect(row.locator('td').nth(3)).toHaveText('1');
	await expect(row.getByText('Still queued')).toHaveCount(0); // titles never leak into the counts table
	const ready = await row.locator('td').nth(1).innerText();
	expect(ready).toMatch(/1\/20/); // in_queue/target with gauge

	// today's row in the daily table counts the watch (rows are newest first)
	const dailySection = page.locator('section.panel', { hasText: 'Daily activity' });
	const today = dailySection.locator('tbody tr').first();
	await expect(today).toBeVisible();
	await expect(today.locator('td').nth(2)).toContainText('1');
	// the per-category breakdown shows which list it was watched on
	expect(await today.locator('td').nth(2).innerText()).toMatch(/✨ ×1/);

	// range chips re-fetch without breaking
	await page.getByRole('button', { name: '7 days' }).click();
	await expect(page.getByRole('heading', { name: 'Daily activity' })).toContainText('last 7 days');
	const r = await (await request.get('/api/stats/page?days=7')).json();
	expect(r.daily).toHaveLength(7);
	const cat = r.categories.find((c: { name: string }) => c.name === 'Stats test');
	expect(cat.counts.watched).toBe(1);
	expect(cat.counts.in_queue).toBe(1);
});

test('statistics drill into verdicts, offline copies and paginated category records', async ({ page, request }) => {
    const profiles = await (await request.get('/api/profiles')).json();
    await page.goto('/stats');
    await page.evaluate((pid: string) => localStorage.setItem('bt.profile', pid), String(profiles[0].id));
    await page.reload();
    await expect(page.getByRole('heading', { name: 'Your offline library' })).toBeVisible();
    await expect(page.getByRole('img', { name: /^Curator decisions:/ })).toBeVisible();
    await expect(page.getByRole('img', { name: /^Offline video quality:/ })).toBeVisible();
    await expect(page.getByRole('img', { name: /^Daily discoveries/ })).toBeVisible();

    await page.getByRole('button', { name: 'Explore Judge rejected', exact: true }).click();
    let dialog = page.getByRole('dialog', { name: 'Judge rejected', exact: true });
    await expect(dialog.getByText('A rejected stats video', { exact: true })).toBeVisible();
    await expect(dialog.getByText('Too much sensationalism for this category', { exact: true })).toBeVisible();
    await dialog.locator('li', { hasText: 'A rejected stats video' }).getByRole('button', { name: 'View verdict' }).click();
    await expect(page.getByRole('dialog', { name: 'A rejected stats video' })).toBeVisible();
    await page.getByRole('button', { name: 'Close', exact: true }).click();

    await page.getByRole('button', { name: 'Explore Offline copies', exact: true }).click();
    dialog = page.getByRole('dialog', { name: 'Offline copies', exact: true });
    await expect(dialog.getByText('Still queued', { exact: true })).toBeVisible();
    await expect(dialog.getByText('720p', { exact: true })).toBeVisible();
    await expect(dialog.locator('li', { hasText: 'Still queued' }).getByRole('link', { name: 'Play', exact: true })).toHaveAttribute('href', '/watch/statvid00002');
    await page.screenshot({ path: '/tmp/bt-stats-offline-explorer.png' });
    await dialog.getByRole('button', { name: 'Close', exact: true }).click();

    await page.getByRole('button', { name: 'In review · Stats test: 45', exact: true }).click();
    dialog = page.getByRole('dialog', { name: 'In review · Stats test', exact: true });
    await expect(dialog.getByText(/Showing 40 of 45/)).toBeVisible();
    await dialog.getByRole('button', { name: 'Show more', exact: true }).click();
    await expect(dialog.getByText(/Showing 45 of 45/)).toBeVisible();
    await expect(dialog.getByRole('button', { name: 'Show more', exact: true })).toHaveCount(0);
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);

    await page.getByRole('button', { name: /^720p/ }).click();
    dialog = page.getByRole('dialog', { name: 'Offline copies · 720p', exact: true });
    await expect(dialog.getByText('Still queued', { exact: true })).toBeVisible();
    await dialog.getByRole('button', { name: 'Close', exact: true }).click();

    await page.getByRole('button', { name: /Channels in your offline library/ }).click();
    dialog = page.getByRole('dialog', { name: 'Offline channels', exact: true });
    const channel = dialog.locator('li').filter({ has: page.locator('a[href="/channel/UCtest"]') });
    await expect(channel.getByText('Test', { exact: true })).toBeVisible();
    await expect(channel.getByRole('link', { name: 'View channel' })).toHaveAttribute('href', '/channel/UCtest');
    await dialog.getByRole('button', { name: 'Close', exact: true }).click();

    const dailySection = page.locator('section.panel', { hasText: 'Daily activity' });
    await expect(dailySection.locator('tbody tr')).toHaveCount(1);
    await dailySection.getByRole('button', { name: 'Show quiet days', exact: true }).click();
    await expect(dailySection.locator('tbody tr')).toHaveCount(30);
    await dailySection.locator('tbody tr').first().locator('td').nth(2).getByRole('button').click();
    dialog = page.getByRole('dialog', { name: 'Watched', exact: true });
    await expect(dialog.getByText('Watched on the stats test list', { exact: true })).toBeVisible();
    await dialog.getByRole('button', { name: 'Close', exact: true }).click();
});


for (const width of [1920, 1280, 1024, 768, 390]) {
    test(`statistics stay within the viewport at ${width}px`, async ({ page, request }) => {
        await page.setViewportSize({ width, height: 1000 });
        const profiles = await (await request.get('/api/profiles')).json();
        await page.goto('/stats');
        await page.evaluate((id: string) => localStorage.setItem('bt.profile', id), String(profiles[0].id));
        await page.reload();
        await expect(page.getByText('Ready in queues')).toBeVisible();
        const other = page.locator('tr', { hasText: 'Another profile category' });
        await expect(other).toContainText('Another stats profile');
        await expect(other.locator('td').nth(1)).toContainText('1/20');
        const geometry = await page.evaluate(() => ({
            document: document.documentElement.scrollWidth,
            panels: [...document.querySelectorAll('section.panel')].map(el => el.getBoundingClientRect().right),
            tables: [...document.querySelectorAll('.twrap')].map(el => ({ width: el.clientWidth, content: el.scrollWidth }))
        }));
        expect(geometry.document).toBeLessThanOrEqual(width + 1);
        expect(geometry.panels.every(right => right <= width + 1)).toBe(true);
        if (width >= 1024) expect(geometry.tables.every(t => t.content <= t.width + 1)).toBe(true);
        if (width === 1280 || width === 390) await page.screenshot({ path: `/tmp/bt-stats-${width}.png`, fullPage: true });
        const first = await (await request.get('/api/stats/page', { headers: { 'X-Profile-Id': String(profiles[0].id) } })).json();
        const second = await (await request.get('/api/stats/page', { headers: { 'X-Profile-Id': String(profiles[profiles.length - 1].id) } })).json();
        expect(first).toEqual(second);
    });
}
