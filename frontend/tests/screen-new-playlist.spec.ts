import { test, expect } from '@playwright/test';
import { freshPage, resetBackend } from './helpers';

test('The Screen always offers New playlist once playlists exist', async ({ page, request }) => {
	await resetBackend(request);
	await request.post('/api/categories', { data: { name: 'Woodworking', brief: 'x' } });
	await freshPage(page, '/tv');
	const link = page.getByRole('link', { name: '＋ New playlist' });
	await expect(link).toBeVisible();
	await expect(link).toHaveAttribute('href', '/settings?tab=categories&new=1');
});
