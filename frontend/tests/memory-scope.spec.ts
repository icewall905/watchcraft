import { test, expect } from '@playwright/test';
import { freshPage } from './helpers';

const context = {
	requests: 0, total_input_tokens: 0, average_input_tokens: 0, median_input_tokens: 0, p95_input_tokens: 0, max_input_tokens: 0,
	cache_hit_percent: null, failed: 0, estimated_requests: 0, measured_requests: 0, cached_tokens: 0,
	average_core_chars: 0, average_correction_chars: 0, average_other_chars: 0, window_hours: 24,
	by_task: [], by_model: [], hourly: [], distribution: [], memory_profiles: [], rule_strengths: {}, revisions_24h: 0,
	configured_core_budget_tokens: 15000
};

test('AI memory switches between video and article rules and feedback without mixing them', async ({ page }) => {
	const lessonQueries: string[] = [], feedbackQueries: string[] = [], memoryQueries: string[] = [];
	const createdLessons: Record<string, unknown>[] = [];
	await page.route('**/api/lessons**', async route => {
		if (route.request().method() === 'POST') {
			const body = route.request().postDataJSON(); createdLessons.push(body);
			return route.fulfill({ json: { id: 23, profile_id: 1, content_type: body.content_type ?? 'video', text: body.text,
				category_id: body.category_id ?? null, channel_id: null, weight: 1, active: true, created_at: new Date().toISOString(), updated_at: new Date().toISOString() } });
		}
		if (route.request().method() !== 'GET') return route.continue();
		const url = new URL(route.request().url()), contentType = url.searchParams.get('content_type');
		lessonQueries.push(contentType ?? 'video-default');
		const content_type = contentType === 'article' ? 'article' : 'video';
		await route.fulfill({ json: [{ id: content_type === 'article' ? 22 : 11, profile_id: 1, content_type,
			text: content_type === 'article' ? 'Prefer reporting with local impact' : 'Prefer calm science videos',
			category_id: null, channel_id: null, weight: 1, active: true, created_at: new Date().toISOString(), updated_at: new Date().toISOString() }] });
	});
	await page.route('**/api/feedback**', async route => {
		const url = new URL(route.request().url()), contentType = url.searchParams.get('content_type');
		feedbackQueries.push(contentType ?? 'video-default');
		const article = contentType === 'article';
		await route.fulfill({ json: article ? [{ id: 32, content_type: 'article', item_id: 911, video_id: '', category_id: null,
			rating: 1, chips: ['Useful'], text: '', processed: true, created_at: new Date().toISOString(), item: { id: 911, title: 'A local archive story' } }] : [{
			id: 31, content_type: 'video', video_id: 'video-id', category_id: null, rating: -1, chips: [], text: '', processed: false,
			created_at: new Date().toISOString(), video: { id: 'video-id', title: 'A calm science video' }
		}] });
	});
	await page.route('**/api/agent/memory**', async route => {
		memoryQueries.push(new URL(route.request().url()).searchParams.get('content_type') ?? 'video-default');
		await route.fulfill({ json: { content_type: new URL(route.request().url()).searchParams.get('content_type') ?? 'video', run: { state: 'idle' },
			core: '', core_valid: true, core_updated_at: null, core_budget_tokens: 15000, versions: 0, active_rules: 1, corrections: 0, context } });
	});

	await freshPage(page, '/settings?tab=memory');
	await expect(page.getByRole('button', { name: 'Videos', exact: true })).toHaveAttribute('aria-pressed', 'true');
	await expect(page.getByText('Prefer calm science videos')).toBeVisible();
	await expect(page.getByText('A calm science video')).toBeVisible();
	await page.getByRole('button', { name: 'Articles', exact: true }).click();
	await expect(page.getByText('Prefer reporting with local impact')).toBeVisible();
	await expect(page.getByRole('link', { name: 'A local archive story' })).toHaveAttribute('href', '/item/911');
	await expect(page.locator('.lesson').filter({ hasText: 'Prefer reporting with local impact' }).getByText(/All articles/)).toBeVisible();
	await expect(page.getByText('Prefer calm science videos')).toHaveCount(0);
	await page.getByLabel('Teach the AI a rule').fill('Prefer public interest investigations');
	await page.getByRole('button', { name: 'Add rule', exact: true }).click();
	await expect.poll(() => createdLessons.length).toBe(1);
	expect(createdLessons[0]).toMatchObject({ content_type: 'article', category_id: null, text: 'Prefer public interest investigations' });
	await page.getByRole('button', { name: 'Videos', exact: true }).click();
	await expect(page.getByText('Prefer calm science videos')).toBeVisible();
	await expect(page.getByText('A calm science video')).toBeVisible();
	expect(lessonQueries).toContain('video-default');
	expect(lessonQueries).toContain('article');
	expect(feedbackQueries).toContain('video-default');
	expect(feedbackQueries).toContain('article');
	expect(memoryQueries).toContain('video-default');
	expect(memoryQueries).toContain('article');
});
