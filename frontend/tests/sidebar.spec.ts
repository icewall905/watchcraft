import { test, expect } from '@playwright/test';
import { freshPage } from './helpers';

test('desktop sidebar, device preference, and TV remote drawer', async ({ page }) => {
	await freshPage(page, '/settings?tab=appearance');
	const navigation = page.getByRole('navigation', { name: 'Main', exact: true });
	await expect(navigation).toBeVisible();
	// Workshop (curators, sources, …) starts collapsed unless it holds the current page.
	await expect(navigation.getByRole('link', { name: 'Curators', exact: true })).toHaveCount(0);
	await navigation.getByRole('button', { name: 'Workshop', exact: true }).click();
	for (const name of ['TV', 'Today', 'Discover', 'News', 'Knowledge', 'Library', 'Briefings', 'Alerts', 'Research', 'Curators', 'Sources'])
		await expect(navigation.getByRole('link', { name, exact: true })).toBeVisible();
	const collapse = page.getByRole('button', { name: 'Collapse sidebar', exact: true });
	await expect(collapse).toHaveText('<<');
	await expect(collapse).toBeInViewport();
	await collapse.click();
	const expand = page.getByRole('button', { name: 'Expand sidebar', exact: true });
	await expect(expand).toHaveText('>>');
	await expect(page.locator('aside.desktop')).toHaveClass(/compact/);
	await expect(navigation.getByRole('link', { name: 'News', exact: true })).toBeVisible();
	await expect(navigation.locator('.lbl')).toHaveCount(0);
	await page.screenshot({ path: '/tmp/wc-sidebar-compact.png' });
	await page.reload();
	await expect(page.locator('aside.desktop')).toHaveClass(/compact/);
	await page.getByRole('button', { name: 'Expand sidebar', exact: true }).click();
	await expect(page.locator('aside.desktop')).not.toHaveClass(/compact/);
	await page.screenshot({ path: '/tmp/wc-sidebar-desktop.png' });
	await page.getByRole('button', { name: /^TV mode/ }).click();
	await expect(navigation).toHaveCount(0);
	await page.reload();
	await expect(page.getByRole('button', { name: /^TV mode/ })).toHaveAttribute('aria-pressed', 'true');
	await expect(navigation).toHaveCount(0);
	await page.getByRole('button', { name: /^TV mode/ }).focus();
	await page.keyboard.press('ArrowLeft');
	await expect(page.getByRole('button', { name: /^Desktop mode/ })).toBeFocused();
	await expect(navigation).toHaveCount(0);
	// Use the explicit edge control as a deterministic left-edge focus target.
	const toggle = page.getByRole('button', { name: 'Open navigation' });
	await toggle.focus();
	await page.keyboard.press('ArrowLeft');
	await expect(navigation).toBeVisible();
	await expect(navigation.getByRole('link', { name: 'Settings' })).toBeFocused();
	await page.screenshot({ path: '/tmp/wc-sidebar-tv.png' });
	await page.keyboard.press('ArrowRight');
	await expect(navigation).toHaveCount(0);
	await expect(toggle).toBeFocused();
	await page.keyboard.press('ArrowLeft');
	await expect(navigation).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(navigation).toHaveCount(0);
	await expect(toggle).toHaveCSS('position', 'fixed');
	await toggle.click();
	// The TV drawer stays minimal; Today is the landing point for curated reading.
	await expect(navigation.getByRole('link', { name: 'News' })).toHaveCount(0);
	await navigation.getByRole('link', { name: 'Hub' }).click();
	await expect(page).toHaveURL(/\/today$/);
	await expect(navigation).toHaveCount(0);
	await page.goto('/settings?tab=appearance');
	await page.getByRole('button', { name: /^Desktop mode/ }).click();
	await expect(navigation).toBeVisible();
	await page.reload();
	await expect(navigation).toBeVisible();
	await page.getByRole('button', { name: /^AI status:/ }).click();
	await expect(page).toHaveURL(/\/agent$/);
});

test('TV sidebar does not intercept modal or player navigation', async ({ page }) => {
	await page.addInitScript(() => { localStorage.setItem('wc.deviceMode', 'tv'); localStorage.setItem('bt.profile', '1'); });
	await page.goto('/');
	await page.getByRole('button', { name: /^Switch profile/ }).click();
	await page.keyboard.press('ArrowLeft');
	await expect(page.getByRole('navigation', { name: 'Main', exact: true })).toHaveCount(0);
	await page.keyboard.press('Escape');
	await page.goto('/watch/sidebar-test');
	await page.keyboard.press('ArrowLeft');
	await expect(page.getByRole('button', { name: 'Open navigation' })).toHaveCount(0);
});
