import { test, expect } from '@playwright/test';

test('a grown-up writes safety rails for one child, not for another', async ({ page, request }) => {
	await request.put('/api/settings/setup', { data: { completed: true } });
	const admin = (await (await request.get('/api/profiles')).json())[0];
	const h = { 'X-Profile-Id': String(admin.id) };
	const hadPin = admin.pin_required;
	if (!hadPin) await request.patch(`/api/profiles/${admin.id}`, { headers: h, data: { pin: '4826' } });
	else await request.post(`/api/profiles/${admin.id}/unlock`, { data: { pin: '4826' } });
	const older = await (await request.post('/api/profiles', { headers: h, data: { name: 'Rails eleven', role: 'child' } })).json();
	const younger = await (await request.post('/api/profiles', { headers: h, data: { name: 'Rails eight', role: 'child' } })).json();
	await page.context().request.post(`/api/profiles/${admin.id}/unlock`, { data: { pin: '4826' } });
	await page.addInitScript(id => localStorage.setItem('bt.profile', String(id)), admin.id);
	await page.goto('/settings?tab=profiles');
	const row = page.locator('.item').filter({ hasText: 'Rails eight' });
	await row.getByRole('button', { name: 'Edit', exact: true }).click();
	await page.getByLabel('Safety rails for the AI (optional)').fill('He is 8. No battles or monsters.');
	await page.screenshot({ path: 'screenshots/profile-safety-rails.png' });
	await page.getByRole('button', { name: 'Save', exact: true }).click();
	await expect(page.getByRole('dialog', { name: /Edit/ })).toHaveCount(0);
	const after = await (await request.get('/api/profiles', { headers: h })).json();
	expect(after.find((p: { id: number }) => p.id === younger.id).safety_rails).toBe('He is 8. No battles or monsters.');
	expect(after.find((p: { id: number }) => p.id === older.id).safety_rails).toBe('');
	// The child cannot set rails.
	const own = await request.put(`/api/profiles/${younger.id}/safety-rails`, { headers: { 'X-Profile-Id': String(younger.id) }, data: { rails: '' } });
	expect(own.status()).toBe(403);
	await request.delete(`/api/profiles/${older.id}`, { headers: h });
	await request.delete(`/api/profiles/${younger.id}`, { headers: h });
	if (!hadPin) await request.patch(`/api/profiles/${admin.id}`, { headers: h, data: { pin: '' } });
});
