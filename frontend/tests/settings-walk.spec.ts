import { test, expect } from '@playwright/test';
import { freshPage, resetBackend } from './helpers';

// Every control on every Settings tab must be reachable with the arrow keys alone.
const TABS = ['categories', 'memory', 'ai', 'player', 'appearance', 'storage', 'sponsorblock', 'extras', 'cookies', 'profiles', 'screens', 'keys'];
const DIRS = ['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'] as const;
let screen: { id: string } | null = null;

test.beforeAll(async ({ request }) => {
	await resetBackend(request);
	// Give the categories tab some content to walk through.
	await request.post('/api/categories', { data: { name: 'Walk test', brief: 'test' } });
	await request.post('/api/lessons', { data: { text: 'Avoid shouting' } });
	// The screens tab needs a registered screen to walk through.
	screen = await (await request.post('/api/playback/targets', { data: { name: 'Walk screen' } })).json();
});

test.afterAll(async ({ request }) => {
	if (screen?.id) await request.delete(`/api/playback/targets/${screen.id}/registration`, { headers: { 'X-Profile-Id': '1' } });
});

for (const tab of TABS) {
	test(`settings tab "${tab}" is fully reachable by arrow keys`, async ({ page }) => {
		await freshPage(page, `/settings?tab=${tab}`);
		await page.waitForTimeout(1200);
		// Tag every usable focusable on the page except the top bar.
		const total = await page.evaluate(() => {
			let i = 0;
			for (const el of document.querySelectorAll<HTMLElement>('main .focusable')) {
				const r = el.getBoundingClientRect();
				if (r.width < 1 || (el as HTMLButtonElement).disabled || el.closest('[inert],[hidden],[data-nav-off]')) continue;
				el.dataset.walk = String(i++);
			}
			return i;
		});
		expect(total).toBeGreaterThan(1);
		const start = await page.evaluate(() => (document.activeElement as HTMLElement)?.dataset.walk ?? '0');
		const seen = new Set<string>([start]);
		const queue = [start];
		while (queue.length) {
			const id = queue.shift()!;
			for (const dir of DIRS) {
				await page.evaluate((i) => document.querySelector<HTMLElement>(`[data-walk="${i}"]`)?.focus(), id);
				await page.keyboard.press(dir);
				const next = await page.evaluate(() => (document.activeElement as HTMLElement)?.dataset.walk);
				if (next !== undefined && !seen.has(next)) {
					seen.add(next);
					queue.push(next);
				}
			}
		}
		const missing = await page.evaluate(
			(ids) =>
				[...document.querySelectorAll<HTMLElement>('[data-walk]')]
					.filter((el) => !ids.includes(el.dataset.walk!))
					.map((el) => (el.getAttribute('aria-label') ?? el.textContent ?? '').trim().slice(0, 40)),
			[...seen]
		);
		expect(missing, `unreachable on ${tab}`).toEqual([]);
	});
}

test('new category form focuses the Name field', async ({ page }) => {
	await freshPage(page, '/settings?tab=categories&new=1');
	await expect.poll(() => page.evaluate(() => document.activeElement?.getAttribute('aria-label')), { timeout: 5000 }).toBe('Name');
});

test('settings: Down at the end of content stays in content; focusing a side tab does not switch panes', async ({ page }) => {
	await freshPage(page, '/settings?tab=extras');
	await page.waitForTimeout(800);
	const last = page.locator('main [data-nav-zone="content"] .focusable:not([disabled])').last();
	await last.focus();
	await page.keyboard.press('ArrowDown');
	expect(await page.evaluate(() => !!document.activeElement?.closest('[data-nav-zone="content"]'))).toBe(true);
	// Move into the side menu and down through it: the pane only changes on Enter.
	await page.keyboard.press('ArrowLeft');
	expect(await page.evaluate(() => !!document.activeElement?.closest('[data-nav-zone="side"]'))).toBe(true);
	await page.keyboard.press('ArrowDown');
	await page.waitForTimeout(300);
	await expect(page).toHaveURL(/tab=extras/);
	await page.keyboard.press('Enter');
	await expect(page).not.toHaveURL(/tab=extras/);
});
