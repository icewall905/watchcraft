import { test, expect } from '@playwright/test';
import { freshPage } from './helpers';

test.beforeEach(async ({ page }) => { page.on('pageerror', error => { throw error; }); });

const item = (id: number, title: string, type = 'news') => ({
	id, url: `https://example.test/${id}`, type, title, summary: `A short note about ${title}.`, author: '', publisher: 'Example Journal',
	image: '', local_image: false, published_at: null, first_seen: 1, provider: 'test', meta: {}, enrichment: {},
	state: { saved: false, favorite: false, read_later: false, archived: false, dismissed: false, read_at: null, notes: '', tags: [], rating: 0 },
	score: 60, reason: '', curator_names: [], verdict: { tier: 'feature', quality: 70, relevance: 65, final: 68, reasons: ['Matches your interests'], judged_by: 'rules', overruled: '', judged_at: 1 }
});
const edition = () => ({
	day: '2026-11-03', built_at: 1, updated_at: 1, intro: 'A measured overview [1].', intro_refs: [{ n: 1, item_id: 1 }], set_aside: 2, curated: true,
	sections: [
		{ key: 'lead', title: 'Lead', entries: [{ item_id: 1, item: item(1, 'A thoughtful lead'), sources: 3, summary: 'The day’s central thread.' }] },
		{ key: 'stories', title: 'Stories', entries: [{ item_id: 2, item: item(2, 'A second story'), sources: 2, summary: 'A concise summary.' }] },
		{ key: 'reads', title: 'Reads', entries: [{ item_id: 3, item: item(3, 'A useful read', 'article') }] },
		{ key: 'memes', title: 'Memes', entries: [{ item_id: 4, item: { ...item(4, 'A small joke', 'meme'), meme: { spoiler: 'A small joke', sensitive: false, community: 'quiet-humor' } } }] },
		{ key: 'research', title: 'Research', curator_ids: [8], curators: [{ id: 8, name: 'Materials', subject: 'new materials', report_at: 1, excerpt: 'Graphene gets cheaper.' }] },
		{ key: 'briefings', title: 'From your briefings', issue_ids: [5], issues: [{ id: 5, briefing_id: 2, name: 'Morning AI', created_at: 1, ai: true, excerpt: 'Three labs shipped new models.' }] },
		{ key: 'alerts', title: 'Alerts', count: 2 }
	]
});

const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==', 'base64');
const preview = (id: number, title: string, has_image = true) => ({ item_id: id, title, meta: 'Example Journal', has_image, at: Date.now() / 1000 });
const overview = (): { generated_at: number; pages: Record<string, unknown> } => ({ generated_at: 1, pages: {
	wire: { waiting: 12, new_today: 4, previews: [{ ...preview(11, 'Rocket lands upright'), summary: 'A reusable booster came back down on its legs.' }, preview(12, 'Moon base plans'), preview(13, 'Comet season'), preview(14, 'Satellite swarm'), preview(15, 'Probe wakes up')],
		groups: [{ key: 'topic:7', label: 'Space', href: '/news?topic=7', count: 5, image_item_id: 11 }, { key: 'topic:8', label: 'Climate', href: '/news?topic=8', count: 3, image_item_id: null }] },
	finds: { waiting: 3, new_today: 0, previews: [preview(21, 'A long read on bridges'), preview(22, 'Typography notes')],
		groups: [{ key: 'curator:2', label: 'Design desk', href: '/discover?curator=2&tab=unread', count: 2, image_item_id: 21 }] },
	memes: { waiting: 0, new_today: 0, previews: [], groups: [] },
	stacks: { waiting: 1, new_today: 0, previews: [preview(31, 'Saved for the weekend', false)], groups: [{ key: 'later', label: 'Read later', href: '/library?view=later', count: 1, image_item_id: null }] },
	alerts: { waiting: 2, new_today: 1, previews: [preview(41, 'Price drop spotted', false)], groups: [] }
} });
const mockOverview = async (page: import('@playwright/test').Page, data: ReturnType<typeof overview> = overview()) => {
	await page.route(/\/api\/hub\/today\/overview(?:\?.*)?$/, route => route.fulfill({ json: data }));
	await page.route('**/api/hub/items/*/assets/image*', route => route.fulfill({ body: PNG, contentType: 'image/png' }));
};

test('Today opens with the lead beside the masthead, with citations, reasons and curator actions', async ({ page }) => {
	const data = edition();
	await page.route('**/api/hub/items/1/overrule', route => route.fulfill({ json: { tier: 'feature', reasons: ['you promoted it'] } }));
	await page.route(/\/api\/hub\/today(?:\?.*)?$/, (route) => route.fulfill({ json: data }));
	await mockOverview(page);
	await freshPage(page, '/today');
	await expect(page.getByText('A measured overview')).toBeVisible();
	await expect(page.getByRole('link', { name: 'Source [1]' })).toHaveAttribute('href', '/item/1');
	await expect(page.getByRole('heading', { name: 'A thoughtful lead' })).toBeVisible();
	await expect(page.getByText('+2 sources')).toBeVisible();
	// The long lists moved to their pages; Today points at them instead of repeating them.
	await expect(page.getByRole('heading', { name: 'A second story' })).toHaveCount(0);
	await expect(page.getByRole('heading', { name: 'Top stories' })).toHaveCount(0);
	await page.getByRole('button', { name: 'Why?' }).first().click();
	await expect(page.getByText('Matches your interests')).toBeVisible();
	await page.getByRole('button', { name: 'Keep A thoughtful lead' }).click();
	await expect(page.getByRole('status').filter({ hasText: /^Kept in your edition\.$/ })).toBeVisible();
});

test('Where to next shows each page with pictures, counts and sub-categories', async ({ page }) => {
	const editionRequests: URL[] = [];
	await page.route(/\/api\/hub\/today(?:\?.*)?$/, route => { editionRequests.push(new URL(route.request().url())); return route.fulfill({ json: edition() }); });
	await mockOverview(page);
	await freshPage(page, '/today');
	await expect.poll(() => editionRequests.length).toBeGreaterThan(0);
	const last = editionRequests.at(-1)!.searchParams;
	expect([last.get('preview'), last.get('stories'), last.get('reads'), last.get('memes')]).toEqual(['true', '0', '0', '0']);
	const next = page.getByRole('region', { name: 'Where to next' });
	const wire = next.getByRole('article', { name: 'The Wire' });
	await expect(wire.getByText('12 stories waiting')).toBeVisible();
	await expect(wire.getByText('+4 today')).toBeVisible();
	await expect(wire.getByRole('link', { name: /Rocket lands upright/ }).first()).toHaveAttribute('href', '/item/11');
	await expect(wire.getByText('A reusable booster came back down on its legs.')).toBeVisible();
	await expect(wire.locator('.card')).toHaveCount(5);
	await expect(wire.locator('.card img').first()).toHaveAttribute('src', /^blob:/);
	await expect(wire.getByRole('link', { name: /Space\s*5/ })).toHaveAttribute('href', '/news?topic=7');
	await expect(wire.getByRole('link', { name: /Climate\s*3/ })).toHaveAttribute('href', '/news?topic=8');
	await expect(next.getByRole('article', { name: 'The Finds' }).getByRole('link', { name: /Design desk/ })).toHaveAttribute('href', '/discover?curator=2&tab=unread');
	const alerts = next.getByRole('article', { name: 'Alerts' });
	await expect(alerts.getByText('2 alerts waiting')).toBeVisible();
	await expect(alerts.getByText('Price drop spotted')).toBeVisible();
	await expect(next.getByRole('article', { name: 'The Stacks' }).getByText('Saved for the weekend')).toBeVisible();
	await expect(next.getByText(/All caught up in .*Meme Radar/)).toBeVisible();
	for (const width of [375, 1440]) {
		await page.setViewportSize({ width, height: 900 });
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
		await page.screenshot({ path: `/tmp/watchcraft-today-overview-${width}.png`, fullPage: true });
	}
});

test('Where to next opens from the last overview at once, then refreshes', async ({ page }) => {
	await page.route(/\/api\/hub\/today(?:\?.*)?$/, route => route.fulfill({ json: edition() }));
	await mockOverview(page);
	const thumbs: string[] = [];
	page.on('request', (r) => { if (r.url().includes('/assets/image')) thumbs.push(r.url()); });
	await freshPage(page, '/today');
	await expect(page.getByRole('article', { name: 'The Wire' }).getByText('12 stories waiting')).toBeVisible();
	expect(thumbs.every((u) => /\?w=(480|240|800)$/.test(u))).toBe(true);  // small previews, never full captures
	await expect.poll(() => page.evaluate(() => Object.keys(localStorage).some((k) => k.startsWith('wc-frontpage:')))).toBe(true);
	await page.unroute(/\/api\/hub\/today\/overview(?:\?.*)?$/);
	await page.route(/\/api\/hub\/today\/overview(?:\?.*)?$/, () => { /* never answers */ });
	await page.reload();
	await expect(page.getByRole('article', { name: 'The Wire' }).getByText('12 stories waiting')).toBeVisible();
});

test('Commons always shows its best posts and the mosaic gathers the best of every page', async ({ page }) => {
	const post = (id: number, title: string, media = false) => ({ pick_id: id, post_id: 500 + id, thread_id: 1, thread_name: 'Homelab', platform: 'discourse', community: 'community.home-assistant.io', author: 'a',
		title, excerpt: `Why ${title} matters.`, url: 'https://example.test', link_url: '', domain: '', media: media ? [{ kind: 'image', url: '/api/hub/commonroom/posts/1/media/0', thumb: '/api/hub/commonroom/posts/1/media/0?size=thumb' }] : [],
		score: 12, comments: 4, published_at: Date.now() / 1000 - 3600, why: '', relevance: 1, saved: false, verdict: '', nsfw: false, spoiler: false });
	await page.route(/\/api\/hub\/today(?:\?.*)?$/, route => route.fulfill({ json: edition() }));
	await mockOverview(page);
	await page.route('**/api/hub/commonroom/threads', route => route.fulfill({ json: { threads: [{ id: 1, name: 'Homelab', enabled: true, new_count: 0, pick_count: 40 }], platforms: [], child: false } }));
	await page.route('**/api/hub/commonroom/feed?*', route => route.fulfill({ json: { posts: route.request().url().includes('section=new') ? [] : [post(1, 'Wall tablet dashboard', true), post(2, 'Proxmox migration notes'), post(3, 'ESP32 print server'), post(4, 'Syncthing races')], next_cursor: '', has_more: false, building: false } }));
	await page.route('**/api/hub/commonroom/posts/*/media/*', route => route.fulfill({ body: PNG, contentType: 'image/png' }));
	await freshPage(page, '/today');
	const commons = page.getByRole('article', { name: 'The Commons' });
	await expect(commons.getByText('4 posts worth reading')).toBeVisible();
	await expect(commons.getByRole('link', { name: /Proxmox migration notes/ })).toHaveAttribute('href', '/common-room?post=502');
	await expect(commons.getByText('Why Proxmox migration notes matters.')).toBeVisible();
	const best = page.getByRole('region', { name: 'The best of everything' });
	await expect(best.getByRole('link', { name: /^The Commons: / })).toBeVisible();
	await expect(best.getByRole('link', { name: /^The Wire: / }).first()).toBeVisible();
	await expect(page.getByRole('navigation', { name: 'Jump to a page' }).getByRole('link', { name: /The Commons/ })).toHaveAttribute('href', '#page-commons');
	for (const width of [390, 1440]) {
		await page.setViewportSize({ width, height: 900 });
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
	}
});

test('Where to next reads video, music and board pages from their own APIs', async ({ page }) => {
	const now = Date.now() / 1000;
	const video = (id: string, title: string, published: number) => ({ video: { id, title, channel_id: 'c', channel_name: 'A Channel', duration_s: 300, published_at: new Date(published * 1000).toISOString(), view_count: 1, like_count: 0, thumbnail: '', is_short: false, is_live: false } });
	await page.route(/\/api\/hub\/today(?:\?.*)?$/, route => route.fulfill({ json: edition() }));
	await mockOverview(page, { generated_at: 1, pages: {} });
	await page.route('**/api/thumb/**', route => route.fulfill({ body: PNG, contentType: 'image/png' }));
	await page.route('**/api/feed/home?*', route => route.fulfill({ json: [
		{ kind: 'continue', title: 'Continue', emoji: '', items: [video('cont', 'Half watched', now)] },
		{ kind: 'category', title: 'Science', emoji: '🔬', category_id: 4, items: [video('new1', 'Fresh science video', now - 60), video('old1', 'Last month video', now - 30 * 86400)] }
	] }));
	const mix = (id: number, name: string, at: number) => ({ id, name, kind: 'mood', emoji: '🌙', description: '', curator: '', curator_name: '', color: '#123', blurb: 'Calm', seed_video_id: '', status: 'ready', error: '', count: 12, created_at: at, refreshed_at: at, last_played_at: 0, covers: [] });
	await page.route('**/api/music/home', route => route.fulfill({ json: { favorites: { tracks: [], artists: [] }, mixes: [mix(1, 'Tonight mix', now - 60), mix(2, 'Old mix', now - 9 * 86400)], recent: [], recent_mixes: [], ai: false, curators: [], offline: { wanted: 0, saved: 0, pending: 0 } } }));
	const board = (id: number, name: string, last: number) => ({ id, name, interests: '', instructions: '', enabled: true, interval_hours: 24, pin_count: 3, target_images: 20, reference_url: null, reference_name: '', last_run: last, last_added: last, preview_urls: [`/api/hub/boards/${id}/pins/1/media`, `/api/hub/boards/${id}/pins/2/media`] });
	await page.route('**/api/hub/boards', route => route.fulfill({ json: { boards: [board(5, 'Cozy cabins', now - 120), board(6, 'Stale board', now - 20 * 86400)] } }));
	await page.route('**/api/hub/boards/*/pins/*/media', route => route.fulfill({ status: 404, body: '' }));
	await page.setViewportSize({ width: 375, height: 812 });
	await freshPage(page, '/today');
	const next = page.getByRole('region', { name: 'Where to next' });
	const screen = next.getByRole('article', { name: 'The Screen' });
	await expect(screen.getByText('2 videos lined up')).toBeVisible();
	await expect(screen.getByText('+1 today')).toBeVisible();
	await expect(screen.getByRole('link', { name: /Fresh science video/ }).first()).toHaveAttribute('href', '/watch/new1');
	await expect(screen.getByRole('link', { name: /Science\s*2/ })).toHaveAttribute('href', '/category/4');
	await expect(screen.getByRole('link', { name: 'Half watched' })).toHaveCount(0);
	const jukebox = next.getByRole('article', { name: 'The Jukebox' });
	await expect(jukebox.getByText('2 mixes ready')).toBeVisible();
	await expect(jukebox.getByText('+1 today')).toBeVisible();
	const boards = next.getByRole('article', { name: 'The Boards' });
	await expect(boards.getByText('1 board grew this week')).toBeVisible();
	await expect(boards.getByRole('link', { name: /Cozy cabins/ }).first()).toHaveAttribute('href', '/boards?board=5');
	await expect(boards.getByRole('link', { name: /Stale board/ })).toHaveCount(0);
	await expect(next.getByRole('link', { name: /^Open The Screen/ })).toHaveAttribute('href', '/tv');
	expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test('Today degrades calmly when no introduction or sections are ready', async ({ page }) => {
	await page.route(/\/api\/hub\/today(?:\?.*)?$/, (route) => route.fulfill({ json: { ...edition(), intro: '', intro_refs: [], sections: [], set_aside: 0, curated: false } }));
	await mockOverview(page, { generated_at: 1, pages: {} });
	await freshPage(page, '/today');
	await expect(page.getByText('A little of what you follow, gathered in one place.')).toBeVisible();
	await expect(page.getByRole('region', { name: 'Where to next' })).toBeVisible();
});

test('Today gives a retry path when the edition request fails', async ({ page }) => {
	await page.route(/\/api\/hub\/today(?:\?.*)?$/, (route) => route.fulfill({ status: 503, json: { detail: 'Edition unavailable' } }));
	await freshPage(page, '/today');
	await expect(page.getByRole('alert')).toContainText('Today could not be loaded');
	await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
});

test('Paper and Dusk editions fit desktop and phone reading widths', async ({ page, request }) => {
	await page.route(/\/api\/hub\/today(?:\?.*)?$/, (route) => route.fulfill({ json: edition() }));
	try {
		for (const theme of ['paper', 'dusk']) {
			await request.put('/api/settings/appearance', { data: { theme } });
			for (const width of [1440, 390]) {
				await page.setViewportSize({ width, height: 1000 });
				await freshPage(page, '/today');
				await expect(page.getByRole('heading', { name: 'A thoughtful lead' })).toBeVisible();
				await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
				expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
				await page.screenshot({ path: `/tmp/watchcraft-today-${theme}-${width}.png`, fullPage: true });
			}
		}
	} finally { await request.put('/api/settings/appearance', { data: { theme: 'projector' } }); }
});


test('unavailable introduction sources preserve citation numbering', async ({ page }) => {
	const data = { ...edition(), intro: 'A missing source [1]. A visible source [2].', intro_refs: [{ n: 1, item_id: null }, { n: 2, item_id: 2 }] };
	await page.route(/\/api\/hub\/today(?:\?.*)?$/, route => route.fulfill({ json: data }));
	await freshPage(page, '/today');
	await expect(page.getByRole('link', { name: 'Source [1]' })).toHaveCount(0);
	await expect(page.getByRole('link', { name: 'Source [2]' })).toHaveAttribute('href', '/item/2');
});


test('Today refreshes an overnight edition on focus and keeps content on refresh failures', async ({ page }) => {
	await page.clock.install({ time: new Date('2026-11-03T10:00:00') });
	let nextDay = false;
	let fail = false;
	let requests = 0;
	await page.route(/\/api\/hub\/today(?:\?.*)?$/, route => {
		requests++;
		if (fail) return route.fulfill({ status: 503, json: { detail: 'Temporary failure' } });
		const data = edition();
		if (nextDay) {
			data.day = '2026-11-04';
			data.sections[0].entries![0].item.title = 'A fresh morning lead';
		}
		return route.fulfill({ json: data });
	});
	await freshPage(page, '/today');
	await expect(page.getByRole('heading', { name: 'A thoughtful lead' })).toBeVisible();
	await expect(page.locator('.date')).toContainText('3');
	nextDay = true;
	await page.clock.setSystemTime(new Date('2026-11-04T14:00:00'));
	await page.evaluate(() => window.dispatchEvent(new Event('focus')));
	await expect(page.getByRole('heading', { name: 'A fresh morning lead' })).toBeVisible();
	await expect(page.getByRole('heading', { name: /Good afternoon/ })).toBeVisible();
	await expect(page.locator('.date')).toContainText('4');
	fail = true;
	const before = requests;
	await page.evaluate(() => window.dispatchEvent(new Event('focus')));
	await expect.poll(() => requests).toBeGreaterThan(before);
	await expect(page.getByRole('heading', { name: 'A fresh morning lead' })).toBeVisible();
	await expect(page.getByRole('alert')).toHaveCount(0);
});


test('Today refreshes at bounded intervals only while visible and coalesces focus requests', async ({ page }) => {
	await page.clock.install();
	let requests = 0;
	let release: (() => void) | undefined;
	let block = false;
	await page.route(/\/api\/hub\/today(?:\?.*)?$/, async route => {
		requests++;
		if (block) await new Promise<void>(resolve => { release = resolve; });
		await route.fulfill({ json: edition() });
	});
	await freshPage(page, '/today');
	await expect(page.getByRole('heading', { name: 'A thoughtful lead' })).toBeVisible();
	const initial = requests;
	await page.evaluate(() => Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' }));
	await page.clock.runFor(6 * 60_000);
	expect(requests).toBe(initial);
	await page.evaluate(() => Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' }));
	block = true;
	await page.clock.runFor(60_000);
	await expect.poll(() => requests).toBe(initial + 1);
	await page.evaluate(() => { window.dispatchEvent(new Event('focus')); window.dispatchEvent(new Event('focus')); });
	expect(requests).toBe(initial + 1);
	block = false;
	release?.();
	await expect(page.getByRole('heading', { name: 'A thoughtful lead' })).toBeVisible();
});


test('a delayed background response cannot replace the newly selected profile edition', async ({ page, request }) => {
	const other = await (await request.post('/api/profiles', { data: { name: 'Edition refresh other' } })).json();
	let block = false;
	let release: (() => void) | undefined;
	let held = false;
	await page.route(/\/api\/hub\/today(?:\?.*)?$/, async route => {
		const isOther = route.request().headers()['x-profile-id'] === String(other.id);
		const data = edition();
		data.sections[0].entries![0].item.title = isOther ? 'Other profile edition' : 'Original profile edition';
		if (block && !isOther) {
			held = true;
			await new Promise<void>(resolve => { release = resolve; });
		}
		await route.fulfill({ json: data }).catch(() => {}); // switching profiles aborts this fetch
	});
	try {
		await freshPage(page, '/today');
		await expect(page.getByRole('heading', { name: 'Original profile edition' })).toBeVisible();
		block = true;
		await page.evaluate(() => window.dispatchEvent(new Event('focus')));
		await expect.poll(() => held).toBe(true);
		await page.getByRole('button', { name: 'Switch profile' }).click();
		await page.getByRole('button', { name: 'Edition refresh other', exact: true }).click();
		await expect(page.getByRole('heading', { name: 'Other profile edition' })).toBeVisible();
		release?.();
		await expect(page.getByRole('heading', { name: 'Original profile edition' })).toHaveCount(0);
	} finally { release?.(); await request.delete(`/api/profiles/${other.id}`); }
});


test('profile identity clears the old edition before delayed session initialization finishes', async ({ page, request }) => {
	const other = await (await request.post('/api/profiles', { data: { name: 'Delayed edition profile' } })).json();
	let stallCategories = false;
	let categoriesHeld = false;
	let releaseCategories: (() => void) | undefined;
	const categoriesGate = new Promise<void>(resolve => { releaseCategories = resolve; });
	let newEditionHeld = false;
	let releaseEdition: (() => void) | undefined;
	const editionGate = new Promise<void>(resolve => { releaseEdition = resolve; });
	await page.route('**/api/categories', async route => {
		if (stallCategories && route.request().headers()['x-profile-id'] === String(other.id)) {
			categoriesHeld = true;
			await categoriesGate;
		}
		await route.continue();
	});
	await page.route(/\/api\/hub\/today(?:\?.*)?$/, async route => {
		const isOther = route.request().headers()['x-profile-id'] === String(other.id);
		const data = edition();
		data.sections[0].entries![0].item.title = isOther ? 'New identity edition' : 'Private original edition';
		if (isOther && stallCategories) {
			newEditionHeld = true;
			await editionGate;
		}
		await route.fulfill({ json: data }).catch(() => {});
	});
	try {
		await freshPage(page, '/today');
		await expect(page.getByRole('heading', { name: 'Private original edition' })).toBeVisible();
		stallCategories = true;
		await page.getByRole('button', { name: 'Switch profile' }).click();
		await page.getByRole('button', { name: 'Delayed edition profile', exact: true }).click();
		await expect.poll(() => categoriesHeld && newEditionHeld).toBe(true);
		// Categories are still held: session.version has not changed yet.
		await expect(page.getByRole('heading', { name: 'Private original edition' })).toHaveCount(0);
		await expect(page.getByRole('status').filter({ hasText: 'Gathering' })).toBeVisible();
		releaseEdition?.();
		await expect(page.getByRole('heading', { name: 'New identity edition' })).toBeVisible();
		stallCategories = false;
		releaseCategories?.();
		await expect(page.getByRole('heading', { name: 'New identity edition' })).toBeVisible();
	} finally {
		releaseEdition?.(); releaseCategories?.();
		await request.delete(`/api/profiles/${other.id}`);
	}
});


test('Today displays labeled archived AI summaries when source excerpts are absent', async ({ page }) => {
	const data = edition();
	for (const section of data.sections.slice(0, 3)) {
		const entry = section.entries![0];
		Object.assign(entry, { summary: '' });
		entry.item.summary = '';
		entry.item.enrichment = { summary: `Archived overview for ${section.key}.` };
	}
	await page.route(/\/api\/hub\/today(?:\?.*)?$/, route => route.fulfill({ json: data }));
	await freshPage(page, '/today');
	for (const key of ['lead']) {
		await expect(page.locator('.summary').filter({ hasText: `Archived overview for ${key}.` })).toContainText('AI summary');
	}
});

test('regenerate rebuilds Today now and reports when the curator finishes polishing', async ({ page }) => {
	let state = { requested_at: 0, completed_at: 0 };
	await page.route(/\/api\/hub\/today(?:\?.*)?$/, route => route.fulfill({ json: { ...edition(), rerun: state } }));
	await page.route('**/api/hub/today/regenerate', route => { state = { requested_at: 10, completed_at: 0 }; return route.fulfill({ json: { rebuilt: true, rerun: state } }); });
	await freshPage(page, '/today');
	await page.getByRole('button', { name: /^↻ Regenerate$/ }).click();
	await expect(page.getByRole('status').filter({ hasText: 'Rebuilt from the best of everything new' })).toBeVisible();
	await expect(page.getByRole('button', { name: /Regenerate \(polishing…\)/ })).toBeEnabled();
	state = { requested_at: 10, completed_at: 11 };
	await page.evaluate(() => window.dispatchEvent(new Event('focus')));
	await expect(page.getByRole('button', { name: /^↻ Regenerate$/ })).toBeEnabled();
	await expect(page.getByRole('status').filter({ hasText: 'Master curator finished' })).toBeVisible();
});

test('an entry can be marked seen or dismissed from Today', async ({ page }) => {
	const posts: unknown[] = [];
	await page.route(/\/(?:api\/)?hub\/today(?:\?.*)?$/, (route) => route.fulfill({ json: edition() }));
	await page.route('**/api/hub/items/state', async (route) => {
		posts.push(route.request().postDataJSON());
		await route.fulfill({ json: { updated: 1 } });
	});
	await freshPage(page, '/today');
	await page.getByRole('button', { name: 'Dismiss A thoughtful lead' }).click();
	await expect(page.getByText('Removed from today.')).toBeVisible();
	expect(posts).toEqual([{ dismissed: true, item_ids: [1] }]);
});


test('News and Today are separate pages', async ({ page }) => {
	await page.route(/\/api\/hub\/today(?:\?.*)?$/, route => route.fulfill({ json: edition() }));
	await mockOverview(page);
	await page.route('**/api/hub/news?*', route => route.fulfill({ json: { topics: [], groups: [{
		key: 'extra', title: 'Another news story', latest: 1783000000,
		items: [item(900, 'Another news story')]
	}] } }));
	await page.setViewportSize({ width: 375, height: 812 });
	await freshPage(page, '/news');
	await expect(page.getByRole('heading', { name: 'The Wire', exact: true, level: 1 })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Another news story' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'A thoughtful lead' })).toHaveCount(0);
	expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
	await freshPage(page, '/today');
	await expect(page.getByRole('heading', { name: 'A thoughtful lead' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Another news story' })).toHaveCount(0);
	await page.getByRole('link', { name: /^Open The Wire/ }).click();
	await expect(page).toHaveURL(/\/news$/);
	await expect(page.getByRole('heading', { name: 'The Wire', exact: true, level: 1 })).toBeVisible();
});
