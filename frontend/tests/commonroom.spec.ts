import { test, expect, type Page } from '@playwright/test';
import { freshPage } from './helpers';

const thread = { id: 1, name: 'Retro computing', interests: 'Amiga', instructions: '', avoid: '', strictness: 'balanced', child_friendly: false, enabled: true, last_run: 1700000000, last_status: 'ok', error: '', new_count: 2, pick_count: 6, source_count: 2 };
const post = (id: number, title: string, extra: Record<string, unknown> = {}) => ({ pick_id: id, post_id: id + 100, thread_id: 1, thread_name: 'Retro computing', platform: 'lemmy', community: 'c/retro', author: 'ann', title, excerpt: `About ${title}`, url: `https://example.org/p/${id}`, link_url: '', domain: '', media: [], score: 12, comments: 3, published_at: Math.floor(Date.now() / 1000) - 3600, why: 'Matches Amiga', relevance: 0.9, saved: false, verdict: '', nsfw: false, spoiler: false, ...extra });

// Most specs here test the New → Deep flow; the default view is Today (see commons-fun.spec.ts).
test.beforeEach(async ({ page }) => {
	await page.addInitScript(() => { try { localStorage.setItem('commons.mode', 'new'); } catch { /* opaque origin */ } });
});

async function setup(page: Page) {
	const calls: { method: string; path: string; query: URLSearchParams; body: any }[] = [];
	await page.route('**/api/hub/commonroom/**', async (route) => {
		const req = route.request(), url = new URL(req.url()), path = url.pathname.replace('/api/hub/commonroom', ''), method = req.method();
		const body = method === 'GET' ? {} : req.postDataJSON() ?? {};
		calls.push({ method, path, query: url.searchParams, body });
		if (path === '/threads') return route.fulfill({ json: { threads: [thread], platforms: [{ key: 'lemmy', label: 'Lemmy', example: 'lemmy.world/c/retro', sorts: [] }], child: false } });
		if (path === '/visit') return route.fulfill({ json: { boundary: 50 } });
		if (path === '/feed') {
			const s = url.searchParams.get('section'), cur = url.searchParams.get('cursor');
			if (s === 'new') return route.fulfill({ json: { posts: [post(1, 'Fresh Amiga find'), post(2, 'Fresh C64 demo')], next_cursor: '', has_more: false, building: false } });
			if (!cur) return route.fulfill({ json: { posts: [post(3, 'Deep dive one')], next_cursor: 'c2', has_more: true, building: false } });
			return route.fulfill({ json: { posts: [post(4, 'Deep dive two')], next_cursor: 'c3', has_more: false, building: false } });
		}
		if (path === '/seen') return route.fulfill({ json: {} });
		if (/^\/posts\/\d+$/.test(path)) return route.fulfill({ json: { ...post(1, 'Fresh Amiga find'), body: 'Full body text' } });
		if (path.endsWith('/comments')) return route.fulfill({ json: { comments: [{ id: 'c1', author: 'bob', body: 'Great find', score: 3, published_at: null, children: [{ id: 'c2', author: 'eve', body: 'Agreed', score: 1, published_at: null, children: [] }] }], url: 'https://example.org/p/1' } });
		return route.fulfill({ json: { ok: true } });
	});
	return calls;
}

test('renders New, divider, then Deep and loads the next cursor', async ({ page }) => {
	const calls = await setup(page);
	await freshPage(page, '/common-room');
	const feed = page.locator('.feed');
	await expect(page.getByText('Fresh Amiga find').first()).toBeVisible();
	await expect(page.getByRole('separator')).toContainText("You're caught up");
	await expect(page.getByRole('link', { name: 'Deep dive one' })).toBeVisible();
	const order = await page.locator('h3, [role=separator]').allTextContents();
	expect(order.findIndex((t) => t.includes('Fresh C64'))).toBeLessThan(order.findIndex((t) => t.includes("caught up")));
	expect(order.findIndex((t) => t.includes('caught up'))).toBeLessThan(order.findIndex((t) => t.includes('Deep dive one')));
	await expect(page.getByRole('link', { name: 'Deep dive two' })).toBeVisible();
	expect(calls.some((c) => c.path === '/feed' && c.query.get('section') === 'deep' && c.query.get('cursor') === 'c2' && c.query.get('boundary') === '50')).toBe(true);
	await expect(feed.first()).toBeVisible();
});

test('thumbs down hides the card and sends feedback', async ({ page }) => {
	const calls = await setup(page);
	await freshPage(page, '/common-room');
	const card = page.locator('article', { hasText: 'Fresh Amiga find' }).first();
	await card.getByRole('button', { name: 'Bad pick' }).click();
	await expect(page.getByRole('heading', { name: 'Fresh Amiga find' })).toHaveCount(0);
	expect(calls.find((c) => c.path === '/picks/1/feedback')?.body).toMatchObject({ verdict: 'down' });
});

test('opening a post shows its comments straight away', async ({ page }) => {
	await setup(page);
	await freshPage(page, '/common-room');
	await page.getByRole('link', { name: 'Fresh Amiga find' }).click();
	await expect(page.getByText('Full body text')).toBeVisible();
	await expect(page.getByText('Great find')).toBeVisible();
	await expect(page.getByText('Agreed')).toBeVisible();
	await expect(page.getByRole('button', { name: /Refresh/ })).toBeVisible();
	await expect(page.getByRole('link', { name: 'Join the discussion' })).toHaveAttribute('href', 'https://example.org/p/1');
});

test('long comment threads fold deep replies and page the rest, best first', async ({ page }) => {
	await setup(page);
	const c = (id: string, author: string, score: number, children: unknown[] = []) => ({ id, author, body: `Body ${id}`, score, published_at: null, children });
	const deep = c('d1', 'ann', 50, [c('d2', 'x', 1, [c('d3', 'y', 1, [c('d4', 'z', 1)])])]);
	const many = Array.from({ length: 13 }, (_, i) => c(`m${i}`, `u${i}`, i));
	await page.route('**/api/hub/commonroom/posts/*/comments*', (route) => route.fulfill({ json: { comments: [...many, deep], url: 'https://example.org/p/1', fetched_at: Math.floor(Date.now() / 1000) - 600, supported: true } }));
	await freshPage(page, '/common-room');
	await page.getByRole('link', { name: 'Fresh Amiga find' }).click();
	const talk = page.getByRole('region', { name: 'Comments' });
	await expect(talk.locator('.body').first()).toHaveText('Body d1');  // best (highest score) first
	await expect(talk.getByText('OP')).toBeVisible();                     // ann wrote the post
	await expect(talk.getByText('Body d3')).toHaveCount(0);
	await talk.getByRole('button', { name: 'Show 2 replies' }).click();
	await expect(talk.getByText('Body d3')).toBeVisible();
	await expect(talk.getByText('Body m0')).toHaveCount(0);
	await talk.getByRole('button', { name: 'Show 4 more comments' }).click();
	await expect(talk.getByText('Body m0')).toBeVisible();
	await talk.getByRole('button', { name: 'New' }).click();
	await expect(talk.getByText(/Updated/)).toBeVisible();
});

test('a source without comments says so instead of loading forever', async ({ page }) => {
	await setup(page);
	await page.route('**/api/hub/commonroom/posts/*/comments*', (route) => route.fulfill({ json: { comments: [], url: 'https://example.org/p/1', fetched_at: 0, supported: false } }));
	await freshPage(page, '/common-room');
	await page.getByRole('link', { name: 'Fresh Amiga find' }).click();
	await expect(page.getByText('This source has no comments to show here.')).toBeVisible();
	await expect(page.getByRole('button', { name: /Refresh/ })).toHaveCount(0);
});

test('a Reddit mirror post points to its Reddit thread and how to read it here', async ({ page }) => {
	await setup(page);
	const thread = 'https://www.reddit.com/r/interestingasfuck/comments/1y2uzek/';
	await page.route('**/api/hub/commonroom/posts/*/comments*', (route) => route.fulfill({ json: { comments: [], url: thread, fetched_at: 0, supported: false, discussion: thread } }));
	await freshPage(page, '/common-room');
	await page.getByRole('link', { name: 'Fresh Amiga find' }).click();
	await expect(page.getByText('The conversation is on reddit.com.')).toBeVisible();
	await expect(page.getByRole('link', { name: 'Read the thread ↗' })).toHaveAttribute('href', thread);
	await expect(page.getByRole('link', { name: 'Discussion on reddit.com ↗' })).toBeVisible();
	await expect(page.getByRole('link', { name: 'Settings → Common Room' })).toHaveAttribute('href', '/settings?tab=commonroom');
});

test('app back closes an open post and keeps the list in place', async ({ page }) => {
	await setup(page);
	await freshPage(page, '/common-room');
	await page.getByRole('link', { name: 'Fresh Amiga find' }).click();
	await expect(page.getByText('Full body text')).toBeVisible();
	const back = () => page.evaluate(() => (window as Window & { watchcraftBack?: () => boolean }).watchcraftBack?.());
	expect(await back()).toBe(true);
	await expect(page.getByRole('dialog')).toHaveCount(0);
	await expect(page).toHaveURL(/\/common-room$/);
	await expect(page.getByRole('link', { name: 'Fresh Amiga find' })).toBeVisible();
	expect(await back()).toBe(false);
	await page.getByRole('link', { name: 'Fresh Amiga find' }).click();
	await page.getByRole('dialog').getByRole('button', { name: 'Close dialog' }).click();
	await expect(page.getByRole('dialog')).toHaveCount(0);
});

test('nav entry is present', async ({ page }) => {
	await setup(page);
	await freshPage(page, '/common-room');
	await expect(page.getByRole('navigation', { name: 'Main', exact: true }).getByRole('link', { name: 'The Commons' })).toHaveAttribute('href', '/common-room');
});

test('a post already rated down starts hidden', async ({ page }) => {
	await setup(page);
	await page.route('**/api/hub/commonroom/feed**', (route) => {
		const u = new URL(route.request().url());
		return route.fulfill({ json: { posts: u.searchParams.get('section') === 'new' ? [post(1, 'Rated down', { verdict: 'down' }), post(2, 'Still shown')] : [], next_cursor: '', has_more: false, building: false } });
	});
	await freshPage(page, '/common-room');
	await expect(page.getByRole('link', { name: 'Still shown' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Rated down' })).toHaveCount(0);
	await expect(page.getByText('Hidden. Tell your curator why?')).toBeVisible();
});

test('long unbroken title stays inside the drawer at 375px', async ({ page }) => {
	await setup(page);
	const long = 'https://example.org/' + 'a'.repeat(160);
	await page.route('**/api/hub/commonroom/feed**', (route) => {
		const u = new URL(route.request().url());
		return route.fulfill({ json: { posts: u.searchParams.get('section') === 'new' ? [post(1, long)] : [], next_cursor: '', has_more: false, building: false } });
	});
	await page.setViewportSize({ width: 375, height: 800 });
	await freshPage(page, '/common-room');
	await page.getByRole('link', { name: long }).click();
	const box = await page.getByRole('dialog').boundingBox();
	expect(box!.x).toBeGreaterThanOrEqual(0);
	expect(box!.x + box!.width).toBeLessThanOrEqual(376);
});

test('Reddit keys are easy to find: grouped Settings tab with status', async ({ page }) => {
	await freshPage(page, '/settings?tab=commonroom');
	const side = page.getByRole('navigation', { name: 'Settings sections' });
	await expect(side.getByText('Sources and keys')).toBeVisible();
	await expect(side.getByRole('button', { name: /Common Room/ })).toHaveAttribute('aria-current', 'page');
	await expect(page.getByRole('heading', { name: 'Common Room' })).toBeVisible();
	await expect(page.getByLabel('Reddit client id')).toBeVisible();
	await expect(page.getByRole('status').filter({ hasText: /limited mode|keys are set/ })).toBeVisible();
	await page.getByLabel('Reddit client id').fill('abc');
	const saved = page.waitForRequest((r) => r.url().includes('/api/settings/commonroom') && r.method() === 'PUT');
	await page.getByRole('button', { name: 'Save Common Room' }).click();
	expect((await saved).postDataJSON().reddit_client_id).toBe('abc');
});

test('post pictures load as the current profile, preview size on the card', async ({ page }) => {
	const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64');
	const media: { url: string; profile: string | undefined }[] = [];
	await page.route('**/api/hub/commonroom/**', async (route) => {
		const req = route.request(), url = new URL(req.url()), path = url.pathname.replace('/api/hub/commonroom', '');
		if (path === '/threads') return route.fulfill({ json: { threads: [thread], platforms: [], child: false } });
		if (path === '/visit') return route.fulfill({ json: { boundary: 50 } });
		if (path === '/feed') return route.fulfill({ json: { posts: url.searchParams.get('section') === 'new' ? [post(1, 'Pictured post', { media: [{ kind: 'image', url: '/api/hub/commonroom/posts/101/media/0', thumb: '/api/hub/commonroom/posts/101/media/0?size=thumb', poster: '' }] })] : [], next_cursor: '', has_more: false, building: false } });
		if (path.startsWith('/posts/101/media/')) { media.push({ url: url.pathname + url.search, profile: req.headers()['x-profile-id'] }); return route.fulfill({ body: png, contentType: 'image/png' }); }
		return route.fulfill({ json: {} });
	});
	await freshPage(page, '/common-room');
	const img = page.locator('.imgbtn img');
	await expect(img).toHaveAttribute('src', /^blob:/);
	expect(media[0].url).toBe('/api/hub/commonroom/posts/101/media/0?size=thumb');
	expect(media[0].profile).toBeTruthy();
});

test('a picture that fails to load leaves no broken frame on the card', async ({ page }) => {
	await page.route('**/api/hub/commonroom/**', async (route) => {
		const url = new URL(route.request().url()), path = url.pathname.replace('/api/hub/commonroom', '');
		if (path === '/threads') return route.fulfill({ json: { threads: [thread], platforms: [], child: false } });
		if (path === '/visit') return route.fulfill({ json: { boundary: 50 } });
		if (path === '/feed') return route.fulfill({ json: { posts: url.searchParams.get('section') === 'new' ? [post(1, 'Lost picture', { media: [{ kind: 'image', url: '/api/hub/commonroom/posts/101/media/0', thumb: '/api/hub/commonroom/posts/101/media/0?size=thumb', poster: '' }] })] : [], next_cursor: '', has_more: false, building: false } });
		if (path.startsWith('/posts/101/media/')) return route.fulfill({ status: 404, json: { detail: 'That picture could not be fetched.' } });
		return route.fulfill({ json: {} });
	});
	await freshPage(page, '/common-room');
	await expect(page.getByText('Lost picture').first()).toBeVisible();
	await expect(page.locator('.card .media')).toHaveCount(0);
});

test('desktop shows the wall in several columns, a phone in one', async ({ page }) => {
	await setup(page);
	await page.setViewportSize({ width: 1600, height: 1000 });
	await freshPage(page, '/common-room');
	await expect(page.getByText('Fresh C64 demo').first()).toBeVisible();
	const lefts = async () => new Set(await page.locator('.feed').first().locator('.item').evaluateAll((els) => els.map((e) => Math.round(e.getBoundingClientRect().left))));
	expect((await lefts()).size).toBeGreaterThan(1);
	await page.setViewportSize({ width: 390, height: 844 });
	await expect.poll(async () => (await lefts()).size).toBe(1);
});

test('Settings: choose Common Room post languages', async ({ page }) => {
	const puts: unknown[] = [];
	await page.route('**/api/hub/commonroom/languages', async (route) => {
		if (route.request().method() === 'GET') return route.fulfill({ json: { languages: [{ key: 'en', count: 40 }, { key: 'de', count: 3 }], allowed: [] } });
		const body = route.request().postDataJSON();
		puts.push(body);
		return route.fulfill({ json: { allowed: body.languages } });
	});
	await freshPage(page, '/settings?tab=commonroom');
	await expect(page.getByRole('heading', { name: 'Post languages' })).toBeVisible();
	await page.getByRole('checkbox', { name: /Danish/ }).check();
	await page.getByRole('checkbox', { name: /English/ }).check();
	await expect(page.getByText('Showing: Danish, English.')).toBeVisible();
	expect(puts).toEqual([{ languages: ['da'] }, { languages: ['da', 'en'] }]);
});

test('Edit sub: Find sources starts an expedition and shows its log and found sources', async ({ page }) => {
	const calls = await setup(page);
	let state = 0;
	const lemmy = { id: 7, source_id: 70, platform: 'lemmy', locator: 'retro@lemmy.world', label: 'retro@lemmy.world', origin: 'ai', enabled: true, error: '', last_ok: null, status: 'ok', fit: 7, tried: 8, kept: 3, hits: 40, url: '' };
	const feed = { id: 8, source_id: 80, platform: 'rss', locator: 'https://amiga.example/feed', label: 'Amiga Journal', origin: 'ai', enabled: true, error: 'gone', last_ok: null, status: 'broken', fit: 5, tried: 8, kept: 0, hits: 12, url: 'https://amiga.example/feed' };
	const hunt = (status: string, log: string[]) => ({ id: 1, status, reason: 'button', found: status === 'done' ? 1 : 0, error: '', created_at: 1, finished_at: 0, log: log.map((message) => ({ at: 1, step: message.startsWith('Following') ? 'saved' : 'searching', message })) });
	await page.route('**/api/hub/commonroom/threads/1/**', async (route) => {
		const path = new URL(route.request().url()).pathname;
		calls.push({ method: route.request().method(), path, query: new URLSearchParams(), body: {} });
		if (path.endsWith('/find-sources')) { state = 1; return route.fulfill({ json: { sources: [lemmy], hunt: hunt('queued', ['Source expedition queued.']) } }); }
		if (path.endsWith('/sources')) {
			if (state === 0) return route.fulfill({ json: { sources: [lemmy], hunt: null } });
			return route.fulfill({ json: { sources: [lemmy, feed], hunt: hunt('done', ['Source expedition queued.', 'Following Amiga Journal (5/8 sample posts fit).']) } });
		}
		if (path.endsWith('/learned')) return route.fulfill({ json: { lessons: [], up: 0, down: 0 } });
		return route.fulfill({ json: { ok: true } });
	});
	await freshPage(page, '/common-room');
	await page.getByRole('button', { name: 'Retro computing' }).click();
	await page.getByRole('button', { name: 'Edit sub' }).click();
	const dialog = page.getByRole('dialog');
	await expect(dialog.getByText('fit 7/8')).toBeVisible();
	await dialog.getByRole('button', { name: 'Find sources' }).click();
	await expect(dialog.getByRole('button', { name: 'Expedition running…' })).toBeDisabled();
	await expect(dialog.getByRole('list', { name: 'Expedition log' })).toContainText('Following Amiga Journal', { timeout: 10000 });
	await expect(dialog.getByRole('link', { name: 'Amiga Journal' })).toBeVisible();
	await expect(dialog.getByText('broken, retried daily')).toBeVisible();
	await expect(dialog.getByText('Last expedition: 1 new source')).toBeVisible();
});

test('a thread badge opens New from Today and clears once visited', async ({ page }) => {
	await page.addInitScript(() => { try { localStorage.setItem('commons.mode', 'today'); } catch { /* opaque origin */ } });
	const calls = await setup(page);
	await freshPage(page, '/common-room');
	const chip = page.getByRole('navigation', { name: 'Subs' }).getByRole('button', { name: /Retro computing/ });
	await expect(chip.locator('.badge')).toHaveText('2');
	await chip.click();
	await expect(page.getByRole('tab', { name: 'New' })).toHaveAttribute('aria-selected', 'true');
	await expect(page.getByText('Fresh Amiga find').first()).toBeVisible();
	expect(calls.some((c) => c.path === '/visit' && String(c.body.thread) === '1')).toBe(true);
	await expect(chip.locator('.badge')).toHaveCount(0);
	expect(await page.evaluate(() => localStorage.getItem('commons.mode'))).toBe('today');
});

test('meh hides the card as boring and offers boring-first reasons', async ({ page }) => {
	const calls = await setup(page);
	await freshPage(page, '/common-room');
	const card = page.locator('article', { hasText: 'Fresh Amiga find' }).first();
	await card.getByRole('button', { name: 'On topic but boring' }).click();
	await expect(page.getByText('Hidden as boring. Anything else?')).toBeVisible();
	await expect(page.getByRole('button', { name: 'boring', exact: true })).toHaveAttribute('aria-pressed', 'true');
	expect(calls.find((c) => c.path === '/picks/1/feedback')?.body).toMatchObject({ verdict: 'meh' });
	expect(calls.some((c) => c.path === '/picks/1' && c.method === 'PUT')).toBe(false);  // one call hides it
});

test('opening and reading a post records open, links and dwell', async ({ page }) => {
	const calls = await setup(page);
	await freshPage(page, '/common-room');
	await page.getByRole('link', { name: 'Fresh Amiga find' }).click();
	const dialog = page.getByRole('dialog');
	await expect(dialog.getByText('Full body text')).toBeVisible();
	await page.waitForTimeout(800);
	const popup = page.waitForEvent('popup');
	await dialog.getByRole('link', { name: 'Open original ↗' }).click();
	await (await popup).close();
	await page.keyboard.press('Escape');
	await expect.poll(() => calls.filter((c) => c.path === '/events').flatMap((c) => c.body.events.map((e: { kind: string }) => e.kind)).sort(), { timeout: 8000 })
		.toEqual(['dwell', 'open', 'out']);
	const ev = calls.filter((c) => c.path === '/events').flatMap((c) => c.body.events);
	expect(ev.every((e: { pick_id: number }) => e.pick_id === 1)).toBe(true);
	expect(ev.find((e: { kind: string }) => e.kind === 'dwell').value).toBeGreaterThan(500);
});

test('Edit sub: a slowed source shows why and can be undone; taste note saves', async ({ page }) => {
	const calls = await setup(page);
	const src = { id: 7, source_id: 70, platform: 'lemmy', locator: 'retro@lemmy.world', label: 'retro@lemmy.world', origin: 'ai', enabled: true, error: '', last_ok: null, status: 'ok', fit: 7, tried: 8, kept: 3, hits: 40, url: '', shown: 20, liked: 1, skipped: 15, throttle: 2, throttle_why: 'you skipped 15 of the last 20 posts' };
	await page.route('**/api/hub/commonroom/threads/1/**', async (route) => {
		const req = route.request(), path = new URL(req.url()).pathname;
		calls.push({ method: req.method(), path, query: new URLSearchParams(), body: req.method() === 'GET' ? {} : req.postDataJSON() });
		if (path.endsWith('/sources')) return route.fulfill({ json: { sources: [src], hunt: null } });
		if (path.endsWith('/sources/7')) return route.fulfill({ json: { ...src, throttle: 0, throttle_why: 'kept by you' } });
		if (path.endsWith('/learned')) return route.fulfill({ json: { lessons: [], up: 3, down: 1, meh: 2, taste: ['Loves build logs with photos'], kinds: { showcase: 0.3, news: -0.2 }, hit_rate: 0.42, shown_week: 12, owner_note: '' } });
		return route.fulfill({ json: { ok: true } });
	});
	await freshPage(page, '/common-room');
	await page.getByRole('button', { name: 'Retro computing' }).click();
	await page.getByRole('button', { name: 'Edit sub' }).click();
	const dialog = page.getByRole('dialog');
	await expect(dialog.getByText('liked 1 · skipped 15')).toBeVisible();
	await expect(dialog.getByText('slowed ×4')).toBeVisible();
	await expect(dialog.getByText('Loves build logs with photos')).toBeVisible();
	await expect(dialog.getByText('42%')).toBeVisible();
	await expect(dialog.getByText(/loves showcase · skips news/)).toBeVisible();
	await dialog.getByRole('button', { name: 'Keep retro@lemmy.world at full strength' }).click();
	await expect(dialog.getByText('slowed ×4')).toHaveCount(0);
	expect(calls.find((c) => c.path.endsWith('/sources/7'))?.body).toEqual({ throttle: 0 });
	await dialog.getByRole('textbox', { name: /what makes a post fun/ }).fill('snarky threads');
	await dialog.getByRole('button', { name: 'Save', exact: true }).click();
	await expect(dialog.getByRole('button', { name: 'Saved' })).toBeVisible();
	expect(calls.find((c) => c.path === '/taste')?.body).toEqual({ note: 'snarky threads' });
});
