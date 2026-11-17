import { test, expect, type APIRequestContext } from '@playwright/test';
import { freshPage } from './helpers';

// Real backend end to end: share to another household profile, then a public link that works signed out and stops
// the moment it is revoked.
const PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';

async function seed(request: APIRequestContext) {
 const profiles = await (await request.get('/api/profiles')).json();
 const owner = { 'X-Profile-Id': String(profiles[0].id) };
 const board = (await (await request.post('/api/hub/boards', { headers: owner, data: { name: 'Share test board', interests: 'red squares', enabled: false } })).json()).board;
 for (const n of [1, 2]) {
  const r = await request.post(`/api/hub/boards/${board.id}/images`, { headers: owner, data: { data_base64: PNG, title: `Red square ${n}`, source_url: `https://example.com/red-${n}.png` } });
  expect(r.ok()).toBeTruthy();
 }
 const ann = await (await request.post('/api/profiles', { data: { name: 'Ann share', emoji: '🦊', color: '#ff8fab' } })).json();
 return { board, owner, ann };
}

async function cleanup(request: APIRequestContext, s: Awaited<ReturnType<typeof seed>>) {
 await request.delete(`/api/hub/boards/${s.board.id}`, { headers: s.owner });
 await request.delete(`/api/profiles/${s.ann.id}`);
}

test('share a board with another profile, who sees a banner and gets a copy', async ({ page, request }) => {
 const s = await seed(request);
 try {
  await freshPage(page, `/boards?board=${s.board.id}`);
  await page.getByRole('toolbar', { name: 'Board actions' }).getByRole('button', { name: 'Share', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Share “Share test board”' });
  await expect(dialog.getByRole('tab', { name: 'Work together' })).toHaveAttribute('aria-selected', 'true');
  await dialog.getByRole('tab', { name: 'Send a copy' }).click();
  await dialog.getByRole('button', { name: 'Send this board to Ann share' }).click();
  await expect(page.getByText('Sent 2 images to Ann share.')).toBeVisible();

  // Single image from the viewer goes to Ann's "Shared with you" board.
  await page.getByRole('button', { name: 'View Red square 1' }).click();
  await page.getByRole('button', { name: 'Share', exact: true }).last().click();
  await page.getByRole('dialog', { name: 'Share image' }).getByRole('button', { name: 'Send this image to Ann share' }).click();
  await expect(page.getByText('Sent the image to Ann share.')).toBeVisible();

  // Ann: banner on the overview, then the copied board opens.
  await page.evaluate((id) => localStorage.setItem('bt.profile', String(id)), s.ann.id);
  await page.goto('/boards');
  const banner = page.getByRole('region', { name: 'Shared with you' });
  await expect(banner).toContainText('shared a board with you');
  await expect(banner).toContainText('shared an image with you');
  await banner.getByRole('button', { name: 'Open' }).first().click();
  await expect(page.getByRole('heading', { name: 'Shared with you', exact: true })).toBeVisible();
  await page.goto('/boards');
  await expect(page.getByRole('region', { name: 'Shared with you' })).toHaveCount(1);
  await page.getByRole('button', { name: 'Dismiss' }).click();
  await page.reload();
  await expect(page.getByRole('region', { name: 'Shared with you' })).toHaveCount(0);
 } finally { await cleanup(request, s); }
});

test('work on a board together: the member sees it, adds to it and can leave', async ({ page, request }) => {
 const s = await seed(request);
 try {
  await freshPage(page, `/boards?board=${s.board.id}`);
  await page.getByRole('toolbar', { name: 'Board actions' }).getByRole('button', { name: 'Share', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Share “Share test board”' });
  await dialog.getByRole('button', { name: 'Work on this board with Ann share' }).click();
  await expect(page.getByText('Ann share can now work on “Share test board” with you.')).toBeVisible();
  await expect(dialog.getByRole('button', { name: 'Remove Ann share from this board' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByText('Shared with Ann share')).toBeVisible();

  // Ann: invitation banner, the same board (not a copy), member-only tools.
  await page.evaluate((id) => localStorage.setItem('bt.profile', String(id)), s.ann.id);
  await page.goto('/boards');
  await expect(page.getByRole('region', { name: 'Shared with you' })).toContainText('invited you to work on a board together');
  await page.getByRole('button', { name: /Open Share test board/ }).click();
  await expect(page.getByText(/Shared by/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'View Red square 1' })).toBeVisible();
  const tools = page.getByRole('toolbar', { name: 'Board actions' });
  await expect(tools.getByRole('button', { name: 'Add image' })).toBeVisible();
  await expect(tools.getByRole('button', { name: 'Reshuffle' })).toHaveCount(0);
  await expect(tools.getByRole('button', { name: /Edit .* curator/ })).toHaveCount(0);
  const added = await request.post(`/api/hub/boards/${s.board.id}/images`, { headers: { 'X-Profile-Id': String(s.ann.id) }, data: { data_base64: PNG, title: 'Ann square', source_url: 'https://example.com/ann.png' } });
  expect(added.ok()).toBeTruthy();
  await page.getByRole('button', { name: 'Leave board' }).click();
  await page.getByRole('dialog', { name: 'Leave this board?' }).getByRole('button', { name: 'Leave board' }).click();
  await expect(page.getByRole('button', { name: /Open Share test board/ })).toHaveCount(0);

  // Owner: Ann's image stayed, and a notice says so.
  await page.evaluate((id) => localStorage.setItem('bt.profile', String(id)), Number(s.owner['X-Profile-Id']));
  await page.goto(`/boards?board=${s.board.id}`);
  await expect(page.getByRole('button', { name: 'View Ann square' })).toBeVisible();
 } finally { await cleanup(request, s); }
});

test('public link works signed out on a phone and stops when revoked', async ({ page, request, browser }) => {
 const s = await seed(request);
 try {
  await freshPage(page, `/boards?board=${s.board.id}`);
  await page.getByRole('toolbar', { name: 'Board actions' }).getByRole('button', { name: 'Share', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Share “Share test board”' });
  await dialog.getByRole('tab', { name: 'Get link' }).click();
  await dialog.getByLabel('Link expires').selectOption('30');
  await dialog.getByRole('button', { name: 'Create link' }).click();
  const url = await dialog.getByLabel('Shareable link').inputValue();
  expect(url).toMatch(/\/s\/wcs_[A-Za-z0-9_-]{43}$/);
  await expect(dialog.getByRole('list', { name: 'Public links' })).toContainText('Share test board');

  // A stranger: fresh context, no cookie, no profile, phone viewport.
  const stranger = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const pub = await stranger.newPage();
  const response = await pub.goto(url);
  expect(response?.headers()['x-robots-tag']).toContain('noindex');
  await expect(pub.getByRole('heading', { name: 'Share test board' })).toBeVisible();
  const images = pub.getByRole('list', { name: 'Images' }).locator('img');
  await expect(images).toHaveCount(2);
  await expect.poll(() => images.first().evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0);
  await expect(pub.getByText('Original source')).toHaveCount(0);  // not opted in
  await expect(pub.locator('nav, aside')).toHaveCount(0);  // no app chrome
  const scroll = await pub.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(scroll).toBeLessThanOrEqual(0);
  await pub.getByRole('button', { name: 'Open Red square 1' }).click();
  await expect(pub.getByRole('dialog', { name: 'Red square 1' })).toBeVisible();

  // Revoke in the owner's dialog: the public page is gone on the next load.
  await dialog.getByRole('button', { name: 'Done' }).click();
  await dialog.getByRole('button', { name: /^Revoke link ending/ }).click();
  await expect(page.getByText('Link revoked.')).toBeVisible();
  await pub.reload();
  await expect(pub.getByRole('heading', { name: 'This link is unavailable' })).toBeVisible();
  await stranger.close();
 } finally { await cleanup(request, s); }
});

test('the header lists every public link for revoking', async ({ page, request }) => {
 const s = await seed(request);
 try {
  const pins = (await (await request.get(`/api/hub/boards/${s.board.id}`, { headers: s.owner })).json()).pins;
  await request.post(`/api/hub/mood-share/boards/${s.board.id}/links`, { headers: s.owner, data: { pin_id: pins[0].id } });
  await freshPage(page, '/boards');
  await page.getByRole('button', { name: 'Shared links' }).click();
  const dialog = page.getByRole('dialog', { name: 'Public links' });
  await expect(dialog.getByRole('list', { name: 'Public links' })).toContainText(pins[0].title);
  await dialog.getByRole('button', { name: /^Revoke link ending/ }).click();
  await expect(dialog.getByText('You have no public links.')).toBeVisible();
 } finally { await cleanup(request, s); }
});

test('sharing an image file opens the share sheet straight from the tap', async ({ page, request }) => {
 const s = await seed(request);
 try {
  // Like Safari/Chrome: the share sheet is refused unless it opens right after the tap.
  await page.addInitScript(() => {
   let tapped = 0; const w = window as unknown as { shared: unknown[] };
   w.shared = [];
   addEventListener('click', () => { tapped = performance.now(); }, true);
   Object.defineProperty(navigator, 'canShare', { value: (d: { files?: File[] }) => !!d.files?.length, configurable: true });
   Object.defineProperty(navigator, 'share', { configurable: true, value: async (d: { files?: File[] }) => {
    if (performance.now() - tapped > 150) throw new DOMException('Must be handling a user gesture', 'NotAllowedError');
    w.shared.push({ name: d.files?.[0]?.name, type: d.files?.[0]?.type, size: d.files?.[0]?.size });
   } });
  });
  // A full-size image takes a while to arrive.
  await page.route('**/pins/*/media', async route => { await new Promise(r => setTimeout(r, 600)); await route.continue(); });
  await freshPage(page, `/boards?board=${s.board.id}`);
  await page.getByRole('button', { name: 'View Red square 1', exact: true }).click();
  const viewer = page.getByRole('dialog', { name: 'Red square 1' });
  await viewer.getByRole('button', { name: 'Share', exact: true }).click();
  const button = page.getByRole('button', { name: 'Share file with an app…' });
  await expect(button).toBeVisible();
  await page.waitForTimeout(1500);
  await button.click();
  await expect.poll(() => page.evaluate(() => (window as unknown as { shared: unknown[] }).shared)).toEqual([{ name: 'Red-square-1.png', type: 'image/png', size: expect.any(Number) }]);
  await expect(page.getByText('Could not share this image.')).toHaveCount(0);
 } finally { await cleanup(request, s); }
});

test('a kid profile is a share target and held-back images are reported', async ({ page, request }) => {
 const s = await seed(request);
 try {
  await page.route('**/api/hub/mood-share/targets', route => route.fulfill({ json: { profiles: [{ id: 999, name: 'Kiddo', emoji: '🐣', color: '#ffd166', child: true }] } }));
  await page.route(`**/api/hub/mood-share/boards/${s.board.id}/to-profile`, route => route.fulfill({ json: { ok: true, added: 1, recipient: 'Kiddo', held: 1 } }));
  await freshPage(page, `/boards?board=${s.board.id}`);
  await page.getByRole('toolbar', { name: 'Board actions' }).getByRole('button', { name: 'Share', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Share “Share test board”' });
  await expect(dialog.getByText('Kid · child limits')).toBeVisible();
  await dialog.getByRole('tab', { name: 'Send a copy' }).click();
  await expect(dialog.getByText('Kid · safety-checked')).toBeVisible();
  await expect(dialog.getByText(/pass the kid-safety check first/)).toBeVisible();
  await dialog.getByRole('button', { name: 'Send this board to Kiddo' }).click();
  await expect(page.getByText('Sent 1 image to Kiddo. 1 image was held back by the kid-safety check.')).toBeVisible();
 } finally { await cleanup(request, s); }
});
