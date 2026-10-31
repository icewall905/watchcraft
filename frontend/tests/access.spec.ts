import { test, expect } from '@playwright/test';
import { resetBackend } from './helpers';

test('child access, administrator PIN and shared settings are enforced in the UI', async ({ page, request }) => {
	await resetBackend(request);
	const admin = (await (await request.get('/api/profiles')).json())[0];
	let childId: number | null = null;
	try {
		const protectedAdmin = await request.patch(`/api/profiles/${admin.id}`, { data: { pin: '4826' } });
		expect(protectedAdmin.ok()).toBe(true);
		await page.addInitScript((id) => { if (!localStorage.getItem('bt.profile')) localStorage.setItem('bt.profile', String(id)); }, admin.id);
		await page.goto('/settings?tab=profiles');
		await page.getByRole('dialog', { name: "Who's watching?", exact: true }).getByRole('button', { name: new RegExp(admin.name) }).click();
		await page.getByLabel('Profile PIN', { exact: true }).fill('4826');
		await page.getByRole('button', { name: 'Unlock profile', exact: true }).click();
		await expect(page.getByRole('dialog', { name: "Who's watching?", exact: true })).toHaveCount(0);
		await expect(page.getByRole('heading', { name: 'Profiles', exact: true })).toBeVisible();
		await page.locator('main').getByRole('button', { name: 'Add person' }).click();
		await page.getByLabel('Name', { exact: true }).fill('Child access test');
		await page.getByRole('button', { name: /Profile access/ }).click();
		await page.getByRole('option', { name: /Child/ }).click();
		await page.getByRole('button', { name: 'Add profile', exact: true }).click();
		await expect(page.getByRole('dialog', { name: 'New profile', exact: true })).toHaveCount(0);
		const child = (await (await request.get('/api/profiles')).json()).find((p: { name: string }) => p.name === 'Child access test');
		childId = child.id;
		const row = page.locator('.item').filter({ hasText: 'Child access test' });
		await row.getByRole('button', { name: 'Switch to', exact: true }).click();
		await expect(page.getByRole('button', { name: 'Add person' })).toHaveCount(0);
		await page.goto('/discover');
		await expect(page.getByRole('heading', { name: 'Hub is unavailable for this profile' })).toBeVisible();
		await page.screenshot({ path: '/tmp/watchcraft-child-access.png' });
		await expect(page.getByRole('navigation', { name: 'Main', exact: true }).getByRole('link', { name: 'Discover', exact: true })).toHaveCount(0);
		await expect(page.getByRole('navigation', { name: 'Main', exact: true }).getByRole('link', { name: 'Meme Radar' })).toHaveCount(0);
		const denied = await page.request.get('/api/hub/items', { headers: { 'X-Profile-Id': String(child.id) } });
		expect(denied.status()).toBe(403);
		const deniedMemes = await page.request.get('/api/hub/memes/items', { headers: { 'X-Profile-Id': String(child.id) } });
		expect(deniedMemes.status()).toBe(403);
		await page.goto('/settings?tab=ai');
		await expect(page.getByRole('heading', { name: 'Administrator access required' })).toBeVisible();
		await expect(page.getByRole('button', { name: 'Pause AI', exact: true })).toHaveCount(0);
		await page.getByRole('button', { name: 'Switch profile (Child access test)', exact: true }).click();
		await page.getByRole('dialog', { name: "Who's watching?", exact: true }).getByRole('button', { name: new RegExp(admin.name) }).click();
		await page.getByLabel('Profile PIN', { exact: true }).fill('1111');
		await page.getByRole('button', { name: 'Unlock profile', exact: true }).click();
		await expect(page.getByRole('alert')).toHaveText(/Incorrect PIN/);
		await page.getByLabel('Profile PIN', { exact: true }).fill('4826');
		await page.getByRole('button', { name: 'Unlock profile', exact: true }).click();
		await expect(page.getByRole('heading', { name: 'Administrator access required' })).toHaveCount(0);
		await expect(page.getByRole('button', { name: 'Pause AI', exact: true })).toBeVisible();
		await page.screenshot({ path: '/tmp/watchcraft-admin-access.png' });
	} finally {
		await request.post(`/api/profiles/${admin.id}/unlock`, { data: { pin: '4826' } });
		if (childId !== null) await request.delete(`/api/profiles/${childId}`);
		await request.patch(`/api/profiles/${admin.id}`, { data: { pin: '' } });
	}
});
