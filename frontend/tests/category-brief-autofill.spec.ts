import { expect, test } from '@playwright/test';
import { freshPage } from './helpers';

const BRIEF = 'Simple, fun science for kids 5-9: experiments, space, animals. Nothing scary or mean.';

test('the brief autofills from the name and asks before replacing typed text', async ({ page }) => {
	const sent: { name: string; kind: string }[] = [];
	await page.route('**/api/categories/autofill-brief', (r) => {
		sent.push(r.request().postDataJSON());
		return r.fulfill({ json: { brief: BRIEF } });
	});
	await freshPage(page, '/settings?tab=categories&new=1');
	const name = page.getByLabel('Name', { exact: true });
	const brief = page.getByLabel('Brief for the AI', { exact: true });
	const button = page.getByRole('button', { name: 'Write the brief with AI' });

	// Without a name there is nothing to work from: say so instead of calling the AI.
	await button.click();
	await expect(page.getByText('Give the playlist a name first.')).toBeVisible();
	expect(sent).toEqual([]);
	await expect(brief).toHaveValue('');

	await name.fill('Science for kids');
	await button.click();
	await expect(brief).toHaveValue(BRIEF);
	expect(sent).toEqual([{ name: 'Science for kids', kind: 'playlist' }]);

	// A brief the AI just wrote can be regenerated in one click.
	await button.click();
	await expect.poll(() => sent.length).toBe(2);

	// Text the family typed is not replaced without a second press.
	await brief.fill('My own words');
	await button.click();
	await expect(button).toHaveText('Press again: replaces the brief');
	await expect(brief).toHaveValue('My own words');
	expect(sent.length).toBe(2);
	await button.click();
	await expect(brief).toHaveValue(BRIEF);
	expect(sent.length).toBe(3);
});
