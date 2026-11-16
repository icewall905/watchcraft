import { test, expect } from '@playwright/test';
import { freshPage } from './helpers';

test('a browser opens on its chosen landing page, and The Screen stays reachable', async ({ page }) => {
	await freshPage(page, '/settings?tab=appearance');
	const select = page.getByRole('combobox', { name: 'Open WatchCraft on' });
	await expect(select).toHaveValue('/');
	await select.selectOption({ label: 'The Wire' });
	expect(await page.evaluate(() => localStorage.getItem('wc.landing'))).toBe('/news');
	await page.goto('/');
	await expect(page).toHaveURL(/\/news$/);
	await expect(page.getByRole('heading', { name: 'The Wire', level: 1 })).toBeVisible();
	// Only the first load lands; choosing The Screen afterwards stays there.
	await page.getByRole('navigation', { name: 'Main', exact: true }).getByRole('link', { name: 'The Screen' }).click();
	await expect(page).toHaveURL(/\/tv$/);
	// Unknown saved values fall back to The Screen.
	await page.evaluate(() => localStorage.setItem('wc.landing', 'https://example.org'));
	await page.goto('/');
	await expect(page).toHaveURL(/\/$/);
});

test('inside the Android app the web choice is not applied', async ({ browser }) => {
	const context = await browser.newContext({ userAgent: 'Mozilla/5.0 (Linux; Android 14) WatchCraftAndroid/0.1.12' });
	const page = await context.newPage();
	await freshPage(page, '/');
	await page.evaluate(() => localStorage.setItem('wc.landing', '/news'));
	await page.goto('/');
	await expect(page).toHaveURL(/\/$/);
	await page.goto('/settings?tab=appearance');
	await expect(page.getByText('The Android app picks its own start page')).toBeVisible();
	await expect(page.getByRole('combobox', { name: 'Open WatchCraft on' })).toHaveCount(0);
	await context.close();
});
