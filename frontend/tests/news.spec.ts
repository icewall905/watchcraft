import { test, expect } from '@playwright/test';
import { freshPage } from './helpers';

test('news is story first and opens the profile story view', async ({ page }) => {
	await page.route('**/api/hub/news*', (route) => route.fulfill({ json: { topics: [], groups: [{
		key: '51', story_id: 51, title: 'Harbour plan advances', latest: 1783000000,
		items: [{ id: 51, title: 'Harbour plan advances', summary: 'Council votes today', provider: 'feed', publisher: 'Local',
			local_image: false, state: {}, url: 'https://example.test/a' },
		{ id: 52, title: 'More on the harbour vote', publisher: 'Daily', provider: 'feed', state: {}, url: 'https://example.test/b' }]
	}] } }));
	await page.route('**/api/hub/stories/51', (route) => route.fulfill({ json: {
		id: 51, title: 'Harbour plan advances', items: [{ id: 51, title: 'Harbour plan advances', summary: 'Council votes today', publisher: 'Local', provider: 'feed', state: {}, url: 'https://example.test/a' }]
	} }));
	await page.route('**/api/hub/items/51', (route) => route.fulfill({ json: {
        id: 51, title: 'Harbour plan advances', body: 'The complete archived council message.',
        enrichment: { summary: 'A quick AI overview.' }, local_assets: ['image'],
        capture: { status: 'complete', summary_status: 'ready', error: '' }
    } }));
    await page.route('**/api/hub/items/51/assets/image', (route) => route.fulfill({
        contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="300" height="1200"><rect width="300" height="1200" fill="red"/></svg>'
    }));
    await freshPage(page, '/news');
	await expect(page.getByText('+1 source', { exact: true })).toBeVisible();
	await page.getByRole('link', { name: 'Harbour plan advances' }).click();
	await expect(page.getByRole('heading', { name: 'Harbour plan advances' })).toBeVisible();
	await expect(page.getByText('1 source', { exact: true })).toBeVisible();
    await expect(page.getByText('The complete archived council message.')).toBeVisible();
    await expect(page.getByText('A quick AI overview.')).toBeVisible();
    await expect(page.getByRole('link', { name: 'Harbour plan advances' })).toHaveAttribute('href', '/item/51');
    const image = page.getByRole('img', { name: 'Harbour plan advances' });
    await expect(image).toBeVisible();
    const size = await image.boundingBox();
    expect(size!.height / size!.width).toBeCloseTo(4, 1);
    await expect(page.getByRole('link', { name: 'Open full-size archived image' })).toHaveAttribute('href', /^blob:/);
    await page.screenshot({ path: '/tmp/watchcraft-news-archive.png', fullPage: true });
});
