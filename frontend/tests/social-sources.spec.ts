import { test, expect } from '@playwright/test';
import { freshPage } from './helpers';

test('Lemmy source settings save ranking and moderation choices', async ({ page, request }) => {
	const source = await (await request.post('/api/hub/sources', { data: { provider: 'lemmy', url: '!selfhosted@lemmy.world', name: 'Selfhosted community' } })).json();
	let saved: unknown;
	await page.route(`**/api/hub/sources/${source.id}/watcher`, async route => {
		if (route.request().method() === 'PUT') {
			saved = route.request().postDataJSON().config;
			await route.fulfill({ json: { saved: true, test: { ok: true, count: 1, sample: [], error: '' }, source: { ...source, config: saved } } });
		} else await route.fulfill({ json: { config: saved ?? {}, fields: ['sort', 'item_type', 'limit', 'allow_rss'], revisions: [] } });
	});
	await freshPage(page, '/sources');
	await expect(page.getByText('Lemmy communities', { exact: true }).first()).toBeVisible();
	const settings = page.getByRole('button', { name: 'Collection settings', exact: true });
	await settings.click();
	await expect(page.getByRole('switch', { name: /^Allow RSS fallback/ })).not.toBeChecked();
	await page.getByRole('textbox', { name: 'Community ranking' }).fill('TopDay');
	await page.getByRole('textbox', { name: 'Content type', exact: true }).fill('meme');
	await page.getByRole('spinbutton', { name: 'Posts per check' }).fill('12');
	await page.getByRole('button', { name: 'Save', exact: true }).click();
	await expect.poll(() => saved).toEqual({ sort: 'TopDay', item_type: 'meme', limit: 12 });
	await expect(page.getByText('Collection settings', { exact: true }).last()).toBeVisible();
	await page.getByRole('textbox', { name: 'Community ranking' }).scrollIntoViewIfNeeded();
	await page.screenshot({ path: '/tmp/watchcraft-social-settings.png' });
});
