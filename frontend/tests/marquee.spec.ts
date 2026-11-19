import { expect, test } from '@playwright/test';
import { freshPage } from './helpers';

const movieId = 'a'.repeat(32);
const seriesId = 'b'.repeat(32);
const episodeId = 'c'.repeat(32);
const seasonId = 'd'.repeat(32);
const trailerId = 'e'.repeat(32);
const item = (id: string, name: string, type: string) => ({
	id, name, type, overview: 'A mystery unfolds.', runtime_s: 5400, official_rating: 'PG-13',
	community_rating: 7.4, genres: ['Mystery'], played: false, favorite: false, progress: 0,
	playable: type === 'Movie' || type === 'Episode'
});

test('movie details show trailer playback and credits, and episodes have their own details', async ({ page }) => {
	await page.route(`**/api/jellyfin/items/${movieId}`, (route) => route.fulfill({ json: {
		...item(movieId, 'Quiet Film', 'Movie'), trailer: { item_id: trailerId }, studios: ['Studio One'],
		people: [
			{ name: 'An Actor', role: 'Detective', type: 'Actor', image: null },
			{ name: 'A Director', role: '', type: 'Director', image: null }
		]
	} }));
	await freshPage(page, `/jellyfin/item/${movieId}`);
	await expect(page.getByRole('link', { name: 'Play Trailer' })).toHaveAttribute('href', `/jellyfin/play/${trailerId}?start=0`);
	await expect(page.getByRole('region', { name: 'Cast' }).getByText('Detective')).toBeVisible();
	await expect(page.getByRole('region', { name: 'Details' }).getByText('A Director')).toBeVisible();
	await expect(page.getByRole('region', { name: 'Details' }).getByText('PG-13')).toBeVisible();

	const episode = { ...item(episodeId, 'Pilot', 'Episode'), episode: 1, season: 1, series_id: seriesId,
		series_name: 'Quiet Series', official_rating: 'TV-14', rating_from_series: true, premiere: '2025-01-02' };
	await page.route(`**/api/jellyfin/items/${seriesId}`, (route) => route.fulfill({ json: {
		...item(seriesId, 'Quiet Series', 'Series'), seasons: [item(seasonId, 'Season 1', 'Season')]
	} }));
	await page.route(`**/api/jellyfin/shows/${seriesId}/episodes?**`, (route) => route.fulfill({ json: { items: [episode] } }));
	await page.route(`**/api/jellyfin/items/${episodeId}`, (route) => route.fulfill({ json: episode }));
	await page.goto(`/jellyfin/item/${seriesId}`);
	await expect(page.getByRole('button', { name: 'Play episode 1: Pilot' })).toBeVisible();
	await page.getByRole('link', { name: 'Details for Pilot' }).click();
	await expect(page.getByRole('heading', { name: 'Pilot' })).toBeVisible();
	await expect(page.getByText('TV-14 (series rating)')).toBeVisible();
	await expect(page.getByRole('region', { name: 'Details' }).getByText('2025-01-02')).toBeVisible();
});
