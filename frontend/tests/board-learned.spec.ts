import { test, expect, type Page } from '@playwright/test';
import { freshPage } from './helpers';

const st = (sid: string, text: string, scope: 'board' | 'all', polarity = 1) => ({sid, text, polarity, confidence: 'established', locked: false, scope, updated: 1});
function learned(over: Record<string, unknown> = {}) {
 return {
  board: {id: 1, name: 'Whimsy', interests: 'Whimsical clothes, accessories and jewellery', instructions: 'Playful, colourful', avoid: '', rules: ''},
  statements: [st('a', 'Playful enamel brooches', 'board'), st('b', 'Crochet nursery blankets', 'board'), st('c', 'Soft pastel palettes', 'all')],
  brief: {text: 'SUBJECT: whimsical clothes.\nMORE OF: playful prints.', state: 'stale', compiled_at: 1700000000, edited_at: 0, error: '', absorbed: 0, dropped: [{sid: 'b', text: 'Crochet nursery blankets', why: 'Nursery decor is not clothing or jewellery.'}]},
  pending: [{id: 7, text: 'Hand-made baby mobiles', polarity: 1, reason: 'You marked two mobiles Fits.', created_at: 1}],
  reviews: [{pin_id: 3, verdict: 'good', note: '', title: 'Mobile', picture: 'A crochet baby mobile', created_at: 1, thumb_url: null}],
  review_counts: {good: 1, bad: 0}, hosts: [], prompt: 'Playful, colourful\n\nBOARD BRIEF …', chat: [], ...over,
 };
}

async function setup(page: Page) {
 let state: any = learned();
 const writes: {method: string; path: string; body: any}[] = [];
 await page.route('**/api/hub/boards/**', async route => {
  const req = route.request(), path = new URL(req.url()).pathname, method = req.method();
  const body = method === 'GET' ? {} : req.postDataJSON() ?? {};
  if (method !== 'GET') writes.push({method, path, body});
  if (path === '/api/hub/boards/learned') return route.fulfill({json: {boards: [{id: 1, name: 'Whimsy', interests: '', statements: 2, brief: 'stale', flagged: 1, fits: 1, not_fits: 0}, {id: 2, name: 'Nursery', interests: '', statements: 0, brief: 'none', flagged: 0, fits: 0, not_fits: 0}], shared: [state.statements[2]]}});
  if (path.endsWith('/learned/refine')) { state = {...state, statements: state.statements.filter((x: any) => x.scope === 'all'), brief: {...state.brief, state: 'fresh', text: 'SUBJECT: whimsical wearables.', absorbed: 1}}; return route.fulfill({json: state}); }
  if (path.includes('/learned/statements/')) { const sid = path.split('/').at(-1); state = {...state, statements: state.statements.filter((x: any) => x.sid !== sid), brief: {...state.brief, dropped: []}}; return route.fulfill({json: state}); }
  if (path.endsWith('/learned/chat')) { state = {...state, chat: [{id: 1, role: 'user', text: body.text, proposal: {}, proposal_status: 'none', created_at: 1}, {id: 2, role: 'curator', text: 'I will stop.', proposal: {ops: [{op: 'add', sid: null, text: 'No nursery items', polarity: -1}]}, proposal_status: 'pending', created_at: 2}]}; return route.fulfill({json: {...state, messages: state.chat}}); }
  if (path.endsWith('/chat/2/accept')) { state = {...state, statements: [...state.statements, st('d', 'No nursery items', 'board', -1)], chat: state.chat.map((m: any) => m.id === 2 ? {...m, proposal_status: 'accepted'} : m)}; return route.fulfill({json: state}); }
  if (path.match(/\/learned$/)) return route.fulfill({json: path.includes('/2/') ? learned({board: {...state.board, id: 2, name: 'Nursery'}, statements: [], brief: {text: '', state: 'none', compiled_at: 0, edited_at: 0, error: '', absorbed: 0, dropped: []}, pending: [], reviews: []}) : state});
  return route.fulfill({json: {}});
 });
 return writes;
}

test('board learned page shows, flags and fixes what the curator learned', async ({page}) => {
 const writes = await setup(page);
 await freshPage(page, '/boards/learned?board=1');
 await expect(page.getByRole('heading', {name: 'What your board curators learned'})).toBeVisible();
 await expect(page.getByText('Newer learnings to fold in')).toBeVisible();
 await expect(page.getByRole('heading', {name: 'Possible mistakes'})).toBeVisible();
 await expect(page.getByText('Also applied from all your boards')).toBeVisible();
 await page.locator('.flags').getByRole('button', {name: 'Remove it'}).click();
 await expect(page.getByRole('heading', {name: 'Possible mistakes'})).toBeHidden();
 expect(writes.at(-1)).toMatchObject({method: 'PATCH', path: '/api/hub/boards/1/learned/statements/b', body: {remove: true}});

 await page.getByRole('button', {name: 'Refine master prompt now'}).click();
 await expect(page.getByText('SUBJECT: whimsical wearables.')).toBeVisible();
 await expect(page.getByText('Up to date')).toBeVisible();
 await expect(page.getByText('1 learned item is now part of it and left the list.')).toBeVisible();
 await expect(page.locator('.st-text', {hasText: 'Playful enamel brooches'})).toBeHidden();
 await expect(page.getByText('Everything learned so far is part of the brief above')).toBeVisible();

 await page.getByLabel("Message to this board's curator").fill('Stop finding nursery things');
 await page.getByRole('button', {name: 'Send'}).click();
 await expect(page.getByText('+ less of: No nursery items')).toBeVisible();
 await page.getByRole('button', {name: 'Apply'}).click();
 await expect(page.getByText('Applied')).toBeVisible();
 await expect(page.locator('.st-text', {hasText: 'No nursery items'})).toBeVisible();

 for (const width of [1280, 390]) {
  await page.setViewportSize({width, height: 900});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
 }
 await page.getByRole('button', {name: /Nursery/}).click();
 await expect(page.getByText('No brief yet.')).toBeVisible();
});
