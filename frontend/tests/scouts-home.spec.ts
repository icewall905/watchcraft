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

test('a queued master curator links to Today, not a curator page', async ({ page, request }) => {
 const profile = (await (await request.get('/api/profiles')).json())[0];
 await request.patch(`/api/profiles/${profile.id}`, { data: { prefs: { ...profile.prefs, scouts_home: true } } });
 const curatorPages: string[] = [];
 page.on('request', r => { if (/\/api\/hub\/curators\/[^/?]+$/.test(r.url())) curatorPages.push(r.url()); });
 await page.route('**/api/hub/team', async route => route.fulfill({ json: { limit: 1, running: 0, waiting: 1, journal: [], agents: [{ key: 'master-1', kind: 'master', id: null, name: 'Master Curator', icon: '✧', color: '#fff', mission: 'Selects your daily edition.', status: 'waiting', since: null, next_run: Date.now()/1000 + 300 }] } }));
 await freshPage(page, '/');
 const card = page.getByRole('link', { name: /Master Curator/ });
 await expect(card).toHaveAttribute('href', '/today');
 await expect(card).toContainText('Selection queued');
 expect(curatorPages).toEqual([]);
 await request.patch(`/api/profiles/${profile.id}`, { data: { prefs: profile.prefs } });
});

test('Today preview respects the profile video-row position', async ({ page, request }) => {
 const profile = (await (await request.get('/api/profiles')).json())[0];
 await page.route('**/api/hub/today', route => route.fulfill({ json: { day: '2026-11-03', curated: true, sections: [], intro: '', intro_refs: [], set_aside: 0 } }));
 await page.route('**/api/feed/home**', route => route.fulfill({ json: [{ kind: 'category', category_id: 777, title: 'Placement video row', emoji: '▶', color: '#aaa', items: [] }] }));
 try {
  for (const position of ['top', 'bottom']) {
   await request.patch(`/api/profiles/${profile.id}`, { data: { prefs: { ...profile.prefs, scouts_home_position: position } } });
   await freshPage(page, '/');
   await expect(page.getByRole('link', { name: 'Open Today', exact: true })).toBeVisible();
   await expect(page.getByRole('heading', { name: 'Placement video row' })).toBeVisible();
   const before = await page.locator('.today-card').evaluate(el => {
    const heading = [...document.querySelectorAll('h2')].find(h => h.textContent?.includes('Placement video row'))!;
    return !!(el.compareDocumentPosition(heading) & Node.DOCUMENT_POSITION_FOLLOWING);
   });
   expect(before).toBe(position === 'top');
  }
 } finally { await request.patch(`/api/profiles/${profile.id}`, { data: { prefs: profile.prefs } }); }
});
