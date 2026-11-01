import { test, expect } from '@playwright/test';
import { freshPage } from './helpers';
test('feed links use the picker without requesting a playlist grid or streaming warmup', async ({page,request}) => {
 const profiles = await (await request.get('/api/profiles')).json();
 const headers = {'X-Profile-Id':String(profiles[0].id)};
 const feed = await (await request.post('/api/categories', {headers,data:{name:'Feed route guard',kind:'shorts',brief:'Only local clips',enabled:false}})).json();
 const forbidden:string[]=[];
 page.on('request', r=>{if(/\/api\/feed\/category\/|manifest\.mpd|\/api\/proxy\//.test(r.url())) forbidden.push(r.url());});
 await freshPage(page, `/category/${feed.id}`);
 await expect(page).toHaveURL(/\/shorts$/);
 await expect(page.getByRole('heading',{name:'Choose a feed'})).toBeVisible();
 await expect(page.locator('video')).toHaveCount(0);
 expect(forbidden).toEqual([]);
});
