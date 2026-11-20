import { spawn, type ChildProcess } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import { login } from './helpers';

// Runs its own backend in BT_AUTH=accounts mode (the shared e2e server is in open mode), so it cannot
// disturb other specs. Needs the built frontend (npm run build) and backend/.venv.
test.describe.configure({ mode: 'serial' });

let proc: ChildProcess | undefined;
let dataDir = '';
let base = '';

const freePort = () =>
	new Promise<number>((resolve, reject) => {
		const s = createServer();
		s.listen(0, '127.0.0.1', () => {
			const { port } = s.address() as { port: number };
			s.close(() => resolve(port));
		});
		s.on('error', reject);
	});

test.beforeAll(async () => {
	test.setTimeout(60_000);
	const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
	const backend = process.env.BT_E2E_BACKEND ?? path.resolve(frontend, '../backend');
	dataDir = mkdtempSync(path.join(tmpdir(), 'bt-accounts-'));
	const port = await freePort();
	base = `http://127.0.0.1:${port}`;
	proc = spawn(path.join(backend, '.venv/bin/uvicorn'), ['bt.main:app', '--port', String(port)], {
		cwd: backend,
		env: { ...process.env, BT_AUTH: 'accounts', BT_DATA_DIR: dataDir, BT_STATIC_DIR: path.join(frontend, 'build') },
		stdio: 'ignore'
	});
	for (let i = 0; i < 80; i++) {
		try {
			if ((await fetch(`${base}/api/health`)).ok) return;
		} catch { /* not up yet */ }
		await new Promise((r) => setTimeout(r, 500));
	}
	throw new Error('accounts backend did not start');
});

test.afterAll(() => {
	proc?.kill();
	if (dataDir) rmSync(dataDir, { recursive: true, force: true });
});

const PASS = 'correct horse battery';

test('bootstrap, invite, register, two accounts in one browser, device code', async ({ browser }) => {
	// First admin through the UI.
	const ctx = await browser.newContext({ baseURL: base, viewport: { width: 1280, height: 900 } });
	const page = await ctx.newPage();
	await page.goto('/');
	await expect(page.getByText('Create the first admin')).toBeVisible();
	await page.getByLabel('Username').fill('alice');
	await page.getByLabel('Password', { exact: true }).fill(PASS);
	await page.getByLabel('Repeat password').fill(PASS);
	await page.getByRole('button', { name: 'Create admin' }).click();
	await expect(page.locator('main')).toBeVisible();

	// Invite bob (API as alice: same-origin request carries her cookies).
	const created = await page.evaluate(async () => {
		const r = await fetch('/api/admin/accounts', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json', 'X-Account': sessionStorage.getItem('wc.account') ?? '' },
			body: JSON.stringify({ username: 'bob', role: 'user' })
		});
		return r.json();
	});
	expect(created.link).toMatch(/^\/join\//);

	// Bob registers in a second, separate browser context.
	const bobCtx = await browser.newContext({ baseURL: base });
	const bobPage = await bobCtx.newPage();
	await bobPage.goto(created.link);
	await bobPage.getByLabel('Password', { exact: true }).fill(PASS);
	await bobPage.getByLabel('Repeat password').fill(PASS);
	await bobPage.getByRole('button', { name: 'Create account' }).click();
	await expect(bobPage.locator('main')).toBeVisible();
	const bobProfiles = await bobPage.evaluate(async () => (await fetch('/api/profiles', { headers: { 'X-Account': sessionStorage.getItem('wc.account') ?? '' } })).json());
	await bobCtx.close();

	// Sign bob into alice's browser as a second account.
	await page.reload();
	// Second sign-in on the same browser (API; the Add account UI is covered by the picker).
	const aliceId = await page.evaluate(() => sessionStorage.getItem('wc.account'));
	const loginRes = await page.evaluate(async () => {
		const r = await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: 'bob', password: 'correct horse battery' }) });
		return r.json();
	});
	expect(loginRes.public_id).toBeTruthy();
	expect(loginRes.public_id).not.toBe(aliceId);

	// Switch tab to bob and compare profile lists.
	await page.evaluate((id: string) => sessionStorage.setItem('wc.account', id), loginRes.public_id);
	await page.reload();
	const asBob = await page.evaluate(async () => (await fetch('/api/profiles', { headers: { 'X-Account': sessionStorage.getItem('wc.account') ?? '' } })).json());
	await page.evaluate((id: string) => sessionStorage.setItem('wc.account', id), aliceId!);
	await page.reload();
	const asAlice = await page.evaluate(async () => (await fetch('/api/profiles', { headers: { 'X-Account': sessionStorage.getItem('wc.account') ?? '' } })).json());
	const ids = (l: { id: number }[]) => l.map((p) => p.id);
	expect(ids(asBob).filter((i) => ids(asAlice).includes(i))).toEqual([]);
	expect(ids(asBob)).toEqual(ids(bobProfiles));

	// Device code: a TV page asks for a code, alice approves it from another page.
	const tvCtx = await browser.newContext({ baseURL: base });
	const tv = await tvCtx.newPage();
	await tv.goto('/');
	await tv.getByRole('button', { name: 'Sign in with code' }).click();
	const code = (await tv.getByTestId('device-code').innerText()).trim();
	expect(code).toMatch(/^[A-Z0-9]{4}-?[A-Z0-9]{2}$/);
	await page.goto(`/link?code=${encodeURIComponent(code)}`);
	await page.getByRole('button', { name: 'Approve' }).click();
	await expect(page.getByTestId('link-approved')).toBeVisible();
	await expect(tv.locator('main')).toBeVisible({ timeout: 15_000 }); // poll runs every 3 s
	await tvCtx.close();
	await ctx.close();
});

test('login screen rejects a wrong password and accepts the right one', async ({ browser }) => {
	const ctx = await browser.newContext({ baseURL: base });
	const page = await ctx.newPage();
	await page.goto('/');
	await login(page, 'alice', 'wrong password');
	await expect(page.getByRole('alert')).toContainText(/not right/i);
	await login(page, 'alice', PASS);
	await expect(page.locator('main')).toBeVisible();
	await ctx.close();
});
