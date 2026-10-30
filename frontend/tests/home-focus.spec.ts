import { test, expect } from '@playwright/test';
import { expectFocus, focused, freshPage, resetBackend } from './helpers';

// Once the user presses a key, the engine must never move focus on its own (late data, restores, …).
test('home: focus stays where the user put it', async ({ page, request }) => {
	await resetBackend(request);
	const cat = await (await request.post('/api/categories', { data: { name: 'Focus test', brief: 'x' } })).json();
	for (const id of ['aaaaaaaaaaa', 'bbbbbbbbbbb', 'ccccccccccc']) {
		await request.post('/api/candidates', { data: { video_id: id, category_id: cat.id } });
		await request.post('/api/history', { data: { video_id: id, position_s: 60, duration_s: 600 } });
	}
	for (let run = 0; run < 3; run++) {
		await freshPage(page, '/');
		await expect(page.getByRole('link', { name: '▶ Play', exact: true })).toBeFocused(); // body text also contains "Play"
		await page.keyboard.press('ArrowUp');
		const where = await focused(page);
		expect(where).toMatch(/Play on|Search|Subscriptions|Agent|Settings|AI status|Pause AI|Switch profile/);
		await page.waitForTimeout(2500);
		expect(await focused(page)).toBe(where);
		// Down again lands in the page, then stays there too.
		await page.keyboard.press('ArrowDown');
		const below = await focused(page);
		await page.waitForTimeout(1500);
		expect(await focused(page)).toBe(below);
	}
});
