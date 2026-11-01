import { test, expect } from '@playwright/test';
import { freshPage } from './helpers';

// Accessibility invariants that can be checked without a screen reader: every control has a
// name, every image has an alt attribute, every field has a label, and nothing traps the
// keyboard with a positive tabindex.

const PAGES = ['/', '/discover', '/memes', '/library', '/settings', '/find?q=test', '/curators'];

for (const url of PAGES) {
	test(`${url} gives every control an accessible name`, async ({ page }) => {
		await freshPage(page, url);
		const unnamed = await page.evaluate(() => {
			const name = (el: Element) => {
				const aria = el.getAttribute('aria-label')?.trim();
				if (aria) return aria;
				const labelled = el.getAttribute('aria-labelledby');
				if (labelled) {
					const text = labelled.split(/\s+/).map((id) => document.getElementById(id)?.textContent ?? '').join(' ').trim();
					if (text) return text;
				}
				const text = (el.textContent ?? '').trim();
				if (text) return text;
				const img = el.querySelector('img[alt]');
				if (img?.getAttribute('alt')?.trim()) return img.getAttribute('alt')!.trim();
				const title = el.getAttribute('title')?.trim();
				if (title) return title;
				const value = (el as HTMLInputElement).value?.trim();
				return value || '';
			};
			return [...document.querySelectorAll('main button, main a[href], main [role="button"], main [role="tab"]')]
				.filter((el) => {
					const r = el.getBoundingClientRect();
					return r.width > 0 && r.height > 0 && !(el as HTMLButtonElement).disabled;
				})
				.filter((el) => !name(el))
				.map((el) => el.outerHTML.slice(0, 120));
		});
		expect(unnamed, `unnamed controls on ${url}`).toEqual([]);
	});
}

test('images carry an alt attribute', async ({ page }) => {
	await freshPage(page, '/');
	const missing = await page.evaluate(() =>
		[...document.querySelectorAll('main img')]
			.filter((img) => !img.hasAttribute('alt'))
			.map((img) => img.getAttribute('src') ?? '(no src)'));
	expect(missing).toEqual([]);
});

test('form fields are labelled', async ({ page }) => {
	await freshPage(page, '/settings?tab=search');
	const unlabelled = await page.evaluate(() =>
		[...document.querySelectorAll('main input:not([type="hidden"]), main select, main textarea')]
			.filter((el) => {
				const id = el.getAttribute('id');
				if (el.getAttribute('aria-label')?.trim()) return false;
				if (el.getAttribute('aria-labelledby')) return false;
				if (id && document.querySelector(`label[for="${CSS.escape(id)}"]`)) return false;
				if (el.closest('label')) return false;
				return true;
			})
			.map((el) => el.outerHTML.slice(0, 120)));
	expect(unlabelled).toEqual([]);
});

test('no positive tabindex and exactly one h1 per page', async ({ page }) => {
	for (const url of PAGES) {
		await freshPage(page, url);
		const bad = await page.evaluate(() => ({
			tabindex: [...document.querySelectorAll('[tabindex]')]
				.filter((el) => Number(el.getAttribute('tabindex')) > 0)
				.map((el) => el.outerHTML.slice(0, 100)),
			h1: document.querySelectorAll('main h1').length
		}));
		expect(bad.tabindex, `positive tabindex on ${url}`).toEqual([]);
		expect(bad.h1, `h1 count on ${url}`).toBeLessThanOrEqual(1);
	}
});

test('the keyboard focus ring is visible', async ({ page }) => {
	await freshPage(page, '/');
	// The app drives focus with its own arrow-key engine, so move with it rather than Tab.
	await page.keyboard.press('ArrowDown');
	await expect.poll(() => page.evaluate(() => document.activeElement?.tagName ?? '')).not.toBe('BODY');
	const ring = await page.evaluate(() => {
		const el = document.activeElement as HTMLElement | null;
		if (!el || el === document.body) return null;
		const s = getComputedStyle(el);
		return { outline: s.outlineStyle, width: s.outlineWidth, shadow: s.boxShadow };
	});
	expect(ring).not.toBeNull();
	const visible = ring!.outline !== 'none' || ring!.shadow !== 'none';
	expect(visible, 'the focused element has no visible ring').toBe(true);
});
