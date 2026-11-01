import { test, expect } from '@playwright/test';

// Installability is a manifest contract: the browser refuses to install without a name,
// a start URL, a standalone display mode and 192 + 512 icons that actually resolve.

test('the manifest is served and satisfies the install contract', async ({ request }) => {
	const res = await request.get('/manifest.webmanifest');
	expect(res.status()).toBe(200);
	expect(res.headers()['content-type']).toContain('json');
	const m = await res.json();
	expect(m.name).toBeTruthy();
	expect(m.short_name).toBeTruthy();
	expect(m.start_url).toBeTruthy();
	expect(m.display).toBe('standalone');
	expect(m.theme_color).toMatch(/^#[0-9a-f]{6}$/i);
	expect(m.background_color).toMatch(/^#[0-9a-f]{6}$/i);
	const sizes = m.icons.map((i: { sizes: string }) => i.sizes);
	expect(sizes).toContain('192x192');
	expect(sizes).toContain('512x512');
	// A maskable icon keeps the logo inside the safe area on Android.
	expect(m.icons.some((i: { purpose?: string }) => i.purpose === 'maskable')).toBe(true);
});

test('every declared icon resolves as a real PNG', async ({ request }) => {
	const m = await (await request.get('/manifest.webmanifest')).json();
	for (const icon of m.icons) {
		const res = await request.get(icon.src);
		expect(res.status(), icon.src).toBe(200);
		expect(res.headers()['content-type'], icon.src).toContain('image/png');
		const body = await res.body();
		// PNG magic number: the file is an image, not an HTML error page.
		expect(body.subarray(0, 4).toString('hex'), icon.src).toBe('89504e47');
	}
});

test('the document declares the theme colour and a viewport', async ({ page }) => {
	await page.goto('/');
	await expect(page.locator('meta[name="theme-color"]')).toHaveAttribute('content', /#[0-9a-f]{6}/i);
	await expect(page.locator('meta[name="viewport"]')).toHaveAttribute('content', /width=device-width/);
	await expect(page.locator('link[rel="manifest"]')).toHaveAttribute('href', /manifest\.webmanifest/);
});
