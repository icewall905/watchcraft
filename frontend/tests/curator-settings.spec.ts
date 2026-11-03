import { test, expect } from '@playwright/test';
import { freshPage } from './helpers';

test('reader can add, edit, pause and delete a curator preference', async ({ page }) => {
	await freshPage(page, '/settings?tab=curator');
	await expect(page.getByRole('heading', { name: 'Curator', exact: true })).toBeVisible();
	await page.getByLabel('New preference', { exact: true }).fill('Practical local-first guides');
	await page.getByRole('button', { name: 'Add preference', exact: true }).click();
	const input = page.getByLabel('Preference', { exact: true }).last();
	await expect(input).toHaveValue('Practical local-first guides');
	await input.fill('Practical self-hosted guides');
	await page.getByRole('button', { name: 'Save preference', exact: true }).last().click();
	await expect(page.getByText('Edited by you', { exact: false }).last()).toBeVisible();
	await page.getByRole('button', { name: 'Pause', exact: true }).last().click();
	await expect(page.getByRole('button', { name: 'Enable', exact: true }).last()).toBeVisible();
	await page.getByRole('button', { name: 'Delete', exact: true }).last().click();
	await expect(page.getByLabel('Preference', { exact: true })).toHaveCount(0);
});
