import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { join } from 'node:path';
import { resetBackend } from './helpers';

const DATA = process.env.BT_E2E_DATA;
const BACKEND = process.env.BT_E2E_BACKEND;
test.skip(!DATA || !BACKEND, 'needs scripts/e2e.sh');

function python(code: string) {
	execFileSync(join(BACKEND!, '.venv/bin/python'), ['-c', code], { cwd: BACKEND, env: { ...process.env, BT_DATA_DIR: DATA! } });
}

test.beforeAll(async ({ request }) => {
	await resetBackend(request);
	expect((await request.put('/api/settings/memory', { data: { enabled: true, max_core_tokens: 15000 } })).ok()).toBe(true);
	python(`
from sqlmodel import select, delete
from bt.db import init_db, session
from bt.models import Profile, Lesson, MemoryCore, LlmRequest
from bt.agent.memory import fingerprint
init_db()
with session() as s:
    # Browser specs share one backend. Earlier AI calls must not change the
    # average asserted below, and earlier core snapshots must not win selection.
    s.exec(delete(LlmRequest))
    s.exec(delete(MemoryCore))
    pid = s.exec(select(Profile)).first().id
    rule = Lesson(profile_id=pid, text="Prefer thoughtful science explanations")
    s.add(rule); s.commit()
    s.add(MemoryCore(profile_id=pid, text="Prefer thoughtful science explanations", sources={str(rule.id):fingerprint(rule)}))
    s.add(LlmRequest(task="verdict", model="test", input_tokens=1200, cached_tokens=600, estimated=False, ok=True))
    s.add(LlmRequest(task="memory_compaction", model="test", input_tokens=600, estimated=False, ok=True))
    s.commit()
`);
});

test('memory settings show core, input averages and an independently queued manual run', async ({ page, request }) => {
	const profiles = await (await request.get('/api/profiles')).json();
	await page.goto('/settings?tab=memory');
	await page.evaluate((pid: string) => localStorage.setItem('bt.profile', pid), String(profiles[0].id));
	await page.reload();
	await expect(page.getByRole('heading', { name: 'Nightly memory consolidation', exact: true })).toBeVisible();
	const budget = page.getByRole('slider', { name: 'Core token budget per profile', exact: true });
	await expect(budget).toHaveAttribute('aria-valuemin', '10000');
	await expect(budget).toHaveAttribute('aria-valuemax', '100000');
	await expect(budget).toHaveAttribute('aria-valuenow', '15000');
	await expect(page.locator('pre.core-memory')).toHaveText('Prefer thoughtful science explanations');
	await expect(page.getByText('Average input tokens / request', { exact: true })).toBeVisible();
	await expect(page.locator('.context-summary')).toContainText('900');
	const nightly = page.getByRole('switch', { name: /^Enable nightly consolidation/ });
	if (await nightly.getAttribute('aria-checked') === 'true') await nightly.click();
	await page.getByRole('button', { name: 'Save changes', exact: true }).click();
	await expect.poll(async () => (await (await request.get('/api/settings')).json()).memory.enabled).toBe(false);
	await page.getByRole('button', { name: 'Run now', exact: true }).click();
	await expect(page.getByText('Queued — waiting for the worker', { exact: true })).toBeVisible();
	await expect(page.getByRole('button', { name: 'Run now', exact: true })).toBeDisabled();
	python(`
from types import SimpleNamespace
from bt.agent.memory import process
from bt.db import get_settings
rt=SimpleNamespace(settings=get_settings, check=lambda:None, llm_ok=lambda:True, state=lambda *a,**k:None,event=lambda *a,**k:None)
assert process(rt)
`);
	await expect(page.getByText(/Last run: completed/)).toBeVisible();
	await expect(page.getByRole('button', { name: 'Run now', exact: true })).toBeEnabled();
	await page.screenshot({ path: '/tmp/bt-memory-settings.png', fullPage: true });
	await page.goto('/stats');
	await expect(page.getByRole('heading', { name: /AI context per request/ })).toBeVisible();
	const panel = page.locator('section.panel').filter({ has: page.getByRole('heading', { name: /AI context per request/ }) });
	await expect(panel.getByRole('heading', { name: 'Context size over time', exact: true })).toBeVisible();
	await expect(panel.getByRole('heading', { name: 'Request size distribution', exact: true })).toBeVisible();
	await expect(panel.getByRole('heading', { name: 'Current core memory by profile', exact: true })).toBeVisible();
	await expect(panel.getByText('Median input tokens', { exact: true })).toBeVisible();
	await expect(panel.locator('.profile-context')).toContainText('Family');
	await panel.screenshot({ path: '/tmp/bt-context-stats.png' });
	await page.setViewportSize({ width: 390, height: 844 });
	await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
