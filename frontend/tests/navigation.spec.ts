import { test, expect } from '@playwright/test';
import { freshPage } from './helpers';

test('desktop sidebar: sections, links, active state, compact toggle', async ({ page }) => {
	await freshPage(page, '/stats');
	const nav = page.getByRole('navigation', { name: 'Main', exact: true });
	for (const h of ['Watch', 'Explore', 'Research', 'Automation']) await expect(nav.getByRole('button', { name: h, exact: true })).toBeVisible();
	await expect(nav.getByRole('link', { name: 'News' })).toHaveAttribute('href', '/news');
	await expect(nav.getByRole('link', { name: 'Research' })).toHaveAttribute('href', '/research');
	await expect(nav.getByRole('link', { name: 'Statistics' })).toHaveAttribute('aria-current', 'page');
	await expect(nav.getByRole('link', { name: 'TV' })).not.toHaveAttribute('aria-current', 'page');
	await expect(page.getByText('Watching the Internet for you.')).toBeVisible();
	await nav.getByRole('button', { name: 'Explore', exact: true }).click();
	await expect(nav.getByRole('link', { name: 'News' })).toHaveCount(0);
	await page.getByRole('button', { name: 'Collapse sidebar' }).click();
	await expect(page.getByText('Watching the Internet for you.')).toHaveCount(0);
	await page.reload();
	await expect(page.getByRole('button', { name: 'Expand sidebar' })).toBeVisible();
});

test('TV is active on / and /tv', async ({ page }) => {
	for (const url of ['/', '/tv']) {
		await freshPage(page, url);
		await expect(page.getByRole('navigation', { name: 'Main', exact: true }).getByRole('link', { name: 'TV' })).toHaveAttribute('aria-current', 'page');
	}
});

test('tv mode drawer lists only TV entries; /tv?mode=tv switches mode', async ({ page }) => {
	await page.goto('/tv?mode=tv');
	await expect(page.getByRole('button', { name: 'Open navigation' })).toBeVisible();
	await page.getByRole('button', { name: 'Open navigation' }).click();
	const nav = page.getByRole('navigation', { name: 'Main', exact: true });
	await expect(nav.getByRole('link')).toHaveText(['TV Home', 'Video search', 'Subscriptions', 'Playlists', 'Hub', 'Settings']);
	await expect(nav.getByRole('link', { name: 'News' })).toHaveCount(0);
});

test('mobile viewport shows bottom bar and More sheet', async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 800 });
	await freshPage(page, '/');
	const bar = page.getByRole('navigation', { name: 'Primary' });
	await expect(bar).toBeVisible();
	await expect(bar.getByRole('link')).toHaveText(['TV', 'Discover', 'Library', 'Alerts']);
	await expect(page.getByRole('navigation', { name: 'Main', exact: true })).toHaveCount(0);
	await bar.getByRole('button', { name: 'More' }).click();
	const sheet = page.getByRole('dialog', { name: 'More navigation' });
	await expect(sheet.getByRole('link', { name: 'News' })).toBeVisible();
	await expect(sheet.getByRole('link', { name: 'Settings' })).toBeVisible();
	await expect(sheet.getByRole('link', { name: 'Find' })).toBeVisible();
});

test('Ctrl+K opens palette, Enter searches, Esc closes', async ({ page }) => {
	await freshPage(page, '/stats');
	await expect(page.getByRole('button', { name: 'Search everything' })).toBeVisible(); // hydrated
	await page.keyboard.press('Control+k');
	const dlg = page.getByRole('dialog', { name: 'Search everything' });
	await expect(dlg).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(dlg).toHaveCount(0);
	await page.getByRole('button', { name: 'Search everything' }).click();
	await page.keyboard.type('rust');
	await page.keyboard.press('Enter');
	await expect(page).toHaveURL(/\/find\?q=rust$/);
});
