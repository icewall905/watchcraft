import { test, expect } from '@playwright/test';

test('choose a provider, discover models with its key, and keep the custom connection until saving', async ({ page, request }) => {
	const original = { base_url: 'http://custom-llm:8080/v1', model: 'family-model', api_key: 'custom-secret' };
	await request.put('/api/settings/llm', { data: original });
	let observed: { base_url: string; api_key: string } | null = null;
	await page.route('**/api/agent/llm-models', async route => {
		observed = route.request().postDataJSON();
		await route.fulfill({ json: { ok: true, models: ['glm-example', 'kimi-example'] } });
	});
	await page.goto('/settings?tab=ai');
	const provider = page.getByRole('button', { name: /^Provider/ });
	const server = page.getByLabel('Judge server');
	const key = page.getByLabel('Judge API key', { exact: true });
	const model = page.getByLabel('Judge model', { exact: true });
	await expect(server).toHaveValue(original.base_url);
	await expect(key).toHaveValue('********');
	await provider.click();
	await expect(page.getByRole('option', { name: /Ollama Cloud/ })).toBeVisible();
	await expect(page.getByRole('option', { name: /OpenRouter/ })).toBeVisible();
	await expect(page.getByRole('option', { name: /OpenCode Zen/ })).toBeVisible();
	for (const name of ['OpenAI', 'Anthropic', 'xAI', 'DeepSeek', 'Qwen']) {
		await expect(page.getByRole('option', { name: new RegExp(`^${name}\\b`) })).toBeAttached();
	}
	await page.getByRole('option', { name: /OpenCode Go/ }).click();
	await expect(server).toHaveValue('https://opencode.ai/inference/go/openai/v1');
	await expect(key).toHaveValue('');
	await expect(model).toHaveValue('');
	await key.fill('go-test-key');
	await page.getByRole('button', { name: 'Load available models' }).click();
	await expect(page.getByRole('button', { name: 'kimi-example' })).toBeVisible();
	expect(observed).toEqual({ base_url: 'https://opencode.ai/inference/go/openai/v1', api_key: 'go-test-key' });
	await page.getByLabel('Filter models').fill('kimi');
	await expect(page.getByRole('button', { name: 'glm-example' })).toHaveCount(0);
	await page.getByRole('button', { name: 'kimi-example' }).click();
	await expect(model).toHaveValue('kimi-example');
	// Merely exploring does not change the running curator's settings.
	let saved = (await (await request.get('/api/settings')).json()).llm;
	expect(saved.base_url).toBe(original.base_url);
	expect(saved.model).toBe(original.model);
	await provider.click();
	await page.getByRole('option', { name: /Custom \/ local/ }).click();
	await expect(server).toHaveValue(original.base_url);
	await expect(key).toHaveValue('********');
	await expect(model).toHaveValue(original.model);
	await provider.click();
	await page.getByRole('option', { name: /OpenCode Go/ }).click();
	await key.fill('go-test-key');
	await model.fill('kimi-example');
	await page.getByRole('button', { name: 'Save changes', exact: true }).first().click();
	await expect.poll(async () => (await (await request.get('/api/settings')).json()).llm.base_url)
		.toBe('https://opencode.ai/inference/go/openai/v1');
	saved = (await (await request.get('/api/settings')).json()).llm;
	expect(saved.model).toBe('kimi-example');
	expect(saved.api_key).toBe('********');
});
