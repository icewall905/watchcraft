import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { join } from 'node:path';

const data = process.env.BT_E2E_DATA;
const backend = process.env.BT_E2E_BACKEND;
test.skip(!data || !backend, 'needs scripts/e2e.sh');

test.beforeAll(() => {
	execFileSync(join(backend!, '.venv/bin/python'), ['-c', `
from sqlmodel import select
from bt.db import init_db, session
from bt.models import Candidate, Category, Profile, Video
init_db()
with session() as s:
    profile = s.exec(select(Profile)).first()
    cat = Category(profile_id=profile.id, name='Remote back playlist')
    s.add(cat)
    s.commit()
    s.refresh(cat)
    for vid in ['backtest001', 'backtest002']:
        s.add(Video(id=vid, title=vid, duration_s=120))
        s.add(Candidate(category_id=cat.id, video_id=vid, status='accepted', score=80))
    s.commit()
`], { cwd: backend, env: { ...process.env, BT_DATA_DIR: data! } });
});

test('Back leaves the entire watch session and returns to its playlist', async ({ page, request }) => {
	const categories = await (await request.get('/api/categories')).json();
	const id = categories.find((c: { name: string }) => c.name === 'Remote back playlist').id;
	await page.addInitScript(() => localStorage.setItem('bt.profile', '1'));
	await page.goto(`/category/${id}`);
	await page.getByRole('link', { name: 'backtest001' }).click();
	await expect(page).toHaveURL(/\/watch\/backtest001/);
	// A normal in-app navigation can add another watch page to browser history.
	await page.evaluate((category) => {
		const link = document.createElement('a');
		link.href = `/watch/backtest002?c=${category}`;
		link.textContent = 'Another video';
		document.body.appendChild(link);
		link.click();
	}, id);
	await expect(page).toHaveURL(/\/watch\/backtest002/);
	await page.keyboard.press('Escape');
	await expect(page).toHaveURL(new RegExp(`/category/${id}$`));
});
