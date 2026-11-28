import { test, expect, type Page } from '@playwright/test';
import { freshPage } from './helpers';

// The Marquee (Jellyfin) in Find and Play on. Jellyfin itself is mocked; the backend relay is covered by pytest.
const MOVIE = 'a'.repeat(32);
const SERIES = 'b'.repeat(32);
const item = (o: Record<string, unknown>) => ({
	id: MOVIE, name: 'Film', type: 'Movie', collection_type: null, year: 2020, overview: '', runtime_s: 7200,
	official_rating: null, community_rating: null, genres: [], series_id: null, series_name: null, season_id: null,
	season: null, episode: null, premiere: null, is_folder: false, child_count: null, unplayed: null, played: false,
	favorite: false, position_s: 0, progress: 0, image: null, thumb: null, backdrop: null, playable: true, ...o
});
const status = (linked: boolean) => ({ configured: true, allowed: true, linked, user_name: 'me', is_admin: false, server_name: 'Den', version: '10.10' });

async function mockFind(page: Page, linked: boolean) {
	const searched: string[] = [];
	await page.route('**/api/hub/search*', (route) => route.fulfill({ json: { items: [], total: 0, videos: [], curators: [], collections: [], topics: [] } }));
	await page.route('**/api/jellyfin/status', (route) => route.fulfill({ json: status(linked) }));
	await page.route('**/api/jellyfin/search*', (route) => {
		searched.push(new URL(route.request().url()).searchParams.get('q') ?? '');
		return route.fulfill({ json: { items: [item({}), item({ id: SERIES, name: 'Show', type: 'Series', playable: false })] } });
	});
	return searched;
}

test('Find shows linked profiles The Marquee titles, playable ones open the player', async ({ page }) => {
	const searched = await mockFind(page, true);
	await freshPage(page, '/find?q=film');
	const group = page.getByRole('region', { name: 'The Marquee' });
	await expect(group.getByRole('heading', { name: 'The Marquee' })).toBeVisible();
	await expect(group.getByRole('link', { name: /Film/ })).toHaveAttribute('href', `/jellyfin/play/${MOVIE}`);
	await expect(group.getByRole('link', { name: /Show/ })).toHaveAttribute('href', `/jellyfin/item/${SERIES}`);
	expect(searched).toContain('film');
	// Jellyfin hits alone are results, not "nothing matches".
	await expect(page.getByRole('heading', { name: /Nothing in this profile matches/ })).toHaveCount(0);
});

test('Find skips The Marquee quietly for unlinked profiles', async ({ page }) => {
	const searched = await mockFind(page, false);
	await freshPage(page, '/find?q=film');
	await expect(page.getByRole('heading', { name: 'Nothing in this profile matches "film"' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'The Marquee' })).toHaveCount(0);
	expect(searched).toEqual([]);
});

test('Play on sends a Marquee title from its page, and the screen opens the player', async ({ page }) => {
	const sent: Record<string, unknown>[] = [];
	await page.route('**/api/jellyfin/items/' + MOVIE, (route) => route.fulfill({ json: item({ position_s: 600, progress: 0.08 }) }));
	await page.route('**/api/playback/targets', (route) => route.fulfill({ json: [
		{ id: 'tv1', name: 'Den TV', online: true, paired: true, state: {} }] }));
	await page.route('**/api/playback/targets/tv1/commands', (route) => {
		sent.push(route.request().postDataJSON());
		return route.fulfill({ json: { command_id: 'c1' } });
	});
	await freshPage(page, `/jellyfin/item/${MOVIE}`);
	await page.getByRole('button', { name: /Play on…/ }).click();
	const picker = page.getByRole('dialog', { name: 'Play on' });
	await expect(picker.getByText('Choose the screen for this title from The Marquee.')).toBeVisible();
	await picker.getByRole('button', { name: /Den TV/ }).click();
	await expect.poll(() => sent.length).toBe(1);
	expect(sent[0]).toMatchObject({ action: 'play', jellyfin_id: MOVIE });
	await expect(picker).toBeHidden();
	// With a screen chosen, the regular Play button sends there too.
	await page.getByRole('button', { name: /Resume from/ }).click();
	await expect.poll(() => sent.length).toBe(2);
	await expect(page).toHaveURL(new RegExp(`/jellyfin/item/${MOVIE}$`));
});

test('a registered screen opens a received Marquee title at the sent position', async ({ page }) => {
	const profiles = await (await page.request.get('/api/profiles')).json();
	let delivered = false;
	await page.route('**/api/playback/targets/rx1/heartbeat', (route) => {
		const command = delivered ? null : { id: 'cmd1', action: 'play', video_id: null, jellyfin_id: MOVIE, category_id: null, position_s: 42, profile_id: profiles[0].id, created_at: Date.now() / 1000 };
		delivered = true;
		return route.fulfill({ json: { command, pair_code: '123456' } });
	});
	await page.route('**/api/jellyfin/items/*/play', (route) => route.fulfill({ status: 409, json: { detail: 'mocked' } }));
	await freshPage(page, '/');
	await page.evaluate(() => localStorage.setItem('bt.playback.receiver', JSON.stringify({ id: 'rx1', name: 'Den TV', token: 't' })));
	await page.reload();
	await expect(page).toHaveURL(new RegExp(`/jellyfin/play/${MOVIE}\\?cast=cmd1&start=42$`), { timeout: 10000 });
});

test('a shared screen on another account plays a received Marquee title with the cast grant', async ({ page }) => {
	let delivered = false;
	const grants: (string | undefined)[] = [];
	await page.route('**/api/playback/targets/rx1/heartbeat', (route) => {
		// The sender's profile is not one of this screen's profiles: no switch, the grant does the work.
		const command = delivered ? null : { id: 'cmd2', action: 'play', video_id: null, jellyfin_id: MOVIE, category_id: null, position_s: null, profile_id: 987654, grant: 'g1', created_at: Date.now() / 1000 };
		delivered = true;
		return route.fulfill({ json: { command, pair_code: '123456' } });
	});
	let reported = '';
	await page.route('**/api/playback/targets/rx1/heartbeat', async (route) => {
		reported = route.request().postDataJSON()?.error ?? reported;
		return route.fallback();
	});
	await page.route('**/api/jellyfin/items/*/play', (route) => {
		grants.push(route.request().headers()['x-cast-grant']);
		return route.fulfill({ status: 409, json: { detail: 'mocked' } });
	});
	await freshPage(page, '/');
	await page.evaluate(() => localStorage.setItem('bt.playback.receiver', JSON.stringify({ id: 'rx1', name: 'Den TV', token: 't' })));
	await page.reload();
	await expect(page).toHaveURL(new RegExp(`/jellyfin/play/${MOVIE}\\?cast=cmd2$`), { timeout: 10000 });
	await expect.poll(() => grants.length).toBeGreaterThan(0);
	expect(grants[0]).toBe('g1');
	expect(reported).toBe('');
});
