import { test, expect } from '@playwright/test';
import { freshPage } from './helpers';

async function story(page: import('@playwright/test').Page) {
	await page.route('**/api/hub/stories/51', route => route.fulfill({ json: {
		id: 51, title: 'Shared harbour story', items: [{ id: 51, title: 'Harbour article', publisher: 'Local', provider: 'rss', state: {}, url: 'https://example.test/article' }]
	} }));
	await page.route('**/api/hub/items/51', route => route.fulfill({ json: { id: 51, body: 'Archived harbour article.', enrichment: { summary: 'AI harbour overview.' } } }));
}

test('sharing is explicit, copies a public URL and revokes it', async ({ page, context }) => {
	await context.grantPermissions(['clipboard-read', 'clipboard-write']);
	await story(page);
	let path: string | null = null;
	let creates = 0;
	await page.route('**/api/hub/items/51/share', route => {
		if (route.request().method() === 'POST') { creates++; path = '/share/test-public-token'; }
		if (route.request().method() === 'DELETE') path = null;
		return route.fulfill({ json: { path } });
	});
	await freshPage(page, '/news/51');
	await expect(page.getByRole('button', { name: 'Share article', exact: true })).toBeEnabled();
	expect(creates).toBe(0);
	await page.getByRole('button', { name: 'Share article', exact: true }).click();
	const input = page.getByRole('textbox', { name: 'Public article link' });
	await expect(input).toHaveValue(/\/share\/test-public-token$/);
	await expect(page.getByRole('link', { name: 'Open public link' })).toHaveAttribute('href', /\/share\/test-public-token$/);
	await page.screenshot({ path: '/tmp/watchcraft-share-desktop.png', fullPage: true });
	await page.setViewportSize({ width: 390, height: 844 });
	await page.screenshot({ path: '/tmp/watchcraft-share-mobile.png', fullPage: true });
	await page.getByRole('button', { name: 'Copy link', exact: true }).click();
	await expect(page.getByRole('button', { name: 'Copied', exact: true })).toBeVisible();
	expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(await input.inputValue());
	await page.getByRole('button', { name: 'Revoke link', exact: true }).click();
	await expect(input).toHaveCount(0);
	await expect(page.getByRole('button', { name: 'Share article', exact: true })).toBeEnabled();
});

test('an existing link is shown and a failed revocation retains it', async ({ page }) => {
	await story(page);
	await page.route('**/api/hub/items/51/share', route => route.request().method() === 'DELETE'
		? route.fulfill({ status: 503, json: { detail: 'Please retry later' } })
		: route.fulfill({ json: { path: '/share/existing-token' } }));
	await freshPage(page, '/news/51');
	const input = page.getByRole('textbox', { name: 'Public article link' });
	await expect(input).toHaveValue(/\/share\/existing-token$/);
	await page.getByRole('button', { name: 'Revoke link', exact: true }).click();
	await expect(page.getByRole('alert').filter({ hasText: 'Please retry later' })).toBeVisible();
	await expect(input).toHaveValue(/\/share\/existing-token$/);
});

test('a delayed share creation cannot expose a previous profile link', async ({ page, request }) => {
	const other = await (await request.post('/api/profiles', { data: { name: 'Share other profile' } })).json();
	let release!: () => void;
	const wait = new Promise<void>(resolve => { release = resolve; });
	let started = false;
	await story(page);
	await page.route('**/api/hub/items/51/share', async route => {
		if (route.request().method() === 'POST') {
			started = true;
			await wait;
			await route.fulfill({ json: { path: '/share/previous-profile-token' } }).catch(() => {});
		} else await route.fulfill({ json: { path: null } });
	});
	try {
		await freshPage(page, '/news/51');
		await page.getByRole('button', { name: 'Share article', exact: true }).click();
		await expect.poll(() => started).toBe(true);
		await page.getByRole('button', { name: 'Switch profile' }).click();
		await page.getByRole('button', { name: 'Share other profile', exact: true }).click();
		release();
		await expect(page.getByRole('button', { name: 'Share article', exact: true })).toBeEnabled();
		await expect(page.getByRole('textbox', { name: 'Public article link' })).toHaveCount(0);
	} finally { release(); await request.delete(`/api/profiles/${other.id}`); }
});
