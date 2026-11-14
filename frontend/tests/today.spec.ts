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
		{ key: 'research', title: 'Research', curator_ids: [8], curators: [{ id: 8, name: 'Materials', subject: 'new materials', report_at: 1 }] },
		{ key: 'alerts', title: 'Alerts', count: 2 }
	]
});

test('Today assembles the edition, citations, reasons and curator actions', async ({ page }) => {
	const data = edition();
	await page.route('**/api/hub/items/1/overrule', route => route.fulfill({ json: { tier: 'feature', reasons: ['you promoted it'] } }));
	await page.route(/\/api\/hub\/today(?:\?.*)?$/, (route) => route.fulfill({ json: data }));
	await freshPage(page, '/today');
	await expect(page.getByText('A measured overview')).toBeVisible();
	await expect(page.getByRole('link', { name: 'Source [1]' })).toHaveAttribute('href', '/item/1');
	await expect(page.getByRole('heading', { name: 'A thoughtful lead' })).toBeVisible();
	await expect(page.getByText('+2 sources')).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Stories' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'A useful read' })).toBeVisible();
	await expect(page.getByRole('link', { name: 'Read the report' })).toHaveAttribute('href', '/research/8');
	await expect(page.getByRole('link', { name: 'Review alerts' })).toHaveAttribute('href', '/alerts');
	await page.getByRole('button', { name: 'Why?' }).first().click();
	await expect(page.getByText('Matches your interests')).toBeVisible();
	await page.getByRole('button', { name: 'Keep A thoughtful lead' }).click();
	await expect(page.getByRole('status').filter({ hasText: /^Kept in your edition\.$/ })).toBeVisible();
});

test('Today opens a long edition and grows it in steps', async ({ page }) => {
	const data = edition();
	const editionRequests: URL[] = [];
	const makeEntry = (id: number, title: string, type = 'news') => ({ item_id: id, item: item(id, title, type), sources: 1, summary: `A brief overview of ${title}.` });
	data.sections.find(s => s.key === 'stories')!.entries = Array.from({ length: 79 }, (_, i) => makeEntry(1000 + i, `Story ${i + 1}`));
	data.sections.find(s => s.key === 'reads')!.entries = Array.from({ length: 20 }, (_, i) => makeEntry(2000 + i, `Read ${i + 1}`, 'article'));
	data.sections.find(s => s.key === 'memes')!.entries = Array.from({ length: 10 }, (_, i) => ({ ...makeEntry(3000 + i, `Moment ${i + 1}`, 'meme'), item: { ...item(3000 + i, `Moment ${i + 1}`, 'meme'), meme: { spoiler: `Moment ${i + 1}`, sensitive: false, community: 'quiet-humor' } } }));
	await page.route(/\/api\/hub\/today(?:\?.*)?$/, route => { editionRequests.push(new URL(route.request().url())); return route.fulfill({ json: data }); });
	await page.setViewportSize({ width: 390, height: 844 });
	await freshPage(page, '/today');
	await expect.poll(() => editionRequests.length).toBeGreaterThan(0);
	expect(editionRequests.at(-1)?.searchParams.get('preview')).toBe('true');
	expect(editionRequests.at(-1)?.searchParams.get('stories')).toBe('16');
	expect(editionRequests.at(-1)?.searchParams.get('reads')).toBe('9');
	expect(editionRequests.at(-1)?.searchParams.get('memes')).toBe('6');
	await expect(page.getByRole('heading', { name: 'Stories · 79' })).toBeVisible();
	await expect(page.getByRole('link', { name: 'Story 16', exact: true })).toBeVisible();
	await expect(page.getByRole('link', { name: 'Story 17', exact: true })).toHaveCount(0);
	// Each click adds a step and tells the server only what is now rendered.
	await page.getByRole('button', { name: 'Show 20 more stories' }).click();
	await expect(page.getByRole('link', { name: 'Story 36', exact: true })).toBeVisible();
	await expect(page.getByRole('link', { name: 'Story 37', exact: true })).toHaveCount(0);
	await expect.poll(() => editionRequests.at(-1)?.searchParams.get('stories')).toBe('36');
	await page.getByRole('button', { name: 'Show 20 more stories' }).click();
	await page.getByRole('button', { name: 'Show 20 more stories' }).click();
	await page.getByRole('button', { name: 'Show 3 more stories' }).click();
	await expect(page.getByRole('link', { name: 'Story 79', exact: true })).toBeVisible();
	await expect(page.getByRole('link', { name: 'Explore news' })).toHaveAttribute('href', '/news#news-feed');
	await expect(page.getByRole('heading', { name: 'Worth your time · 20' })).toBeVisible();
	await expect(page.getByRole('link', { name: 'Read 10', exact: true })).toHaveCount(0);
	await page.getByRole('button', { name: 'Show 9 more reads' }).click();
	await page.getByRole('button', { name: 'Show 2 more reads' }).click();
	await expect(page.getByRole('link', { name: 'Read 20', exact: true })).toBeVisible();
	await expect(page.getByRole('link', { name: /^Moment 7 / })).toHaveCount(0);
	await page.getByRole('button', { name: 'Show 4 more moments' }).click();
	await expect(page.getByRole('link', { name: /^Moment 10 / })).toBeVisible();
	expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
	await page.setViewportSize({ width: 1440, height: 1000 });
	expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
	await page.screenshot({ path: '/tmp/watchcraft-today-expanded-1440.png', fullPage: true });
	await page.setViewportSize({ width: 390, height: 844 });
	await page.getByRole('button', { name: 'Show fewer stories' }).click();
	await expect(page.getByRole('link', { name: 'Story 17', exact: true })).toHaveCount(0);
	await expect(page.getByRole('link', { name: 'Story 16', exact: true })).toBeVisible();
});

test('Today degrades calmly when no introduction or sections are ready', async ({ page }) => {
	await page.route(/\/api\/hub\/today(?:\?.*)?$/, (route) => route.fulfill({ json: { ...edition(), intro: '', intro_refs: [], sections: [], set_aside: 0, curated: false } }));
	await freshPage(page, '/today');
	await expect(page.getByText('A little of what you follow, gathered in one place.')).toBeVisible();
	await expect(page.getByRole('status').filter({ hasText: 'Your first edition is taking shape' })).toBeVisible();
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
	for (const key of ['lead', 'stories', 'reads']) {
		await expect(page.locator('.summary').filter({ hasText: `Archived overview for ${key}.` })).toContainText('AI summary');
	}
});

test('manual curator rerun stays pending and refreshes after completion', async ({ page }) => {
	let state = { requested_at: 0, completed_at: 0 };
	await page.route(/\/api\/hub\/today(?:\?.*)?$/, route => route.fulfill({ json: { ...edition(), rerun: state } }));
	await page.route('**/api/hub/today/rerun', route => { state = { requested_at: 10, completed_at: 0 }; return route.fulfill({ json: state }); });
	await freshPage(page, '/today');
	await page.getByRole('button', { name: 'Rerun master curator' }).click();
	await expect(page.getByRole('button', { name: 'Curating…' })).toBeDisabled();
	state = { requested_at: 10, completed_at: 11 };
	await page.evaluate(() => window.dispatchEvent(new Event('focus')));
	await expect(page.getByRole('button', { name: 'Rerun master curator' })).toBeEnabled();
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


test('Today offers a deeper edition and labels related story runs', async ({ page }) => {
 const data = edition();
 data.sections.find(s => s.key === 'stories')!.entries = Array.from({ length: 140 }, (_, i) => ({
  item_id: 4000 + i, item: item(4000 + i, `Deep story ${i + 1}`),
  group_key: i < 3 ? 'curator:1' : 'curator:3', group_title: i < 3 ? 'AI research' : 'Home Assistant'
 }));
 data.sections.find(s => s.key === 'reads')!.entries = Array.from({ length: 60 }, (_, i) => ({
  item_id: 5000 + i, item: item(5000 + i, `Deep read ${i + 1}`, 'document')
 }));
 await page.route(/\/api\/hub\/today(?:\?.*)?$/, route => route.fulfill({ json: data }));
 await freshPage(page, '/today');
 await expect(page.getByRole('heading', { name: 'Stories · 140' })).toBeVisible();
 await expect(page.getByRole('heading', { name: 'Worth your time · 60' })).toBeVisible();
 await expect(page.getByRole('heading', { name: 'AI research', exact: true })).toHaveCount(1);
 await expect(page.getByRole('heading', { name: 'Home Assistant', exact: true })).toHaveCount(1);
 for (let i = 0; i < 4; i++) await page.getByRole('button', { name: 'Show 20 more stories' }).click();
 await expect(page.getByRole('link', { name: 'Deep story 96', exact: true })).toBeVisible();
 await expect(page.getByRole('link', { name: 'Deep story 97', exact: true })).toHaveCount(0);
 await page.setViewportSize({ width: 390, height: 844 });
 expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});


test('News starts with Today and continues into the news feed on the same page', async ({ page }) => {
	await page.route(/\/api\/hub\/today(?:\?.*)?$/, route => route.fulfill({ json: edition() }));
	await page.route('**/api/hub/news?*', route => route.fulfill({ json: { topics: [], groups: [{
		key: 'extra', title: 'Another news story', latest: 1783000000,
		items: [item(900, 'Another news story')]
	}] } }));
	await freshPage(page, '/news');
	await expect(page.getByRole('heading', { name: 'News', exact: true, level: 1 })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'A thoughtful lead' })).toBeVisible();
	const today = page.getByRole('region', { name: 'Today', exact: true });
	const feed = page.locator('#news-feed');
	expect(await today.evaluate((node, next) => !!(node.compareDocumentPosition(document.querySelector(next)!) & Node.DOCUMENT_POSITION_FOLLOWING), '#news-feed')).toBe(true);
	await page.getByRole('link', { name: 'Explore news' }).click();
	await expect(page.getByRole('heading', { name: 'More news', exact: true })).toBeInViewport();
	await expect(feed.getByRole('heading', { name: 'Another news story' })).toBeVisible();
	await page.evaluate(() => window.scrollTo(0, 0));
	await page.screenshot({ path: '/tmp/watchcraft-combined-news.png', fullPage: true });
});
