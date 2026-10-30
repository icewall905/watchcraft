import { test, expect } from '@playwright/test';
import { freshPage } from './helpers';

test('desktop sidebar, device preference, and TV remote drawer', async ({ page }) => {
	await freshPage(page, '/settings?tab=appearance');
	const navigation = page.getByRole('navigation', { name: 'Main', exact: true });
	await expect(navigation).toBeVisible();
	await expect(navigation.getByRole('link')).toHaveCount(6);
	await page.screenshot({ path: '/tmp/wc-sidebar-desktop.png' });
	await expect(navigation.getByRole('link', { name: /Video AI|Agent/ })).toHaveCount(0);
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
	await navigation.getByRole('link', { name: 'Discover' }).click();
	await expect(page).toHaveURL(/\/discover$/);
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
