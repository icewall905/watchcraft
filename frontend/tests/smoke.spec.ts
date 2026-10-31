import { test, expect, type Page } from '@playwright/test';
import { expectFocus, focused, freshPage, moveUntil, resetBackend } from './helpers';
import { mkdirSync } from 'node:fs';

// Keyboard-only smoke test: everything below uses arrow keys, Enter, Backspace and the on-screen keyboard.
mkdirSync('screenshots', { recursive: true });

const ROWS = ['1234567890', 'abcdefghij', 'klmnopqrst', "uvwxyz-'.?"];
/** Type with the on-screen keyboard. Assumes focus starts on "a" (OSK autofocus). */
async function oskType(page: Page, text: string) {
	await expect(page.getByRole('dialog', { name: /./ }).locator('.kb')).toBeVisible();
	await expectFocus(page, /^a$/);
	let r = 1;
	let c = 0;
	for (const ch of text) {
		if (ch === ' ') {
			// Space bar sits on the bottom row; reach it then come back up.
			while (r < 4) (await page.keyboard.press('ArrowDown'), r++);
			await moveUntil(page, 'ArrowRight', /^Space$/);
			await moveUntil(page, 'ArrowLeft', /^Space$/);
			await page.keyboard.press('Enter');
			await page.keyboard.press('ArrowUp');
			r = 3;
			c = Math.max(0, ROWS[3].indexOf(((await focused(page)) || 'u')[0]));
			continue;
		}
		const tr = ROWS.findIndex((row) => row.includes(ch));
		const tc = ROWS[tr].indexOf(ch);
		while (r < tr) (await page.keyboard.press('ArrowDown'), r++);
		while (r > tr) (await page.keyboard.press('ArrowUp'), r--);
		while (c < tc) (await page.keyboard.press('ArrowRight'), c++);
		while (c > tc) (await page.keyboard.press('ArrowLeft'), c--);
		expect(await focused(page)).toBe(ch);
		await page.keyboard.press('Enter');
	}
}

// The smoke adds a profile; later specs in the same run expect a single profile (no "Who's watching?" picker).
test.afterAll(async ({ request }) => {
	const profiles = await (await request.get('/api/profiles')).json();
	for (const p of profiles.slice(1)) await request.delete(`/api/profiles/${p.id}`);
});

test('keyboard-only smoke', async ({ page, request }) => {
	await resetBackend(request);
	await freshPage(page);
	await expect(page.locator('.topbar')).toBeVisible();
	await page.waitForTimeout(500);
	// Initial focus lands in the hero.
	await expectFocus(page, /Create a playlist|Watch the AI work|Play/);
	await page.screenshot({ path: 'screenshots/home-1920.png' });

	// --- profile: open the switcher (top right), add a person.
	await page.keyboard.press('ArrowUp');
	await moveUntil(page, 'ArrowRight', /Switch profile/);
	await page.keyboard.press('Enter');
	await expect(page.getByRole('heading', { name: "Who's watching?" })).toBeVisible();
	await moveUntil(page, 'ArrowRight', /Add person/);
	await page.keyboard.press('Enter');
	await expect(page.getByRole('heading', { name: 'New profile' })).toBeVisible();
	await expectFocus(page, /Name/);
	await page.keyboard.press('Enter'); // opens OSK
	await oskType(page, 'maja');
	await page.keyboard.press('Backspace'); // closes OSK (back)
	await expect(page.locator('.osk')).toHaveCount(0);
	await expect(page.getByLabel('Name')).toHaveValue('maja');
	await moveUntil(page, 'ArrowDown', /Cancel|Add profile/, 30);
	await moveUntil(page, 'ArrowRight', /Add profile/);
	await page.keyboard.press('Enter');
	await expect(page.getByRole('button', { name: /maja/ })).toBeVisible();
	await page.screenshot({ path: 'screenshots/profiles-1920.png' });
	await page.keyboard.press('Backspace'); // close picker
	await expect(page.getByRole('heading', { name: "Who's watching?" })).toHaveCount(0);

	// --- category via Settings.
	await moveUntil(page, 'ArrowLeft', /^(TV|Subscriptions|Discover|Settings)(?:\s|$)/);
	await moveUntil(page, 'ArrowDown', /^Settings(?: Settings)?$/);
	await page.keyboard.press('Enter');
	await expect(page).toHaveURL(/\/settings/);
	await page.waitForTimeout(300);
	await moveUntil(page, 'ArrowRight', /New playlist/);
	await page.keyboard.press('Enter');
	await expect(page.getByRole('heading', { name: 'New playlist' })).toBeVisible();
	await page.waitForTimeout(300);
	await moveUntil(page, 'ArrowDown', /^Name/);
	await page.keyboard.press('Enter');
	await oskType(page, 'space kids');
	await page.keyboard.press('Escape');
	await moveUntil(page, 'ArrowDown', /Brief for the AI/);
	await page.keyboard.press('Enter');
	await oskType(page, 'fun rockets');
	await page.keyboard.press('Escape');
	await page.screenshot({ path: 'screenshots/settings-category-form-1920.png', fullPage: false });
	await moveUntil(page, 'ArrowDown', /Cancel|Create playlist/, 60);
	await moveUntil(page, 'ArrowRight', /Create playlist/);
	await page.keyboard.press('Enter');
	await expect(page.getByText(/Created space kids/)).toBeVisible();
	await expect(page.getByText('space kids').first()).toBeVisible();
	await page.screenshot({ path: 'screenshots/settings-categories-1920.png' });

	// --- Agent page, toggle pause.
	await moveUntil(page, 'ArrowLeft', /^(TV|Subscriptions|Discover|News|Knowledge|Library|Briefings|Alerts|Research|Curators|Sources|Video AI|Statistics|Settings)(?:\s|$)/);
	await moveUntil(page, 'ArrowUp', /^TV(?: TV)?$/);
	await moveUntil(page, 'ArrowDown', /^Video AI(?: Video AI)?$/);
	await page.keyboard.press('Enter');
	await expect(page).toHaveURL(/\/agent/);
	await page.waitForTimeout(400);
	await expectFocus(page, /Pause AI/);
	await page.keyboard.press('Enter');
	await expect(page.locator('#main-pause, .acts .btn').first()).toContainText('Resume AI');
	const st = await (await request.get('/api/agent/status')).json();
	expect(st.control.paused).toBe(true);
	await page.waitForTimeout(1500);
	await page.screenshot({ path: 'screenshots/agent-1920.png' });
	await page.keyboard.press('Enter');
	await expect(page.locator('.acts .btn').first()).toContainText('Pause AI');

	// --- Back navigation with Backspace.
	await page.keyboard.press('Backspace');
	await expect(page).toHaveURL(/\/settings/);
	// Settings pushed one extra entry (the category form); Back walks through it, then home.
	for (let i = 0; i < 3 && new URL(page.url()).pathname !== '/'; i++) {
		await page.keyboard.press('Backspace');
		await page.waitForTimeout(300);
	}
	await expect(page).toHaveURL(/:\d+\/$/);
	await page.waitForTimeout(500);
	await page.screenshot({ path: 'screenshots/home-after-1920.png' });
});

test('mobile screenshots', async ({ browser }) => {
	const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true, deviceScaleFactor: 2 });
	const page = await ctx.newPage();
	for (const [name, url] of [
		['home', '/'],
		['settings', '/settings?tab=ai'],
		['agent', '/agent'],
		['search', '/search']
	]) {
		await page.goto(url);
		await page.waitForTimeout(1200);
		await page.screenshot({ path: `screenshots/${name}-390.png` });
	}
	await ctx.close();
});
