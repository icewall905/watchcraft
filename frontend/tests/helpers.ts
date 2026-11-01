import { expect, type APIRequestContext, type Page } from '@playwright/test';

/** Text describing the focused element (aria-label + text + placeholder). */
export const focused = (page: Page) =>
	page.evaluate(() => {
		const a = document.activeElement as HTMLElement | null;
		return a ? `${a.getAttribute('aria-label') ?? ''} ${a.textContent ?? ''} ${(a as HTMLInputElement).placeholder ?? ''}`.replace(/\s+/g, ' ').trim() : '';
	});

export const expectFocus = (page: Page, re: RegExp) => expect.poll(() => focused(page), { timeout: 5000 }).toMatch(re);

export async function moveUntil(page: Page, dir: 'ArrowUp' | 'ArrowDown' | 'ArrowLeft' | 'ArrowRight', match: RegExp, max = 25) {
	for (let i = 0; i < max; i++) {
		if (match.test(await focused(page))) return;
		await page.keyboard.press(dir);
		await page.waitForTimeout(60);
	}
	expect(await focused(page)).toMatch(match);
}

/** Reset the backend to a known state: one profile, no categories, no history, AI resumed. */
export async function resetBackend(request: APIRequestContext) {
	await request.post('/api/agent/resume');
	const profiles = await (await request.get('/api/profiles')).json();
	for (const p of profiles.slice(1)) await request.delete(`/api/profiles/${p.id}`);
	const headers = { 'X-Profile-Id': String(profiles[0].id) };
	for (const c of await (await request.get('/api/categories', { headers })).json()) await request.delete(`/api/categories/${c.id}`, { headers });
	await request.delete('/api/history', { headers });
	for (const l of await (await request.get('/api/lessons', { headers })).json()) await request.delete(`/api/lessons/${l.id}`, { headers });
}

/** Load a URL with fresh preferences and the default profile explicitly selected.
 * Specs share a backend, so another spec may have created additional profiles.
 * Profile-picker/onboarding tests use their own navigation and storage setup.
 */
export async function freshPage(page: Page, url = '/') {
	const profiles = await (await page.request.get('/api/profiles')).json();
	await page.goto(url);
	await page.evaluate((profileId: number) => {
		localStorage.clear();
		localStorage.setItem('bt.profile', String(profileId));
	}, profiles[0].id);
	await page.reload();
}
