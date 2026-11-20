import { test, expect, type Page } from '@playwright/test';
import { freshPage } from './helpers';

const at = (day: number) => new Date(2026, 10, day, 12).getTime() / 1000;
const KEYS = ['overall', 'videos', 'shorts', 'news', 'articles', 'memes', 'web', 'moodboards', 'music'];
const TITLES = ['Overall', 'Videos', 'Shorts', 'News', 'Articles', 'Memes', 'Web', 'Mood boards', 'Music'];
const statement = (sid: string, text: string, extra: Record<string, unknown> = {}) =>
	({ sid, text, polarity: 1, confidence: 'established', locked: false, teaching_ids: [1], board_id: null, since: at(10), updated: at(12), ...extra });
const sections = (filled: Record<string, unknown[]>) => KEYS.map((key, i) => ({ key, title: TITLES[i], statements: filled[key] ?? [] }));
const version = (id: number, n: number, cause = 'nightly') =>
	({ id, version: n, cause, created_at: at(14), changes: { added: [{ sid: 's1', section: 'overall', text: 'Calm science explainers', polarity: 1 }], changed: [], removed: [], merged: [1] } });
const signal = (kind: string, surface: string, title: string, day: number, extra: Record<string, unknown> = {}) =>
	({ kind, surface, ref_type: 'item', ref_id: day, title, at: at(day), ...extra });
const teaching = (id: number, text: string, signals: unknown[], extra: Record<string, unknown> = {}) => ({
	id, surface: 'short', section: 'shorts', polarity: -1, text, reason: 'Two quick dislikes in a row.', signals, status: 'pending',
	user_authored: false, origin: 'signals', board_id: null, correction: '', note: '', merged_version_id: null, rejected_at: null,
	created_at: at(14), updated_at: at(14), ...extra
});
const pranks = teaching(7, 'Avoid prank shorts', [signal('dislike', 'short', 'Loud prank', 13), signal('dislike', 'short', 'Another prank', 14)]);

type Call = { method: string; path: string; body: unknown };

async function mockCurator(page: Page, opts: { acceptStatus?: number } = {}) {
	const calls: Call[] = [];
	let thread: unknown[] = [];
	let current = {
		version: version(3, 3),
		sections: sections({
			overall: [statement('s1', 'Calm science explainers', { confidence: 'explicit', locked: true })],
			shorts: [statement('s2', 'Shouting reaction shorts', { polarity: -1, confidence: 'tentative' })]
		})
	};
	const history = [
		{ id: 3, version: 3, cause: 'nightly', created_at: at(14), added: 1, changed: 0, removed: 0, merged: 1 },
		{ id: 1, version: 1, cause: 'migration', created_at: at(10), added: 2, changed: 0, removed: 0, merged: 0 }
	];
	const proposal = { id: 41, role: 'curator', text: 'Shall I drop that?', proposal: { ops: [{ op: 'remove', section: 'shorts', sid: 's2', text: null, polarity: -1, before: 'Shouting reaction shorts' }] },
		proposal_status: 'pending', base_version_id: 3, applied_version_id: null, created_at: at(15) };
	await page.route('**/api/curator/**', async (route) => {
		const req = route.request(), url = new URL(req.url()), path = url.pathname.replace('/api/curator', ''), method = req.method();
		const body = req.postDataJSON?.() ?? null;
		calls.push({ method, path: path + url.search, body });
		const json = (data: unknown, status = 200) => route.fulfill({ status, json: data });
		if (method === 'GET' && path === '/knowledge') return json({ ...current, history, pending: { short: 1, article: 0 } });
		if (method === 'GET' && path === '/chat') return json({ messages: thread });
		if (method === 'GET' && path === '/knowledge/statements/s1/why')
			return json({ statement: current.sections[0].statements[0], teachings: [teaching(1, 'Prefer calm science explainers',
				[signal('like', 'video', 'How tides work', 12, { url: 'https://example.test/tides' }), signal('note', 'correction', 'Settings', 13, { note: 'more like this' }), signal('read', 'news', 'Sneaky feed', 13, { url: 'javascript:alert(1)' })],
				{ polarity: 1, user_authored: true })] });
		if (method === 'GET' && path === '/teachings') return json({ teachings: url.searchParams.get('status') === 'pending' ? [pranks] : [] });
		if (method === 'POST' && path === '/teachings/7/reject') {
			const reply = { ...proposal, id: 40, text: 'Got it — only pranks that hurt people?', proposal: {}, proposal_status: 'none' };
			thread = [{ ...reply, id: 38, role: 'user', text: 'You learned “Avoid prank shorts” wrong.' }, reply];
			return json({ teaching: { ...pranks, status: 'rejected' }, correction: null,
				message: reply });
		}
		if (method === 'POST' && path === '/chat')
			return json({ messages: [null, proposal] });
		if (method === 'POST' && path === '/chat/41/accept') {
			if (opts.acceptStatus) return json({ detail: 'stale' }, opts.acceptStatus);
			current = { version: version(4, 4, 'chat'), sections: sections({ overall: [statement('s1', 'Calm science explainers'), statement('s3', 'Gentle nature documentaries')] }) };
			return json({ message: { ...proposal, proposal_status: 'accepted' }, ...current });
		}
		if (method === 'PATCH' && path === '/knowledge/statements/s2') {
			current = { version: version(4, 4, 'edit'), sections: sections({ overall: current.sections[0].statements }) };
			return json(current);
		}
		if (method === 'GET' && path === '/knowledge/versions/1') return json({ ...version(1, 1, 'migration'), sections: current.sections });
		if (method === 'POST' && path === '/knowledge/versions/1/restore') return json(version(5, 5, 'undo'));
		return json({ detail: `unmocked ${method} ${path}` }, 404);
	});
	return calls;
}

test('knowledge sections render and Why lists the signals behind a statement', async ({ page }) => {
	await mockCurator(page);
	await freshPage(page, '/settings?tab=curator');
	await expect(page.getByRole('heading', { name: 'What I know about you' })).toBeVisible();
	await expect(page.getByText('Version 3 · woven', { exact: false })).toBeVisible();
	const overall = page.getByRole('region', { name: 'Overall', exact: true });
	await expect(overall.getByText('Calm science explainers')).toBeVisible();
	await expect(overall.getByText('You told me')).toBeVisible();
	await expect(overall.getByRole('img', { name: 'Locked' })).toBeVisible();
	const shorts = page.getByRole('region', { name: 'Shorts', exact: true });
	await expect(shorts.getByText('Avoid', { exact: true })).toBeVisible();
	await expect(shorts.getByText('Tentative')).toBeVisible();
	await expect(page.getByRole('region', { name: 'Music', exact: true })).toHaveCount(0);
	await overall.getByRole('button', { name: 'Why?' }).click();
	await expect(overall.getByText('Liked', { exact: true })).toBeVisible();
	await expect(overall.getByRole('link', { name: 'How tides work' })).toHaveAttribute('href', 'https://example.test/tides');
	await expect(overall.getByText('more like this')).toBeVisible();
	await expect(overall.getByText('Sneaky feed')).toBeVisible();
	await expect(overall.getByRole('link', { name: 'Sneaky feed' })).toHaveCount(0);
	await overall.getByRole('button', { name: "That's wrong" }).click();
	await expect(page.getByPlaceholder('Tell me what I got wrong…')).toHaveValue('That\'s wrong: "Calm science explainers"');
	await expect(page.getByPlaceholder('Tell me what I got wrong…')).toBeFocused();
});

test('Forget & correct sends the correction and appends the curator reply', async ({ page }) => {
	const calls = await mockCurator(page);
	await freshPage(page, '/settings?tab=curator');
	const card = page.locator('li.teaching', { hasText: 'Avoid prank shorts' });
	await expect(card.getByText('Because you disliked 2 shorts: Loud prank, Another prank (Nov 13–14)')).toBeVisible();
	await expect(card.getByText('Two quick dislikes in a row.')).toBeVisible();
	await card.getByRole('button', { name: 'Forget this' }).click();
	await expect(card.getByRole('button', { name: 'Forget & correct' })).toBeDisabled();
	await card.getByLabel('What did you actually mean? (optional)').fill('Only pranks where someone gets hurt');
	await card.getByRole('button', { name: 'Forget & correct' }).click();
	await expect(page.getByText('Got it — only pranks that hurt people?')).toBeVisible();
	await expect(page.getByText('You learned “Avoid prank shorts” wrong.')).toBeVisible();
	expect(calls.filter((c) => c.method === 'GET' && c.path === '/chat')).toHaveLength(2);
	expect(calls.find((c) => c.path === '/teachings/7/reject')?.body).toEqual({ correction: 'Only pranks where someone gets hurt' });
});

test('chat shows a proposal and Apply refreshes the knowledge', async ({ page }) => {
	await mockCurator(page);
	await freshPage(page, '/settings?tab=curator');
	await page.getByPlaceholder('Tell me what I got wrong…').fill('Shouting shorts are fine');
	await page.getByRole('button', { name: 'Send', exact: true }).click();
	await expect(page.getByText('Shall I drop that?')).toBeVisible();
	await expect(page.getByText('− remove (Shorts) Shouting reaction shorts')).toBeVisible();
	await page.getByRole('button', { name: 'Apply', exact: true }).click();
	await expect(page.getByText('Applied', { exact: true })).toBeVisible();
	await expect(page.getByText('Gentle nature documentaries')).toBeVisible();
	await expect(page.getByRole('region', { name: 'Shorts', exact: true })).toHaveCount(0);
	await expect(page.getByText('Version 4 · woven', { exact: false })).toBeVisible();
});

test('a stale proposal explains that the knowledge changed', async ({ page }) => {
	await mockCurator(page, { acceptStatus: 409 });
	await freshPage(page, '/settings?tab=curator');
	await page.getByPlaceholder('Tell me what I got wrong…').fill('Shouting shorts are fine');
	await page.getByRole('button', { name: 'Send', exact: true }).click();
	await page.getByRole('button', { name: 'Apply', exact: true }).click();
	await expect(page.getByText('What I know changed since this suggestion — ask again.')).toBeVisible();
	await expect(page.getByRole('button', { name: 'Apply', exact: true })).toHaveCount(0);
});

test('history shows a version diff and restores it', async ({ page }) => {
	const calls = await mockCurator(page);
	await freshPage(page, '/settings?tab=curator');
	await page.getByRole('button', { name: 'History', exact: true }).click();
	await expect(page.getByRole('button', { name: /Version 3 Nightly weave · current/ })).toBeVisible();
	await page.getByRole('button', { name: /Version 1 Initial import/ }).click();
	await expect(page.getByText('+ Overall, Prefer: Calm science explainers')).toBeVisible();
	await page.getByRole('button', { name: 'Restore this version' }).click();
	await page.getByRole('button', { name: 'Yes, restore version 1' }).click();
	await expect.poll(() => calls.some((c) => c.method === 'POST' && c.path === '/knowledge/versions/1/restore')).toBe(true);
	await expect(page.getByRole('heading', { name: 'History', exact: true })).toHaveCount(0);
});

test('curator settings fit a phone without horizontal scroll', async ({ page }) => {
	await mockCurator(page);
	await page.setViewportSize({ width: 375, height: 812 });
	await freshPage(page, '/settings?tab=curator');
	const overall = page.getByRole('region', { name: 'Overall', exact: true });
	await overall.getByRole('button', { name: 'Why?' }).click();
	await expect(overall.getByText('How tides work')).toBeVisible();
	await page.locator('li.teaching').getByRole('button', { name: 'Forget this' }).click();
	await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
});

test('removing the last statement in a section keeps focus on the knowledge', async ({ page }) => {
	const calls = await mockCurator(page);
	await freshPage(page, '/settings?tab=curator');
	const shorts = page.getByRole('region', { name: 'Shorts', exact: true });
	await shorts.getByRole('button', { name: 'Remove', exact: true }).click();
	await shorts.getByRole('button', { name: 'Confirm remove', exact: true }).click();
	await expect(shorts).toHaveCount(0);
	expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ remove: true });
	await expect(page.getByRole('heading', { name: 'What I know about you' })).toBeFocused();
});
