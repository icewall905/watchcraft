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
	await freshPage(page, '/news');
	await expect(page.getByText('+1 source', { exact: true })).toBeVisible();
	await page.getByRole('link', { name: 'Harbour plan advances' }).click();
	await expect(page.getByRole('heading', { name: 'Harbour plan advances' })).toBeVisible();
	await expect(page.getByText('1 source', { exact: true })).toBeVisible();
});
