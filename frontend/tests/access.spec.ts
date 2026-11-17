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
		expect(child.controls.finds).toBe(false); // parental controls: children start locked down
		const everything = Object.fromEntries(Object.keys(child.controls).map((k) => [k, true]));
		expect((await page.request.put(`/api/profiles/${child.id}/parental`, { headers: { 'X-Profile-Id': String(admin.id) }, data: { controls: everything } })).ok()).toBe(true);
		const row = page.locator('.item').filter({ hasText: 'Child access test' });
		await row.getByRole('button', { name: 'Switch to', exact: true }).click();
		await expect(page.getByRole('button', { name: 'Add person' })).toHaveCount(0);
		// Children use the Hub under the kid-safe lock: kid-checked items only, adult tools hidden.
		await page.goto('/discover');
		await expect(page.getByRole('heading', { name: 'Hub is unavailable for this profile' })).toHaveCount(0);
		await expect(page.getByRole('note').filter({ hasText: 'Kid-safe' })).toBeVisible();
		await page.screenshot({ path: '/tmp/watchcraft-child-access.png' });
		await expect(page.getByRole('navigation', { name: 'Main', exact: true }).getByRole('link', { name: 'The Finds', exact: true })).toBeVisible();
		const kid = { headers: { 'X-Profile-Id': String(child.id) } };
		expect((await page.request.get('/api/hub/items', kid)).status()).toBe(200);
		expect((await page.request.get('/api/hub/memes/items', kid)).status()).toBe(200);
		expect((await page.request.post('/api/hub/items/import', { ...kid, data: { url: 'https://example.org/' } })).status()).toBe(403);
		expect((await page.request.post('/api/hub/search/web', { ...kid, data: { q: 'anything' } })).status()).toBe(403);
		expect((await page.request.get('/api/music/home', kid)).status()).toBe(403);
		await page.goto('/library');
		await expect(page.getByLabel('URL to import')).toHaveCount(0);
		await page.goto('/find');
		await expect(page.getByRole('heading', { name: 'Search the web' })).toHaveCount(0);
		await page.goto('/music');
		await expect(page.getByRole('heading', { name: 'Not available on kid profiles' })).toBeVisible();
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
