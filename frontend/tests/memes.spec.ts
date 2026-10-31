import { test, expect } from '@playwright/test';
import { freshPage } from './helpers';

test('Meme Radar is in navigation and renders its empty states', async ({ page }) => {
	await freshPage(page, '/');
	const nav = page.getByRole('navigation', { name: 'Main', exact: true });
	await expect(nav.getByRole('link', { name: 'Meme Radar' })).toBeVisible();
	await nav.getByRole('link', { name: 'Meme Radar' }).click();
	await expect(page.getByRole('heading', { name: 'Meme Radar', level: 1 })).toBeVisible();
	for (const t of ['Trending', 'Emerging', 'Formats', 'Explore', 'Saved', 'History'])
		await expect(page.getByRole('tab', { name: t, exact: true })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Nothing counted trending yet' })).toBeVisible();
	await page.getByRole('tab', { name: 'Explore', exact: true }).click();
	await expect(page.getByRole('heading', { name: 'No memes yet' })).toBeVisible();
	await expect(page.getByRole('link', { name: 'Create a Meme Tracker' })).toHaveAttribute('href', '/curators/new?template=meme-radar');
});
