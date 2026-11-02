import { test, expect } from '@playwright/test';
import { freshPage } from './helpers';

// Search is per profile. When nothing matches, say so and offer the live video search
// instead of leaving the person wondering whether search is broken.
test('an empty search names the scope and offers the live video search', async ({ page }) => {
	await page.route('**/api/hub/search*', (route) => route.fulfill({
		json: { items: [], total: 0, videos: [], curators: [], collections: [], topics: [] }
	}));
	await freshPage(page, '/find?q=octopus');
	await expect(page.getByRole('heading', { name: 'Nothing in this profile matches "octopus"' })).toBeVisible();
	await expect(page.getByText(/household's video library/)).toBeVisible();
	const youtube = page.getByRole('link', { name: 'Search YouTube' });
	await expect(youtube).toHaveAttribute('href', '/search?q=octopus');
});

test('the palette offers the live video search for a typed query', async ({ page }) => {
	await freshPage(page, '/discover');
	await page.getByRole('button', { name: 'Search everything' }).click();
	const dialog = page.getByRole('dialog', { name: 'Search everything' });
	await expect(dialog).toBeVisible();
	await dialog.getByRole('combobox').fill('octopus');
	const option = dialog.getByRole('option').first();
	await expect(option).toContainText('Search YouTube for “octopus”');
	await option.getByRole('button').click();
	await expect(page).toHaveURL(/\/search\?q=octopus/);
});
