import { expect, test, type Page } from '@playwright/test';
import { freshPage, resetBackend } from './helpers';

const t = (id: string, title: string, artist: string, extra: Record<string, unknown> = {}) => ({
	video_id: id, title, artist, artist_id: '', views: 1_000_000, duration_s: 200, thumbnail: '', fav: false, fav_artist: false, ...extra
});
const LOVED = [t('lovedtrack1', 'Dancing Queen', 'ABBA', { fav: true })];
const FRESH = [t('freshtrack1', 'Titanium', 'David Guetta', { reason: 'New for you' }), t('freshtrack2', 'Halo', 'Beyoncé', { reason: 'New for you' })];
const DJ = [t('djtrack0001', 'Take On Me', 'a-ha', { reason: '80s gold' }), t('djtrack0002', 'Mr. Brightside', 'The Killers'), t('djtrack0003', 'Kiss', 'Prince')];
const curators = [{ key: 'disco', name: 'Disco Dee', emoji: '🪩', color: '#ff4fa3', taste: 'bangers' }, { key: 'retro', name: 'Rewind Rae', emoji: '📼', color: '#ffb020', taste: 'classics' }];

async function fixture(page: Page) {
	// No MSE and inert media: the deck mounts and stays on its first track without real streams.
	await page.addInitScript(() => {
		Object.defineProperty(window, 'MediaSource', { value: undefined });
		HTMLMediaElement.prototype.play = async function () {};
	});
	await page.route('**/api/music/home', (r) => r.fulfill({ json: { favorites: { tracks: LOVED, artists: [{ name: 'ABBA', artist_id: '', key: 'abba' }] }, mixes: [], recent: [], ai: true, curators } }));
	await page.route('**/api/music/for-you*', (r) => r.fulfill({ json: { loved: LOVED, fresh: FRESH, generated_at: 1 } }));
	await page.route('**/api/music/plays', (r) => r.fulfill({ json: { ok: true } }));
	// Streams never answer: the deck stays on its first track while the test drives the UI.
	await page.route('**/api/video/**', () => new Promise(() => {}));
}

test.beforeAll(async ({ request }) => resetBackend(request));

test('music page shows loved and fresh shelves and the nav entry', async ({ page }) => {
	await fixture(page);
	await freshPage(page, '/music');
	await expect(page.getByRole('link', { name: 'Music' }).first()).toBeVisible();
	await expect(page.getByRole('heading', { name: '💖 Songs you love' })).toBeVisible();
	await expect(page.getByRole('heading', { name: '🌱 New for you' })).toBeVisible();
	await expect(page.getByText('Titanium')).toBeVisible();
	await page.getByRole('button', { name: '❤️ Favourites' }).click();
	await expect(page.getByRole('button', { name: 'ABBA', exact: true })).toBeVisible();
});

test('DJ starts a party set, takes a request and slots it in next', async ({ page }) => {
	await fixture(page);
	const bodies: Record<string, unknown>[] = [];
	await page.route('**/api/music/dj', async (r) => {
		const body = r.request().postDataJSON();
		bodies.push(body);
		if (body.request) return r.fulfill({ json: { line: 'Request granted!', source: 'ai', tracks: [t('requested01', 'Dancing Queen', 'ABBA', { reason: 'Guest request' })] } });
		return r.fulfill({ json: { line: 'Hands in the air!', source: 'ai', tracks: DJ } });
	});
	await freshPage(page, '/music');
	await page.getByRole('button', { name: /Throwback/ }).first().click();
	const deck = page.getByRole('region', { name: 'Music player' });
	await expect(deck.getByRole('heading', { name: 'Take On Me' })).toBeVisible();
	await expect(deck.getByText('🎧 DJ booth', { exact: true })).toBeVisible();
	await expect(deck.getByText('Hands in the air!').first()).toBeVisible();
	expect(bodies[0]).toMatchObject({ vibe: 'Throwback', n: 3 });

	await deck.getByPlaceholder('Request a song or artist…').fill('some ABBA please');
	await deck.getByRole('button', { name: 'Request', exact: true }).click();
	await expect(deck.getByText('🙋 some ABBA please')).toBeVisible();
	await expect(deck.locator('.upnext li').first()).toContainText('Dancing Queen');
	expect(bodies.at(-1)).toMatchObject({ request: 'some ABBA please', n: 1 });
	expect((bodies.at(-1)!.queue as string[])).toContain('djtrack0001');
});

test('a curator builds a mix from a description', async ({ page }) => {
	await fixture(page);
	let status = 'building';
	const mix = () => ({ id: 7, name: status === 'ready' ? 'Roller Rink Retro' : 'Curating…', kind: 'curator', emoji: '📼', description: '80s roller disco', curator: 'retro', curator_name: 'Rewind Rae', color: '#ffb020', blurb: status === 'ready' ? 'Lace up!' : '', seed_video_id: '', status, error: '', count: status === 'ready' ? 2 : 0, created_at: 1, refreshed_at: 0, covers: [], tracks: status === 'ready' ? FRESH : [] });
	let created: Record<string, unknown> | null = null;
	await page.route('**/api/music/mixes', (r) => {
		if (r.request().method() === 'POST') { created = r.request().postDataJSON(); return r.fulfill({ json: mix() }); }
		return r.fulfill({ json: { mixes: [mix()] } });
	});
	await page.route('**/api/music/mixes/7', (r) => r.fulfill({ json: mix() }));
	await freshPage(page, '/music?tab=mixes');
	await page.getByRole('button', { name: /New mix/ }).click();
	await page.getByRole('button', { name: '80s roller disco' }).click();
	await page.getByRole('button', { name: /Rewind Rae/ }).click();
	await page.getByRole('button', { name: 'Ask Rewind Rae' }).click();
	await expect(page.getByText(/digging through YouTube Music/)).toBeVisible();
	expect(created).toMatchObject({ kind: 'curator', description: '80s roller disco', curator: 'retro' });
	status = 'ready';
	await expect(page.getByRole('dialog', { name: /Roller Rink Retro/ })).toBeVisible({ timeout: 8000 });
	await expect(page.getByText('Lace up!')).toBeVisible();
	await expect(page.getByRole('dialog').getByText('Halo')).toBeVisible();
});

test('a video that will not play is skipped and the set goes on', async ({ page }) => {
	await fixture(page);
	await page.route('**/api/video/djtrack0001/**', (r) => r.fulfill({ status: 404, json: { detail: 'no streams' } }));
	await page.route('**/api/music/dj', (r) => r.fulfill({ json: { line: 'Go!', source: 'radio', tracks: DJ } }));
	await freshPage(page, '/music');
	await page.getByRole('button', { name: /Party/ }).first().click();
	const deck = page.getByRole('region', { name: 'Music player' });
	await expect(deck.getByRole('heading', { name: 'Mr. Brightside' })).toBeVisible();
	await expect(page.getByText(/Skipping "Take On Me"/)).toBeVisible();
});

test('the DJ recovers when its first dig fails', async ({ page }) => {
	await fixture(page);
	let calls = 0;
	await page.route('**/api/music/dj', (r) => (++calls === 1 ? r.fulfill({ status: 503, json: { detail: 'busy' } }) : r.fulfill({ json: { line: 'Back on!', source: 'radio', tracks: DJ } })));
	await freshPage(page, '/music');
	await page.getByRole('button', { name: /Dance/ }).first().click();
	const deck = page.getByRole('region', { name: 'Music player' });
	await expect(deck).toHaveCount(0); // nothing to play yet
	await expect(page.getByRole('region', { name: 'Music player' }).getByRole('heading', { name: 'Take On Me' })).toBeVisible({ timeout: 12000 });
});

test('a song stored offline plays from the server copy', async ({ page }) => {
	await fixture(page);
	const saved = t('savedtrack1', 'Saved Song', 'Local Band', { offline: true });
	await page.route('**/api/music/for-you*', (r) => r.fulfill({ json: { loved: [saved], fresh: FRESH, generated_at: 1 } }));
	await page.route('**/api/music/home', (r) => r.fulfill({ json: { favorites: { tracks: [saved], artists: [] }, mixes: [], recent: [], ai: false, curators, offline: { wanted: 3, saved: 1, pending: 2 } } }));
	await freshPage(page, '/music');
	await expect(page.getByText('💾 1 of 3 songs saved offline · 2 on the way')).toBeVisible();
	await page.getByRole('button', { name: 'Play Saved Song by Local Band' }).click();
	await expect(page.locator('section[aria-label="Music player"] video')).toHaveAttribute('src', '/api/video/savedtrack1/file');
});
