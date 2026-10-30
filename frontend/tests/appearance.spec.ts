import { test, expect } from '@playwright/test';
import { freshPage } from './helpers';

test('all themes apply immediately, persist, and retain readable text and buttons', async ({ page, request }) => {
	await request.put('/api/settings/appearance', { data: { theme: 'projector' } });
	try {
		await freshPage(page, '/settings?tab=appearance');
		await expect(page.getByRole('heading', { name: 'Appearance', exact: true })).toBeVisible();
		const cards = page.getByLabel('Themes', { exact: true }).locator('.theme-card');
		await expect(cards).toHaveCount(11);
		await expect(cards.first()).toHaveAttribute('aria-pressed', 'true');
		const original = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--bg-0').trim());
		expect(original.toLowerCase()).toBe('#0a0817');
		for (const name of ['Studio', 'Reel', 'Clay', 'Twilight', 'Graphite', 'Blossom', 'Cobalt', 'Lime', 'Noir', 'Tide']) {
			const card = page.getByRole('button', { name: new RegExp(`^${name}`) });
			await card.click();
			await expect(card).toHaveAttribute('aria-pressed', 'true');
			if (name === 'Studio' || name === 'Noir') await page.screenshot({ path: `/tmp/bt-theme-${name === 'Noir' ? 'noir' : 'ocean'}.png`, fullPage: true });
			const colors = await page.evaluate(() => {
				const css = getComputedStyle(document.documentElement);
				return ['--bg-1', '--text', '--accent', '--accent-ink'].map(k => css.getPropertyValue(k).trim());
			});
			function contrast(a: string, b: string) {
				const luminance = (hex: string) => {
					const rgb = hex.replace('#', '').match(/../g)!.map(x => parseInt(x, 16) / 255);
					const linear = rgb.map(v => v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
					return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
				};
				const [l1, l2] = [luminance(a), luminance(b)].sort((x, y) => y - x);
				return (l1 + 0.05) / (l2 + 0.05);
			}
			expect(contrast(colors[0], colors[1]), `${name} text contrast`).toBeGreaterThanOrEqual(4.5);
			expect(contrast(colors[2], colors[3]), `${name} primary button contrast`).toBeGreaterThanOrEqual(4.5);
		}
		await page.reload();
		await expect(page.getByRole('button', { name: /^Tide/ })).toHaveAttribute('aria-pressed', 'true');
		await expect(page.locator('html')).toHaveAttribute('data-theme', 'lagoon');
		await page.goto('/stats');
		await expect(page.locator('html')).toHaveAttribute('data-theme', 'lagoon');
		await page.goto('/settings?tab=appearance');
		await page.getByRole('button', { name: /^Projector/ }).click();
		await expect(page.locator('html')).toHaveAttribute('data-theme', 'projector');
		await expect.poll(() => page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--bg-0').trim())).toBe(original);
	} finally {
		await request.put('/api/settings/appearance', { data: { theme: 'projector' } });
	}
});
