import { test, expect } from '@playwright/test';
import { freshPage } from './helpers';

test('scouts shelf is opt-in and shows genuine findings', async ({ page, request }) => {
 const profile = (await (await request.get('/api/profiles')).json())[0];
 await page.route('**/api/hub/team', async route => route.fulfill({ json: { limit: 1, running: 0, waiting: 0, journal: [], agents: [{ key: 'curator-7', kind: 'curator', id: 7, role: 'watch', name: 'Science scout', icon: '🔭', color: '#fff', mission: '', status: 'idle', since: null, next_run: null, finds: [{ id: 42, title: 'A real finding', found_at: Date.now()/1000 }] }] } }));
 await freshPage(page, '/');
 await expect(page.getByRole('heading', { name: 'Your scouts' })).toHaveCount(0);
 await page.goto('/settings?tab=appearance');
 await page.getByRole('button', { name: /Scouts shelf: Off/ }).click();
 await expect.poll(async () => (await (await request.get('/api/profiles')).json())[0].prefs.scouts_home).toBe(true);
 await page.getByRole('button', { name: /Position: Before video rows/ }).click();
 await expect.poll(async () => (await (await request.get('/api/profiles')).json())[0].prefs.scouts_home_position).toBe('bottom');
 await page.goto('/');
 await expect(page.getByRole('heading', { name: 'Your scouts' })).toBeVisible();
 await expect(page.getByRole('link', { name: /A real finding/ })).toHaveAttribute('href', '/item/42');
 await expect(page.getByRole('link', { name: 'View activity' })).toHaveAttribute('href', '/team');
 await request.patch(`/api/profiles/${profile.id}`, { data: { prefs: profile.prefs } });
});
