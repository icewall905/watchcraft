import { test, expect } from '@playwright/test';
import { resetBackend } from './helpers';

// Exercise persistence through the real API; only the external AI reply is simulated.
test.beforeEach(async ({ request }) => {
	await resetBackend(request);
	await request.put('/api/settings/setup', { data: { completed: false } });
	await request.put('/api/settings/agent', { data: { enabled: false } });
});
test.afterEach(async ({ request }) => {
	await request.put('/api/settings/setup', { data: { completed: true } });
});

test('first visit saves a profile and can skip AI without enabling background work', async ({ page, request }) => {
	await page.goto('/');
	await expect(page).toHaveURL(/\/welcome$/);
	await page.getByLabel('Profile name').fill('Our home');
	await page.getByRole('button', { name: 'Save profile and continue' }).click();
	await expect(page.getByRole('heading', { name: "Let AI find things you'll enjoy." })).toBeVisible();
	await expect(page.locator('body')).toHaveCSS('font-family', /Inter/);
	await page.evaluate(() => document.fonts.ready);
	await page.screenshot({ path: 'screenshots/welcome-ai-1920.png' });
	await page.getByRole('button', { name: 'Skip AI and start browsing' }).click();
	await expect(page).toHaveURL(/\/search$/);
	let settings = await (await request.get('/api/settings')).json();
	expect(settings.agent.enabled).toBe(false);
	expect(settings.setup.completed).toBe(true);
	expect((await (await request.get('/api/profiles')).json())[0].name).toBe('Our home');
	await page.goto('/');
	await expect(page).not.toHaveURL(/\/welcome/);
});

test('connection failure keeps the guide open; successful test saves AI and a first playlist', async ({ page, request }) => {
	await request.put('/api/settings/llm', { data: { base_url: '', model: '', api_key: '' } });
	let ok = false;
	await page.route('**/api/agent/llm-test', route => route.fulfill({ json: ok ? { ok: true, reply: 'pong', models: ['home-model'] } : { ok: false, error: 'Model server is not reachable' } }));
	await page.goto('/welcome');
	await page.getByRole('button', { name: 'Save profile and continue' }).click();
	await page.getByLabel('Judge server').fill('http://host.docker.internal:8080/v1');
	await page.getByLabel('Judge model', { exact: true }).fill('home-model');
	await page.getByRole('button', { name: 'Test, save and continue' }).click();
	await expect(page.getByText('Could not reach the model')).toBeVisible();
	expect((await (await request.get('/api/settings')).json()).agent.enabled).toBe(false);
	ok = true;
	await page.getByRole('button', { name: 'Test, save and continue' }).click();
	await expect(page.getByLabel('Playlist name')).toBeVisible();
	await page.getByLabel('Playlist name').fill('Welcome test playlist');
	await page.getByLabel('What should the curator look for?').fill('Calm woodworking with clear explanations.');
	await page.getByRole('button', { name: 'Create playlist and start watching' }).click();
	await expect(page).toHaveURL(/\/$/);
	const settings = await (await request.get('/api/settings')).json();
	expect(settings.agent.enabled).toBe(true);
	expect(settings.setup.completed).toBe(true);
	expect(settings.llm.model).toBe('home-model');
	const categories = await (await request.get('/api/categories')).json();
	const created = categories.find((c: { name: string }) => c.name === 'Welcome test playlist');
	expect(created).toBeTruthy();
	await request.delete(`/api/categories/${created.id}`);
});

test('guide remains usable on a phone without horizontal scrolling', async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await page.goto('/welcome');
	await expect(page.getByRole('heading', { name: 'Who is watching?' })).toBeVisible();
	expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
	await page.screenshot({ path: 'screenshots/welcome-phone.png' });
});
