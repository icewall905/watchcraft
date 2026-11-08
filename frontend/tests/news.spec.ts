import { test, expect } from '@playwright/test';
import { freshPage } from './helpers';

test('news is story first and opens the profile story view', async ({ page }) => {
	await page.route('**/api/hub/news*', (route) => route.fulfill({ json: { topics: [], groups: [{
		key: '51', story_id: 51, title: 'Harbour plan advances', latest: 1783000000,
		items: [{ id: 51, title: 'Harbour plan advances', summary: 'Council votes today', provider: 'feed', publisher: 'Local',
			local_image: false, state: {}, url: 'https://example.test/a' },
		{ id: 52, title: 'More on the harbour vote', publisher: 'Daily', provider: 'feed', state: {}, url: 'https://example.test/b' }]
	}] } }));
	await page.route('**/api/hub/stories/51', (route) => route.fulfill({ json: {
		id: 51, title: 'Harbour plan advances', items: [{ id: 51, title: 'Harbour plan advances', summary: 'Council votes today', publisher: 'Local', provider: 'feed', state: {}, url: 'https://example.test/a' }]
	} }));
	await page.route('**/api/hub/items/51', (route) => route.fulfill({ json: {
        id: 51, title: 'Harbour plan advances', body: 'The complete archived council message.',
        enrichment: { summary: 'A quick AI overview.' }, local_assets: ['image'],
        capture: { status: 'complete', summary_status: 'ready', error: '' }
    } }));
    await page.route('**/api/hub/items/51/assets/image', (route) => route.fulfill({
        contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="300" height="1200"><rect width="300" height="1200" fill="red"/></svg>'
    }));
	await freshPage(page, '/news');
	await expect(page.getByText('+1 source', { exact: true })).toBeVisible();
	await page.getByRole('button', { name: '1 more source' }).click();
	await expect(page.getByRole('link', { name: 'Daily: More on the harbour vote' })).toHaveAttribute('href', '/item/52');
	await page.getByRole('link', { name: 'Harbour plan advances' }).click();
	await expect(page.getByRole('heading', { name: 'Harbour plan advances', level: 1 })).toBeVisible();
	await expect(page.getByText('1 source', { exact: true })).toBeVisible();
    await expect(page.getByText('The complete archived council message.')).toBeVisible();
    await expect(page.getByText('A quick AI overview.')).toBeVisible();
    await expect(page.getByRole('link', { name: 'Harbour plan advances' })).toHaveAttribute('href', '/item/51');
    const image = page.getByRole('img', { name: 'Harbour plan advances' });
    await expect(image).toBeVisible();
    const size = await image.boundingBox();
    expect(size!.height / size!.width).toBeCloseTo(4, 1);
    await expect(page.getByRole('link', { name: 'Open full-size archived image' })).toHaveAttribute('href', /^blob:/);
    await page.screenshot({ path: '/tmp/watchcraft-news-archive.png', fullPage: true });
});

test('news is a scrolling weekly mix that loads more stories and records what was seen', async ({ page }) => {
	const groups = Array.from({ length: 100 }, (_, index) => ({
		key: `week-${index + 1}`, title: `Weekly story ${index + 1}`, latest: 1783000000 - index, new: index < 3,
		items: [{ id: index + 1, title: `Weekly story ${index + 1}`, summary: `Short overview ${index + 1}`,
			provider: 'feed', publisher: 'Local', local_image: false, state: {}, url: `https://example.test/${index + 1}` }]
	}));
	const requests: URL[] = [];
	const seen: { item_ids: number[]; visit_at: number }[] = [];
	await page.route('**/api/hub/news/seen', (route) => { seen.push(route.request().postDataJSON()); return route.fulfill({ json: { recorded: 1 } }); });
	await page.route('**/api/hub/news?*', (route) => { requests.push(new URL(route.request().url())); return route.fulfill({ json: {
		groups, topics: [], mode: 'foryou', new_count: 3, supply: { target: 100, window_days: 7, available: 100, shortfall: 0, met: true, pending_review: 0, as_of: 1783000000 },
		article_count: 100, story_count: 100
	} }); });
	await freshPage(page, '/news');
	await expect(page.getByRole('tab', { name: 'For you' })).toHaveAttribute('aria-selected', 'true');
	await expect(page.getByRole('tab', { name: 'This week' })).toHaveAttribute('aria-selected', 'true');
	await expect(page.getByText('100 articles across 100 stories · 3 new since your last visit')).toBeVisible();
	const first = requests.at(-1)!;
	expect(first.searchParams.get('mode')).toBe('foryou');
	expect(first.searchParams.get('seed')).toBeTruthy();
	await expect(page.getByRole('heading', { name: 'Weekly story 20', exact: true })).toBeVisible();
	await expect(page.getByText('Showing 20 of 100 stories')).toBeVisible();
	await page.screenshot({ path: '/tmp/watchcraft-news-week-1280.png' });
	await page.getByRole('heading', { name: 'Weekly story 20', exact: true }).scrollIntoViewIfNeeded();
	await expect(page.getByRole('heading', { name: 'Weekly story 40', exact: true })).toBeAttached();
	await page.getByRole('button', { name: 'Show more stories' }).click();
	await expect.poll(() => seen.flatMap((s) => s.item_ids).length, { timeout: 10_000 }).toBeGreaterThan(0);
	expect(seen[0].visit_at).toBe(Number(first.searchParams.get('visit_at')));
	// Same visit after a reload keeps the order; Fresh mix starts a new visit.
	await page.reload();
	await expect(page.getByText('Showing', { exact: false })).toBeVisible();
	expect(requests.at(-1)!.searchParams.get('seed')).toBe(first.searchParams.get('seed'));
	await page.evaluate(() => window.scrollTo(0, 0));
	await page.getByRole('button', { name: 'Fresh mix' }).click();
	await expect.poll(() => requests.at(-1)!.searchParams.get('seed')).not.toBe(first.searchParams.get('seed'));
	await page.getByRole('tab', { name: 'Latest' }).click();
	await expect.poll(() => requests.at(-1)!.searchParams.get('mode')).toBe('latest');
	await page.getByRole('searchbox', { name: 'Filter news' }).fill('Weekly story');
	await expect(page.getByText('Showing 20 of 100 stories')).toBeVisible();
	await page.setViewportSize({ width: 390, height: 844 });
	await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
	await page.evaluate(() => window.scrollTo(0, 0));
	await page.screenshot({ path: '/tmp/watchcraft-news-week-390.png' });
});

test('news pages stories from the server, keeps the place and counts outlets honestly', async ({ page }) => {
	const total = 45;
	const all = Array.from({ length: total }, (_, index) => ({
		key: `page-${index + 1}`, title: `Paged story ${index + 1}`, latest: 1783000000 - index,
		items: [
			{ id: index * 10 + 1, title: `Paged story ${index + 1}`, provider: 'rss', publisher: 'Wire', local_image: false, state: {}, url: `https://example.test/${index}/a` },
			{ id: index * 10 + 2, title: `Paged story ${index + 1} copy`, provider: 'rss', publisher: 'wire', local_image: false, state: {}, url: `https://example.test/${index}/b` },
			{ id: index * 10 + 3, title: `Paged story ${index + 1} elsewhere`, provider: 'rss', publisher: 'Daily', local_image: false, state: {}, url: `https://example.test/${index}/c` },
			{ id: index * 10 + 4, title: `Paged story ${index + 1} thread`, provider: 'hackernews', publisher: 'Hacker News', local_image: false, state: {}, url: `https://example.test/${index}/d` }
		]
	}));
	const requests: URL[] = [];
	await page.route('**/api/hub/news/seen', (route) => route.fulfill({ json: { recorded: 1 } }));
	await page.route('**/api/hub/news?*', (route) => {
		const url = new URL(route.request().url());
		requests.push(url);
		const limit = Number(url.searchParams.get('limit') ?? 300);
		const offset = Number((url.searchParams.get('cursor') ?? 'c0').slice(1));
		const groups = all.slice(offset, offset + limit);
		const next = offset + groups.length < total ? `c${offset + groups.length}` : null;
		return route.fulfill({ json: {
			groups, topics: [{ id: 7, name: 'Harbour', kind: 'news', keywords: [], muted: false }], mode: 'foryou', new_count: 0,
			next_cursor: next, total_stories: total - offset, total_articles: (total - offset) * 4, total_capped: false, limit
		} });
	});
	await freshPage(page, '/news');
	await expect(page.getByText(`${total * 4} articles across ${total} stories`)).toBeVisible();
	await expect(page.getByText(`Showing 20 of ${total} stories`)).toBeVisible();
	expect(requests[0].searchParams.get('limit')).toBe('30');
	expect(requests[0].searchParams.get('cursor')).toBeNull();
	await expect(page.getByText('2 outlets · 1 discussion').first()).toBeVisible();
	// Scrolling past the loaded stories asks the server for the next page with the cursor.
	await page.getByRole('button', { name: 'Show more stories' }).click();
	await expect.poll(() => requests.some((r) => r.searchParams.get('cursor') === 'c30')).toBe(true);
	// Infinite scroll keeps going while the sentinel stays near the viewport.
	await expect.poll(async () => {
		const more = page.getByRole('button', { name: 'Show more stories' });
		if (await more.isVisible()) await more.click({ timeout: 1000 }).catch(() => {});
		return page.getByRole('heading', { name: 'Paged story 45', exact: true }).count();
	}).toBe(1);
	await expect(page.getByText(`Showing ${total} of ${total} stories`)).toBeVisible();
	await expect(page.getByRole('button', { name: "You're through this mix — get a fresh one" })).toBeVisible();
	expect(new Set(await page.locator('section.story').evaluateAll((els) => els.map((e) => e.getAttribute('aria-label')))).size).toBe(total);
	// Back from an article restores the place, refetching the pages it needs.
	await page.getByRole('heading', { name: 'Paged story 40', exact: true }).scrollIntoViewIfNeeded();
	await page.route('**/api/hub/items/391', (route) => route.fulfill({ json: { id: 391, title: 'Paged story 40', state: {}, url: 'https://example.test/39/a' } }));
	const before = requests.length;
	await page.getByRole('link', { name: 'Paged story 40', exact: true }).click();
	await expect(page).toHaveURL(/\/item\/391/);
	await page.goBack();
	await expect(page.getByRole('heading', { name: 'Paged story 40', exact: true })).toBeInViewport();
	expect(requests.slice(before).some((r) => r.searchParams.get('cursor') === 'c30')).toBe(true);
	// The topic filter is applied by the server, from the first page.
	await page.getByRole('button', { name: 'Harbour', exact: true }).click();
	await expect.poll(() => requests.at(-1)!.searchParams.get('topic')).toBe('7');
	expect(requests.at(-1)!.searchParams.get('cursor')).toBeNull();
});

test('a client filter that matches nothing does not walk every server page, and a failed load drops the cursor', async ({ page }) => {
	const total = 600;
	const all = Array.from({ length: total }, (_, index) => ({
		key: `bulk-${index}`, title: `Bulk story ${index}`, latest: 1783000000 - index,
		items: [{ id: index + 1, title: `Bulk story ${index}`, provider: 'rss', publisher: 'Wire', local_image: false, state: {}, url: `https://example.test/bulk/${index}` }]
	}));
	const requests: URL[] = [];
	let failTopic = true;
	await page.route('**/api/hub/news/seen', (route) => route.fulfill({ json: { recorded: 1 } }));
	await page.route('**/api/hub/news?*', (route) => {
		const url = new URL(route.request().url());
		requests.push(url);
		if (url.searchParams.get('topic') === '7' && failTopic) return route.fulfill({ status: 500, json: { detail: 'boom' } });
		const offset = Number((url.searchParams.get('cursor') ?? 'c0').slice(1));
		const groups = all.slice(offset, offset + 30);
		return route.fulfill({ json: {
			groups, topics: [{ id: 7, name: 'Harbour', kind: 'news', keywords: [], muted: false }], mode: 'foryou', new_count: 0,
			next_cursor: offset + 30 < total ? `c${offset + 30}` : null, total_stories: total - offset, total_capped: false, limit: 30
		} });
	});
	await freshPage(page, '/news');
	await expect(page.getByText(`Showing 20 of ${total} stories`)).toBeVisible();
	await page.getByRole('searchbox', { name: 'Filter news' }).fill('zzznothingmatches');
	await page.waitForTimeout(2500);
	const paged = requests.filter((r) => r.searchParams.get('cursor')).length;
	expect(paged).toBeGreaterThan(0);
	expect(paged).toBeLessThanOrEqual(12);
	await page.waitForTimeout(1500);
	expect(requests.filter((r) => r.searchParams.get('cursor')).length).toBe(paged);
	await page.getByRole('searchbox', { name: 'Filter news' }).fill('');
	// A failed topic load must not leave the previous list's cursor behind.
	await page.getByRole('button', { name: 'Harbour', exact: true }).click();
	await expect(page.getByRole('alert')).toContainText("Couldn't load news");
	failTopic = false;
	await page.getByRole('button', { name: 'Retry' }).click();
	await expect(page.getByText(`Showing 20 of ${total} stories`)).toBeVisible();
	await page.getByRole('button', { name: 'Show more stories' }).click();
	await expect.poll(() => requests.some((r) => r.searchParams.get('topic') === '7' && r.searchParams.get('cursor') === 'c30')).toBe(true);
	const topicPages = requests.filter((r) => r.searchParams.get('topic') === '7' && r.searchParams.get('cursor'));
	expect(topicPages.every((r) => Number(r.searchParams.get('cursor')!.slice(1)) % 30 === 0)).toBe(true);
});
