import { test, expect } from '@playwright/test';
import { freshPage } from './helpers';

test('each kind of model request can be sent to the worker or the judge', async ({ page }) => {
	await freshPage(page, '/settings?tab=ai');
	await page.getByText('Where each request goes').click();
	const plan = page.getByRole('button', { name: /Plan new video searches/ });
	await expect(plan).toContainText('Worker');
	await expect(page.getByRole('button', { name: /Front Page re-check of uncertain picks/ })).toContainText('Judge');
	await plan.click();
	await page.getByRole('option', { name: 'Judge' }).click();
	await expect(plan).toContainText('Judge');
	const saved = page.waitForRequest((r) => r.url().includes('/api/settings/llm') && r.method() === 'PUT');
	await page.getByRole('button', { name: 'Save changes' }).first().click();
	expect((await saved).postDataJSON().routes).toEqual({ video_plan: 'judge' });
	// Back to the default removes the override instead of storing it.
	await plan.click();
	await page.getByRole('option', { name: 'Worker' }).click();
	const reset = page.waitForRequest((r) => r.url().includes('/api/settings/llm') && r.method() === 'PUT');
	await page.getByRole('button', { name: 'Save changes' }).first().click();
	expect((await reset).postDataJSON().routes).toEqual({});
});
