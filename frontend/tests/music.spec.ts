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
	await page.route('**/api/music/home', (r) => r.fulfill({ json: { favorites: { tracks: LOVED, artists: [{ name: 'ABBA', artist_id: '', key: 'abba' }] }, mixes: [], recent: [], recent_mixes: [], ai: true, curators, offline: { wanted: 0, saved: 0, pending: 0 } } }));
	await page.route('**/api/music/for-you*', (r) => r.fulfill({ json: { loved: LOVED, fresh: FRESH, generated_at: 1 } }));
	await page.route('**/api/music/plays', (r) => r.fulfill({ json: { ok: true } }));
	await page.route('**/api/music/lyrics/**', (r) => r.fulfill({ json: { video_id: 'x', status: 'missing', source: '', synced: false, lines: [], plain: '', offset_ms: 0, offset_source: '', duration: 0, transcript: { status: 'idle', progress: 0, enabled: true, lines: [] } } }));
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

test('karaoke lyrics follow the song and the timing can be nudged', async ({ page }) => {
	await fixture(page);
	// The video reports 12 s in: the second line is being sung.
	await page.addInitScript(() => Object.defineProperty(HTMLMediaElement.prototype, 'currentTime', { get: () => 12, set: () => {}, configurable: true }));
	const lines = [{ t: 5000, text: 'First line of the song' }, { t: 10000, text: 'Second line we sing now' }, { t: 16000, text: 'Third line is next' }];
	let saved: unknown = null;
	await page.route('**/api/music/lyrics/lovedtrack1', (r) => r.fulfill({ json: { video_id: 'lovedtrack1', status: 'found', source: 'lrclib', synced: true, lines, plain: '', offset_ms: 0, offset_source: '', duration: 200 } }));
	await page.route('**/api/music/lyrics/lovedtrack1/offset', (r) => { saved = r.request().postDataJSON(); return r.fulfill({ json: { video_id: 'lovedtrack1', status: 'found', source: 'lrclib', synced: true, lines, plain: '', offset_ms: 500, offset_source: 'manual', duration: 200 } }); });
	await freshPage(page, '/music');
	await page.getByRole('button', { name: 'Play Dancing Queen by ABBA' }).first().click();
	const deck = page.getByRole('region', { name: 'Music player' });
	await deck.getByTitle('Lyrics (karaoke)').click();
	await expect(deck.locator('.line.now')).toHaveText('Second line we sing now');
	// 12 s into a line sung from 10 s until the next line at 16 s: a third of the way, not finished early.
	await expect.poll(async () => Number(await deck.locator('.line.now').evaluate((el) => getComputedStyle(el).getPropertyValue('--p')))).toBeCloseTo(1 / 3, 2);
	await expect(deck.locator('.line.next').first()).toHaveText('Third line is next');
	await deck.getByRole('button', { name: 'Lyrics later' }).click();
	await expect(deck.getByText('+0.5s')).toBeVisible();
	await expect.poll(() => saved).toEqual({ offset_ms: 500 });
});

test('songs without lyrics can be transcribed by listening', async ({ page }) => {
	await fixture(page);
	let calls = 0;
	await page.route('**/api/music/lyrics/lovedtrack1/listen', (r) => {
		calls++;
		const missing = { video_id: 'lovedtrack1', status: 'missing', source: '', synced: false, lines: [], plain: '', offset_ms: 0, rate: 1, offset_source: '', duration: 0 };
		return r.fulfill({ json: { mode: 'transcript', lyrics: missing, message: calls < 2 ? 'Listening… 40%' : '', transcript: calls < 2 ? { status: 'running', progress: 40, enabled: true, lines: [] } : { status: 'ready', progress: 100, enabled: true, lines: [{ t: 0, end: 60000, text: 'Words the app heard' }] } } });
	});
	await freshPage(page, '/music');
	await page.getByRole('button', { name: 'Play Dancing Queen by ABBA' }).first().click();
	const deck = page.getByRole('region', { name: 'Music player' });
	await deck.getByTitle('Lyrics (karaoke)').click();
	await deck.getByRole('button', { name: /Listen and write them/ }).click();
	await expect(deck.getByText(/Listening… 40%/)).toBeVisible();
	await expect(deck.getByText('Words the app heard')).toBeVisible({ timeout: 8000 });
	await expect(deck.getByText(/heard by the app/)).toBeVisible();
});

test('what is playing can be saved as a mix, and a curated mix kept as a copy', async ({ page }) => {
	await fixture(page);
	let body: Record<string, unknown> | null = null;
	const saved = { id: 11, name: 'Friday', kind: 'manual', emoji: '💿', description: '', curator: '', curator_name: '', color: '', blurb: '', seed_video_id: '', status: 'ready', error: '', count: 2, created_at: 2, refreshed_at: 2, last_played_at: 0, covers: [] };
	await page.route('**/api/music/mixes', (r) => {
		if (r.request().method() === 'POST') { body = r.request().postDataJSON(); return r.fulfill({ json: saved }); }
		return r.fulfill({ json: { mixes: [] } });
	});
	await freshPage(page, '/music');
	await page.getByRole('heading', { name: '🌱 New for you' }).locator('..').getByRole('button', { name: '▶ Play' }).click();
	page.once('dialog', (d) => d.accept('Friday'));
	await page.getByRole('region', { name: 'Music player' }).getByRole('button', { name: 'Save as mix' }).click();
	await expect(page.getByText('💾 Saved "Friday" with 2 songs')).toBeVisible();
	expect(body).toMatchObject({ kind: 'manual', name: 'Friday' });
	expect((body!.tracks as { video_id: string }[]).map((t) => t.video_id)).toEqual(['freshtrack1', 'freshtrack2']);
	await expect(page.getByRole('heading', { name: '💿 Recent mixes' })).toBeVisible();
	await expect(page.getByRole('button', { name: /💿 Friday/ })).toBeVisible();
});

test('the front page shows recent mixes and recent plays first', async ({ page }) => {
	await fixture(page);
	const mix = { id: 3, name: 'Roller Rink', kind: 'curator', emoji: '🛼', description: '', curator: 'retro', curator_name: 'Rewind Rae', color: '#ffb020', blurb: 'Lace up', seed_video_id: '', status: 'ready', error: '', count: 2, created_at: 1, refreshed_at: 1, last_played_at: 5, covers: [] };
	await page.route('**/api/music/home', (r) => r.fulfill({ json: { favorites: { tracks: [], artists: [] }, mixes: [mix], recent: [FRESH[1]], recent_mixes: [mix], ai: true, curators, offline: { wanted: 0, saved: 0, pending: 0 } } }));
	let copied = false;
	await page.route('**/api/music/mixes/3', (r) => r.fulfill({ json: { ...mix, tracks: FRESH } }));
	await page.route('**/api/music/mixes/3/copy', (r) => { copied = true; return r.fulfill({ json: { ...mix, id: 4, kind: 'manual', name: 'Roller Rink (saved)', tracks: FRESH } }); });
	await page.route('**/api/music/mixes/4', (r) => r.fulfill({ json: { ...mix, id: 4, kind: 'manual', name: 'Roller Rink (saved)', tracks: FRESH } }));
	await freshPage(page, '/music');
	const headings = page.locator('.shelf h2');
	await expect(headings.nth(0)).toHaveText('💿 Recent mixes');
	await expect(headings.nth(1)).toHaveText('🕘 Recently played');
	await page.getByRole('button', { name: /🛼 Roller Rink/ }).first().click();
	await page.getByRole('button', { name: '💾 Keep a copy' }).click();
	await expect(page.getByRole('dialog', { name: /Roller Rink \(saved\)/ })).toBeVisible();
	expect(copied).toBe(true);
});

test('auto-sync listens to samples, shows progress and applies the fit', async ({ page }) => {
	await fixture(page);
	await page.addInitScript(() => Object.defineProperty(HTMLMediaElement.prototype, 'currentTime', { get: () => 12, set: () => {}, configurable: true }));
	const lines = [{ t: 5000, text: 'First line of the song' }, { t: 10000, text: 'Second line we sing now' }, { t: 16000, text: 'Third line is next' }];
	const base = { video_id: 'lovedtrack1', status: 'found', source: 'lrclib', synced: true, lines, plain: '', offset_source: '', duration: 200 };
	await page.route('**/api/music/lyrics/lovedtrack1', (r) => r.fulfill({ json: { ...base, offset_ms: 0, rate: 1 } }));
	const bodies: { start: boolean }[] = [];
	await page.route('**/api/music/lyrics/lovedtrack1/listen', (r) => {
		bodies.push(r.request().postDataJSON());
		const done = bodies.length >= 3;
		return r.fulfill({ json: { mode: 'sync', lyrics: { ...base, offset_ms: done ? 6000 : 0, rate: 1, offset_source: done ? 'auto' : '' },
			job: done ? { state: 'done', progress: 100, message: 'Lined up on 9 lines (+6.0 s)' } : { state: 'listening', progress: 40, message: 'Listening… 3 of 10' } } });
	});
	await freshPage(page, '/music');
	await page.getByRole('button', { name: 'Play Dancing Queen by ABBA' }).first().click();
	const deck = page.getByRole('region', { name: 'Music player' });
	await deck.getByTitle('Lyrics (karaoke)').click();
	await expect(deck.locator('.line.now')).toHaveText('Second line we sing now');
	await deck.getByRole('button', { name: /Auto-sync/ }).click();
	await expect(deck.getByText('Listening… 3 of 10')).toBeVisible();
	await expect(page.getByText('🎯 Lined up on 9 lines (+6.0 s)')).toBeVisible({ timeout: 10000 });
	// 12 s on the video is 6 s in the lyrics now: the first line.
	await expect(deck.locator('.line.now')).toHaveText('First line of the song');
	expect(bodies[0]).toEqual({ start: true });
	expect(bodies.slice(1).every((b) => b.start === false)).toBe(true);
});

test('a failed auto-sync says why and stops', async ({ page }) => {
	await fixture(page);
	const lines = [{ t: 5000, text: 'First line of the song' }, { t: 10000, text: 'Second line we sing now' }];
	const base = { video_id: 'lovedtrack1', status: 'found', source: 'lrclib', synced: true, lines, plain: '', offset_ms: 0, rate: 1, offset_source: '', duration: 200 };
	await page.route('**/api/music/lyrics/lovedtrack1', (r) => r.fulfill({ json: base }));
	let calls = 0;
	await page.route('**/api/music/lyrics/lovedtrack1/listen', (r) => {
		calls++;
		return r.fulfill({ json: { mode: 'sync', lyrics: base, job: { state: 'failed', progress: 0, message: "Couldn't hear enough of the words to line them up — nudge them by hand." } } });
	});
	await freshPage(page, '/music');
	await page.getByRole('button', { name: 'Play Dancing Queen by ABBA' }).first().click();
	const deck = page.getByRole('region', { name: 'Music player' });
	await deck.getByTitle('Lyrics (karaoke)').click();
	await deck.getByRole('button', { name: /Auto-sync/ }).click();
	await expect(page.getByText(/Couldn't hear enough of the words/)).toBeVisible();
	await expect(deck.getByRole('button', { name: /Auto-sync/ })).toBeEnabled();
	await page.waitForTimeout(3500);
	expect(calls).toBe(1); // no silent retry loop
});
