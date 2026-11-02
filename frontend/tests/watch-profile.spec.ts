import { expect, test, type Page } from '@playwright/test';
import { resetBackend } from './helpers';

// The DASH manifest is profile-scoped: the backend resolves the profile from X-Profile-Id and falls
// back to the first profile when the header is absent. Shaka fetches the manifest with its own
// networking engine, so the header has to be added by a request filter.
const VID = 'profiletest1';

type FilterRun = { uri: string; headers: Record<string, string> };

test.beforeAll(async ({ request }) => resetBackend(request));

/** Make playback reach Shaka (no local file, no detail) and record every manifest request. */
async function watchFixture(page: Page) {
	const manifests: { url: string; profile: string | undefined }[] = [];
	await page.route(`**/api/video/${VID}`, (r) => r.fulfill({ status: 503, json: { detail: 'unavailable' } }));
	await page.route(`**/api/video/${VID}/file`, (r) => r.fulfill({ status: 404, body: '' }));
	await page.route(`**/api/video/${VID}/manifest.mpd*`, (r) => {
		manifests.push({ url: r.request().url(), profile: r.request().headers()['x-profile-id'] });
		return r.fulfill({ status: 503, json: { detail: 'unavailable' } });
	});
	return manifests;
}

test('the manifest request carries the selected profile, not the first one', async ({ page, request }) => {
	const second = await (await request.post('/api/profiles', { data: { name: 'Second Viewer' } })).json();
	const first = (await (await request.get('/api/profiles')).json())[0];
	expect(second.id).not.toBe(first.id);
	const manifests = await watchFixture(page);
	await page.addInitScript((id: number) => localStorage.setItem('bt.profile', String(id)), second.id);
	await page.goto(`/watch/${VID}`);
	await expect.poll(() => manifests.length).toBeGreaterThan(0);
	for (const m of manifests) expect(m.profile).toBe(String(second.id));
});

test('the profile header is confined to same-origin manifest requests', async ({ page }) => {
	await page.addInitScript(() => {
		(window as unknown as { __manifestFilter: FilterRun[] }).__manifestFilter = [];
	});
	// Replace the lazy Shaka chunk with a stub that runs the registered filter over sample URIs.
	await page.route('**/_app/immutable/chunks/*.js', async (route) => {
		const response = await route.fetch();
		const source = await response.text();
		if (!source.includes('shaka.util.DataViewWriter')) return route.fulfill({ response });
		return route.fulfill({
			contentType: 'application/javascript',
			body: `
const seen = window.__manifestFilter;
const URIS = ['/api/video/abc12345678/manifest.mpd', 'https://media.example/manifest.mpd',
  '/api/video/abc12345678/segment', '/api/proxy/token', '/api/video/abc12345678/manifest.mpd?max_height=720'];
class Player {
 static isBrowserSupported() { return true; }
 async attach() {}
 getNetworkingEngine() { return { registerRequestFilter: (filter) => {
  for (const uri of URIS) {
   const request = { uris: [uri], headers: {} };
   filter(0, request);
   seen.push({ uri, headers: request.headers });
  }
 }}; }
 configure() {}
 addEventListener() {}
 getVideoTracks() { return []; }
 async load() {}
 async destroy() {}
}
const stub = { Player, polyfill: { installAll() {} } };
export default stub; export const s = { default: stub };`
		});
	});
	await watchFixture(page);
	await page.goto(`/watch/${VID}`);
	const runs = () => page.evaluate(() => (window as unknown as { __manifestFilter: FilterRun[] }).__manifestFilter);
	await expect.poll(async () => (await runs()).length).toBe(5);
	const seen = await runs();
	const header = (uri: string) => seen.find((s) => s.uri === uri)?.headers['X-Profile-Id'];
	expect(header('/api/video/abc12345678/manifest.mpd')).toBeTruthy();
	expect(header('/api/video/abc12345678/manifest.mpd?max_height=720')).toBeTruthy();
	expect(header('https://media.example/manifest.mpd')).toBeUndefined();
	expect(header('/api/video/abc12345678/segment')).toBeUndefined();
	expect(header('/api/proxy/token')).toBeUndefined();
});
