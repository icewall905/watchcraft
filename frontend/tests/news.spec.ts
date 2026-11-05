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
	await page.getByRole('button', { name: '1 more source' }).click();
	await expect(page.getByRole('link', { name: 'Daily: More on the harbour vote' })).toHaveAttribute('href', '/item/52');
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

test('news is a finite weekly edition with 20 stories per page and local source links', async ({ page }) => {
	const groups = Array.from({ length: 100 }, (_, index) => ({
		key: `week-${index + 1}`, title: `Weekly story ${index + 1}`, latest: 1783000000 - index,
		items: [{ id: index + 1, title: `Weekly story ${index + 1}`, summary: `Short overview ${index + 1}`,
			provider: 'feed', publisher: 'Local', local_image: false, state: {}, url: `https://example.test/${index + 1}` }]
	}));
	await page.route('**/api/hub/news*', (route) => route.fulfill({ json: {
		groups, topics: [], supply: { target: 100, window_days: 7, available: 100, shortfall: 0, met: true, pending_review: 0, as_of: 1783000000 },
		article_count: 100, story_count: 100
	} }));
	await freshPage(page, '/news');
	await expect(page.getByRole('tab', { name: 'This week' })).toHaveAttribute('aria-selected', 'true');
	await expect(page.getByText('100 articles across 100 stories')).toBeVisible();
	await expect(page.getByText('100 approved this week · 100+ goal met')).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Weekly story 1', exact: true })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Weekly story 20', exact: true })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Weekly story 21', exact: true })).toHaveCount(0);
	await expect(page.getByText('Stories 1–20 of 100').first()).toBeVisible();
	await page.screenshot({ path: '/tmp/watchcraft-news-week-1280.png' });
	await page.getByRole('button', { name: 'Next' }).first().click();
	await expect(page.getByText('Page 2 of 5').first()).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Weekly story 21', exact: true })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Weekly story 40', exact: true })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Weekly story 41', exact: true })).toHaveCount(0);
	await page.getByRole('button', { name: 'Next' }).first().click();
	await page.getByRole('button', { name: 'Next' }).first().click();
	await page.getByRole('button', { name: 'Next' }).first().click();
	await expect(page.getByText('Page 5 of 5').first()).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Weekly story 100', exact: true })).toBeVisible();
	await expect(page.getByRole('button', { name: 'Next' }).first()).toBeDisabled();
	await page.getByRole('searchbox', { name: 'Filter news' }).fill('Weekly story');
	await expect(page.getByText('Page 1 of 5').first()).toBeVisible();
	await page.setViewportSize({ width: 390, height: 844 });
	await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
	await page.screenshot({ path: '/tmp/watchcraft-news-week-390.png' });
});
