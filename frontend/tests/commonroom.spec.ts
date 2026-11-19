import { test, expect, type Page } from '@playwright/test';
import { freshPage } from './helpers';

const thread = { id: 1, name: 'Retro computing', interests: 'Amiga', instructions: '', avoid: '', strictness: 'balanced', child_friendly: false, enabled: true, last_run: 1700000000, last_status: 'ok', error: '', new_count: 2, pick_count: 6, source_count: 2 };
const post = (id: number, title: string, extra: Record<string, unknown> = {}) => ({ pick_id: id, post_id: id + 100, thread_id: 1, thread_name: 'Retro computing', platform: 'lemmy', community: 'c/retro', author: 'ann', title, excerpt: `About ${title}`, url: `https://example.org/p/${id}`, link_url: '', domain: '', media: [], score: 12, comments: 3, published_at: Math.floor(Date.now() / 1000) - 3600, why: 'Matches Amiga', relevance: 0.9, saved: false, verdict: '', nsfw: false, spoiler: false, ...extra });

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

test('opening a post loads its comments', async ({ page }) => {
	await setup(page);
	await freshPage(page, '/common-room');
	await page.getByRole('link', { name: 'Fresh Amiga find' }).click();
	await expect(page.getByText('Full body text')).toBeVisible();
	await page.getByRole('button', { name: 'Load comments' }).click();
	await expect(page.getByText('Great find')).toBeVisible();
	await expect(page.getByText('Agreed')).toBeVisible();
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
