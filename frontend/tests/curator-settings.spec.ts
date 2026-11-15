import { test, expect } from '@playwright/test';
import { freshPage } from './helpers';

test('reader can teach the curator directly and an admin can schedule the nightly selection', async ({ page, request }) => {
	await freshPage(page, '/settings?tab=curator');
	await expect(page.getByRole('heading', { name: 'Curator', exact: true })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'What I know about you', exact: true })).toBeVisible();
	const rule = `Practical local-first guides ${Date.now()}`;
	await page.getByLabel('Rule', { exact: true }).fill(rule);
	await page.getByLabel('Applies to', { exact: true }).selectOption('articles');
	await page.getByRole('button', { name: 'Teach the curator', exact: true }).click();
	const card = page.locator('li.teaching', { hasText: rule });
	await expect(card).toBeVisible();
	await expect(card.getByText('You told me', { exact: true })).toBeVisible();
	await card.getByRole('button', { name: 'Forget this', exact: true }).click();
	await card.getByRole('button', { name: 'Forget', exact: true }).click();
	await expect(card).toHaveCount(0);

	await expect(page.getByRole('heading', { name: 'Nightly selection', exact: true })).toBeVisible();
	const run = page.getByRole('switch', { name: /^Run the master curator/ });
	const before = (await (await request.get('/api/settings')).json()).hub_master;
	await run.click();
	await page.getByLabel('Nightly time', { exact: true }).fill('03:45');
	await page.getByRole('button', { name: 'Save changes', exact: true }).click();
	await expect.poll(async () => (await (await request.get('/api/settings')).json()).hub_master).toEqual({ ...before, enabled: !before.enabled, time: '03:45' });
	await request.put('/api/settings/hub_master', { data: before });
});
