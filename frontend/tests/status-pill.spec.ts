import { test, expect } from '@playwright/test';

test('activity preview appears on hover and the button opens the agent page', async ({ page, request }) => {
	// Other specs create profiles; choose one explicitly instead of opening the
	// first-launch picker over the status button this test needs to hover.
	const profiles = await (await request.get('/api/profiles')).json();
	await page.addInitScript(id => localStorage.setItem('bt.profile', String(id)), profiles[0].id);
	await page.goto('/');
	const status = page.getByRole('button', { name: /^AI status:/ });
	const preview = page.locator('.pop').filter({ has: page.getByRole('button', { name: /^(Pause|Resume) AI$/ }) });
	await expect(preview).toBeHidden();
	await status.hover();
	await expect(preview).toBeVisible();
	await expect(page.getByRole('button', { name: 'Open AI activity' })).toHaveCount(0);
	await preview.getByRole('button', { name: /^(Pause|Resume) AI$/ }).hover();
	await expect(preview).toBeVisible();
	await status.click();
	await expect(page).toHaveURL(/\/agent\/?$/);
});
