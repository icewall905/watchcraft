import { test, expect } from '@playwright/test';
import { freshPage } from './helpers';

// The Marquee library page: age and genre filters, and going back from a title keeps the list and scroll spot.
const LIB = 'e'.repeat(32);
const id = (i: number) => i.toString(16).padStart(32, '0');
const item = (i: number) => ({
	id: id(i), name: `Film ${i}`, type: 'Movie', collection_type: null, year: 2020, overview: '', runtime_s: 7200,
	official_rating: 'PG', community_rating: null, genres: [], series_id: null, series_name: null, season_id: null,
	season: null, episode: null, premiere: null, is_folder: false, child_count: null, unplayed: null, played: false,
	favorite: false, position_s: 0, progress: 0, image: null, thumb: null, backdrop: null, playable: true
});

test.use({ viewport: { width: 390, height: 800 } });

test('library filters by age and genre, and back from a title returns to the same spot', async ({ page }) => {
	const queries: URLSearchParams[] = [];
	await page.route('**/api/jellyfin/status', (r) => r.fulfill({ json: { configured: true, allowed: true, linked: true, user_name: 'me', is_admin: false, server_name: 'Den', version: '10.10' } }));
	await page.route('**/api/jellyfin/filters*', (r) => r.fulfill({ json: { genres: ['Action', 'Comedy'], ages: [3, 7, 13] } }));
	await page.route('**/api/jellyfin/items?*', (r) => {
		const q = new URL(r.request().url()).searchParams;
		queries.push(q);
		const start = Number(q.get('start') ?? 0);
		return r.fulfill({ json: { items: Array.from({ length: 60 }, (_, i) => item(start + i + 1)), total: 120 } });
	});
	await page.route('**/api/jellyfin/items/*', (r) => {
		const last = new URL(r.request().url()).pathname.split('/').pop()!;
		return r.fulfill({ json: last === LIB ? { ...item(0), id: LIB, name: 'Movies', type: 'CollectionFolder', collection_type: 'movies', playable: false } : item(parseInt(last, 16)) });
	});
	await freshPage(page, `/jellyfin/library/${LIB}`);
	await expect(page.getByRole('link', { name: /Film 60/ })).toBeVisible();

	await page.getByRole('toolbar', { name: 'Suitable for age' }).getByRole('button', { name: '7', exact: true }).click();
	await page.getByRole('toolbar', { name: 'Genre' }).getByRole('button', { name: 'Action' }).click();
	await expect.poll(() => queries.at(-1)?.get('genre')).toBe('Action');
	expect(queries.at(-1)?.get('max_age')).toBe('7');

	await page.getByRole('button', { name: /Show more/ }).click();
	const target = page.getByRole('link', { name: /Film 90/ });
	await target.scrollIntoViewIfNeeded();
	const y = await page.evaluate(() => window.scrollY);
	expect(y).toBeGreaterThan(1000);
	const fetched = queries.length;
	await target.click();
	await expect(page).toHaveURL(new RegExp(`/jellyfin/(item|play)/${id(90)}`));
	await page.goBack();
	await expect(page.getByRole('link', { name: /Film 120/ })).toBeAttached();
	await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(y - 50);
	expect(queries.length).toBe(fetched);
	await expect(page.getByRole('toolbar', { name: 'Genre' }).getByRole('button', { name: 'Action' })).toHaveAttribute('aria-pressed', 'true');
});
