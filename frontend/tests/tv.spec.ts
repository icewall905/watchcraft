import { test, expect } from '@playwright/test';
import { resetBackend } from './helpers';

// TV mode is the default home experience: a deliberately small drawer, remote-first focus,
// and no desktop chrome. These checks run at projector size with the same autoplay policy
// the kiosk launcher uses (see playwright.config.ts).

async function tvPage(page: import('@playwright/test').Page, url = '/') {
	await page.goto(url);
	await page.evaluate(() => { localStorage.clear(); localStorage.setItem('wc.deviceMode', 'tv'); });
	await page.reload();
	// TV mode is observable through the collapsed sidebar and the TV-only top bar.
	await expect.poll(() => page.evaluate(() =>
		getComputedStyle(document.documentElement).getPropertyValue('--sidebar-width').trim())).toBe('0rem');
}

test.beforeAll(async ({ request }) => {
	await resetBackend(request);
});

test('TV mode hides the desktop sidebar and offers the small drawer', async ({ page }) => {
	await tvPage(page);
	// The desktop sidebar is collapsed to nothing so the TV keeps the whole screen.
	await expect.poll(() => page.evaluate(() =>
		getComputedStyle(document.documentElement).getPropertyValue('--sidebar-width').trim())).toBe('0rem');
	const toggle = page.getByRole('button', { name: 'Open navigation' });
	await expect(toggle).toBeVisible();
	await toggle.click();
	const drawer = page.getByRole('navigation', { name: 'Main', exact: true });
	await expect(drawer).toBeVisible();
	// The drawer is deliberately small: TV Home, video search, subscriptions, playlists, Hub, settings.
	await expect(drawer.getByRole('link', { name: 'TV' })).toBeVisible();
	await expect(drawer.getByRole('link', { name: 'Discover' })).toHaveCount(0);
	await expect(drawer.getByRole('link', { name: 'Hub' })).toHaveAttribute('href', '/today');
	await page.getByRole('button', { name: 'Close menu →' }).click();
	await expect(drawer).toBeHidden();
});

test('TV top bar swaps the command palette for video search', async ({ page }) => {
	await tvPage(page);
	await expect(page.getByRole('link', { name: 'Search videos' })).toBeVisible();
	await expect(page.getByRole('button', { name: 'Search everything' })).toHaveCount(0);
});

test('the TV drawer is reachable and usable with the remote alone', async ({ page }) => {
	await tvPage(page);
	await page.getByRole('button', { name: 'Open navigation' }).click();
	const drawer = page.getByRole('navigation', { name: 'Main', exact: true });
	await expect(drawer).toBeVisible();
	// Arrow keys move focus inside the drawer; Enter follows the focused link.
	await page.keyboard.press('ArrowDown');
	await page.keyboard.press('ArrowDown');
	const focused = await page.evaluate(() => document.activeElement?.textContent?.trim() ?? '');
	expect(focused.length).toBeGreaterThan(0);
	await page.keyboard.press('Enter');
	await expect(page).not.toHaveURL(/^about:blank/);
	await expect(page.locator('main')).toBeVisible();
});

test('switching back to desktop restores the sidebar', async ({ page }) => {
	await tvPage(page);
	await page.evaluate(() => localStorage.setItem('wc.deviceMode', 'desktop'));
	await page.reload();
	await expect.poll(() => page.evaluate(() =>
		getComputedStyle(document.documentElement).getPropertyValue('--sidebar-width').trim())).not.toBe('0rem');
});
