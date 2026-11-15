import { test, expect, type Page } from '@playwright/test';
import { freshPage } from './helpers';
const board=(id:number,name:string)=>({id,name,interests:'Japanese gardens, bonsai',instructions:'',enabled:true,interval_hours:12,target_images:100,reference_url:null as string|null,reference_name:'',pin_count:3,last_status:'ok',error:'',retry_after:0,last_result:''});
const pin=(id:number,title:string,bid=1,gif=false)=>({id,board_id:bid,title,image_url:`/api/hub/boards/${bid}/pins/${id}/media`,thumb_url:`/api/hub/boards/${bid}/pins/${id}/thumb?v=1`,source_url:`https://commons.wikimedia.org/wiki/File:Garden${id}.jpg`,media_type:gif?'gif':'image',attribution:'Garden photographer',license:'CC BY-SA 4.0',saved:false});
async function setup(page:Page,empty=false,tweak?:(rows:any[])=>void,pageSize=0){
 let boards=empty?[]:[board(1,'Garden inspiration'),board(2,'Pottery ideas')];
 let pins=[pin(3,'Quiet courtyard'),pin(2,'Bonsai detail',1,true),pin(1,'Stone garden path')];
 tweak?.(pins);
 let importJob:any=null;
 const writes:{method:string;path:string;body:Record<string,unknown>}[]=[];
 await page.route('**/api/hub/boards**',async route=>{
  const req=route.request(),url=new URL(req.url()),path=url.pathname,method=req.method();
  if(path.endsWith('/media')||path.endsWith('/thumb')){const id=Number(path.split('/').at(-2));return route.fulfill({contentType:'image/svg+xml',body:`<svg xmlns="http://www.w3.org/2000/svg" width="320" height="${240+id*55}" viewBox="0 0 320 400"><rect width="320" height="400" fill="#c1d4c8"/><circle cx="150" cy="160" r="${65+id*5}" fill="#446f59"/><path d="M40 400Q130 220 280 400" fill="#d7ccac"/><path d="M160 340L150 140" stroke="#5e4a3c" stroke-width="12"/></svg>`});}
  const body=method==='POST'||method==='PATCH'||method==='PUT'?req.postDataJSON()??{}:{};
  if(method!=='GET')writes.push({method,path,body});
  if(path==='/api/hub/boards/search'){const q=url.searchParams.get('q')||'';const hits=q.includes('star')?[{...pin(5,'Product 123',2),board_name:'Pottery ideas',similarity:0.71}]:[];return route.fulfill({json:{available:true,reason:'',indexed:4,pins_total:5,pins:hits}});}
  if(path==='/api/hub/boards/pinterest/status')return route.fulfill({json:{enabled:false,blocked:false,blocked_until:0,reason:'',last_block_at:0,last_reason:'',hour_left:15,day_left:80,images_left:400}});
  if(path==='/api/hub/boards'){if(method==='POST'){const made={...board(8,body.name),...body};boards.push(made);return route.fulfill({json:{board:made}});}return route.fulfill({json:{boards}});}
  const id=Number(path.split('/')[4]);
  if(path.endsWith('/pinterest')){if(method==='POST')importJob={id:1,board_id:id,status:'queued',imported:0,skipped:0,total:0,error:''};return route.fulfill({json:{import:importJob}});}
  if(path.endsWith('/reference')){const b=boards.find(b=>b.id===id)!;if(method==='DELETE'){b.reference_url=null;b.reference_name='';return route.fulfill({json:{board:b}});}b.reference_url=`/api/hub/boards/${id}/reference/media?v=1`;b.reference_name=body.title||'Reference image';return route.fulfill({json:{board:b}});}
  if(path.endsWith('/images')){const known:any=body.source_url&&pins.find((p:any)=>p.source_url===body.source_url);if(known)return route.fulfill({json:{pin:known,existing:true}});const added=pin(9,body.title||'Uploaded image',id);if(body.source_url)added.source_url=body.source_url;added.saved=true;if(body.data_base64)added.source_url='upload:sha256:abc';pins=[added,...pins];return route.fulfill({json:{pin:added}});}
  if(path.endsWith('/reshuffle')){const b:any=boards.find(b=>b.id===id)!;const gone=pins.filter((p:any)=>p.board_id===id&&!p.saved&&p.feedback!=='good');pins=pins.filter(p=>!gone.includes(p));Object.assign(b,{last_status:'queued',pin_count:pins.filter(p=>p.board_id===id).length});return route.fulfill({json:{replaced:gone.length,kept:b.pin_count,board:b}});}
  if(path.endsWith('/refresh')){const b=boards.find(b=>b.id===id)!;Object.assign(b,{last_status:'no_matches',last_result:'No images met your instructions in the last search.',retry_after:3});return route.fulfill({json:{queued:false,status:'cooldown',retry_after:3,last_result:b.last_result,board:b}});}
  if(path.endsWith('/feedback')){const pid=Number(path.split('/').at(-2)),row:any=pins.find(p=>p.id===pid)!;row.feedback=body.verdict;row.feedback_note=body.note||'';if(body.verdict==='bad')pins=pins.filter(p=>p.id!==pid);return route.fulfill({json:{pin:{...row,hidden:body.verdict==='bad'},board:boards.find(b=>b.id===id)}});}
  if(path.includes('/pins/')){const pid=Number(path.split('/').at(-1)),row=pins.find(p=>p.id===pid)!;Object.assign(row,body);row.image_url=`/api/hub/boards/${row.board_id}/pins/${row.id}/media`;if(body.hidden)pins=pins.filter(p=>p.id!==pid);return route.fulfill({json:{pin:row}});}
  if(method==='PATCH'){const b=boards.find(b=>b.id===id)!;Object.assign(b,body);return route.fulfill({json:{board:b}});}
  if(method==='DELETE'){boards=boards.filter(b=>b.id!==id);return route.fulfill({json:{ok:true}});}
  const all=pins.filter(p=>p.board_id===id),counted={...boards.find(b=>b.id===id),pin_count:all.length,saved_count:all.filter((p:any)=>p.saved||p.feedback==='good').length};
  let rows=all;if(url.searchParams.get('saved')==='true')rows=rows.filter((p:any)=>p.saved||p.feedback==='good');if(url.searchParams.get('unreviewed')==='true')rows=rows.filter((p:any)=>!p.feedback);
  const cur=url.searchParams.get('cursor');if(cur)rows=rows.filter(p=>p.id<Number(cur));
  if(pageSize&&rows.length>pageSize){rows=rows.slice(0,pageSize);return route.fulfill({json:{board:counted,pins:rows,next_cursor:String(rows.at(-1)!.id),has_more:true}});}
  return route.fulfill({json:{board:counted,pins:rows,next_cursor:null,has_more:false}});
 });return writes;
}

test('create and edit a board with its own curator',async({page})=>{
 const writes=await setup(page,true);await freshPage(page,'/boards');
 await page.getByRole('button',{name:'Create your first board'}).click();
 await page.getByLabel('Board name',{exact:true}).fill('Small gardens');
 await page.getByLabel('Interests and hobbies').fill('Japanese gardens, bonsai');
 await page.getByLabel('What to look for').fill('Quiet spaces with stone paths');
 await page.getByLabel('What to avoid').fill('Text graphics, people');
 await page.getByLabel('Seed searches').fill('zen garden\nmoss courtyard');
 await page.getByText('More tuning').click();await page.getByLabel(/Balanced/).check();await page.getByLabel('Image type').selectOption('photos');await page.getByLabel('Minimum size').selectOption('1000');
 await page.getByLabel('Image target').fill('240');
 for(const width of [1280,390]){await page.setViewportSize({width,height:900});
  // No field may stick out of the dialog (the image-target number box used to).
  const over=await page.evaluate(()=>{const d=document.querySelector('[role=dialog]')!.getBoundingClientRect();return [...document.querySelectorAll('[role=dialog] input, [role=dialog] select, [role=dialog] textarea, [role=dialog] button')].filter(e=>{const r=e.getBoundingClientRect();return r.width&&(r.right>d.right+1||r.left<d.left-1);}).map(e=>e.outerHTML.slice(0,80));});
  expect(over).toEqual([]);}
 await page.getByRole('button',{name:'Create board',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Small gardens',exact:true})).toBeVisible();
 expect(writes[0].body).toMatchObject({name:'Small gardens',interests:'Japanese gardens, bonsai',instructions:'Quiet spaces with stone paths',avoid:'Text graphics, people',seed_searches:'zen garden\nmoss courtyard',strictness:'balanced',media:'photos',min_width:1000,enabled:true,interval_hours:12,target_images:240});
 await page.getByRole('button',{name:'Edit Small gardens curator'}).click();
 await page.getByLabel('Find new images automatically').uncheck();await page.getByRole('button',{name:'Save changes'}).click();
 await expect(page.getByText('Automatic discovery paused.')).toBeVisible();
});

test('board target defaults to 100 and users can set a visual reference and add uploaded images',async({page})=>{
 const writes=await setup(page);await freshPage(page,'/boards?board=1');
 await expect(page.getByText('3 / 100 images')).toBeVisible();
 await page.getByRole('button',{name:'Add inspiration image'}).click();
 await page.getByLabel('Image URL').fill('https://example.com/mood.jpg');
 await page.getByRole('button',{name:'Save inspiration'}).click();
 await expect(page.getByRole('region',{name:'Mood board inspiration image'})).toContainText('Visual inspiration');
 expect(writes.find(w=>w.path.endsWith('/reference')&&w.method==='POST')?.body).toEqual({url:'https://example.com/mood.jpg',title:undefined});
 await page.getByRole('button',{name:'Add image',exact:true}).click();
 await page.getByLabel('Image file').setInputFiles({name:'detail.webp',mimeType:'image/webp',buffer:Buffer.from('sample image')});
 await page.getByLabel('Caption').fill('My uploaded reference');
 await page.getByRole('button',{name:'Add image',exact:true}).last().click();
 await expect(page.getByRole('button',{name:'View My uploaded reference'})).toBeVisible();
 const upload=writes.find(w=>w.path.endsWith('/images'))?.body;
 expect(upload?.title).toBe('My uploaded reference');expect(typeof upload?.data_base64).toBe('string');expect(upload?.url).toBeUndefined();
 await expect(page.getByText('Image added to the board.')).toBeVisible();await expect(page.getByRole('dialog')).toHaveCount(0);await page.waitForTimeout(600);await page.getByRole('button',{name:'View My uploaded reference'}).click();
 await expect(page.getByRole('heading',{name:'Added by you'})).toBeVisible();await page.keyboard.press('Escape');
 await page.getByRole('button',{name:'Remove inspiration'}).click();
 await expect(page.getByRole('region',{name:'Mood board inspiration image'})).toHaveCount(0);
});

test('browse local pins save move hide and respect refresh cooldown',async({page})=>{
 const writes=await setup(page);await freshPage(page,'/boards?board=1');
 await expect(page.getByRole('link',{name:'Mood Board',exact:true}).first()).toHaveAttribute('href','/boards');
 const view=page.getByRole('button',{name:'View Quiet courtyard',exact:true});await expect(view).toBeVisible();
 await page.getByRole('button',{name:'Save Quiet courtyard',exact:true}).click();
 await expect(page.getByRole('button',{name:'Unsave Quiet courtyard',exact:true})).toHaveAttribute('aria-pressed','true');
 await view.click();await expect(page.getByRole('link',{name:'View original source'})).toHaveAttribute('href',/commons.wikimedia/);
 await expect(page.getByText('CC BY-SA 4.0')).toBeVisible();
 await page.getByRole('button',{name:'Move to another board',exact:true}).click();await page.getByLabel('Destination board').selectOption('2');await page.getByRole('button',{name:'Move image',exact:true}).click();
 await expect(view).toHaveCount(0);expect(writes.some(w=>w.body.board_id===2)).toBe(true);
 const viewer=page.getByRole('dialog');
 await expect(viewer.getByRole('heading',{name:'Bonsai detail'})).toBeVisible(); // moving keeps browsing: next image opens
 await viewer.getByRole('button',{name:'Next image'}).click();await expect(viewer.getByRole('heading',{name:'Stone garden path'})).toBeVisible();
 await viewer.getByRole('button',{name:'Remove',exact:true}).click();
 await expect(viewer.getByRole('heading',{name:'Bonsai detail'})).toBeVisible();await page.keyboard.press('Escape');
 await expect(page.getByRole('dialog')).toHaveCount(0);
 await expect(page.getByRole('button',{name:'View Stone garden path',exact:true})).toHaveCount(0);
 await page.getByRole('button',{name:'Find images',exact:true}).click();
 const activity=page.getByRole('region',{name:'Board curator activity'});
 await expect(activity).toContainText('No images met your instructions in the last search.');
 await expect(page.getByRole('button',{name:/Find images \(\d+s\)/})).toBeDisabled();
 await expect(page.getByRole('button',{name:'Find images',exact:true})).toBeEnabled({timeout:6000});
});

test('responsive masonry and reduced-motion GIF preview',async({page})=>{
 await setup(page);await page.emulateMedia({reducedMotion:'reduce'});await freshPage(page,'/boards?board=1');await page.setViewportSize({width:1440,height:1000});
 await expect(page.getByRole('button',{name:'View Quiet courtyard'}).locator('img')).toBeVisible();await page.screenshot({path:'/tmp/watchcraft-boards-desktop.png',fullPage:true});
 await page.getByRole('button',{name:'View Bonsai detail',exact:true}).click();await page.getByRole('button',{name:'Play GIF',exact:true}).click();await expect(page.getByRole('dialog').locator('img')).toBeVisible();await page.keyboard.press('Escape');
 await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.screenshot({path:'/tmp/watchcraft-boards-mobile.png',fullPage:true});
});

test('a delayed board response cannot cross a profile switch',async({page,request})=>{
 const other=await(await request.post('/api/profiles',{data:{name:'Board isolated profile'}})).json();
 let release:(()=>void)|undefined,held=false;
 await page.route('**/api/hub/boards**',async route=>{
  const isOther=route.request().headers()['x-profile-id']===String(other.id),path=new URL(route.request().url()).pathname;
  if(path==='/api/hub/boards')return route.fulfill({json:{boards:isOther?[]:[board(1,'Private garden board')]}});
  held=true;await new Promise<void>(resolve=>{release=resolve;});
  await route.fulfill({json:{board:board(1,'Private garden board'),pins:[pin(3,'Private garden pin')],has_more:false,next_cursor:null}}).catch(()=>{});
 });
 try{
  await freshPage(page,'/boards?board=1');await expect.poll(()=>held).toBe(true);
  await page.getByRole('button',{name:/Switch profile/}).click();await page.getByRole('button',{name:'Board isolated profile',exact:true}).click();
  await expect(page.getByRole('heading',{name:'What inspires you?'})).toBeVisible();release?.();
  await expect(page.getByRole('button',{name:'View Private garden pin'})).toHaveCount(0);
  await expect(page.getByRole('heading',{name:'Private garden board'})).toHaveCount(0);
 }finally{release?.();await request.delete(`/api/profiles/${other.id}`);}
});

 test('import public Pinterest board shows scope and queued status',async({page})=>{
 const writes=await setup(page);await freshPage(page,'/boards?board=1');
 await page.getByRole('button',{name:'Import Pinterest',exact:true}).click();
 await expect(page.getByText('Import up to 25', {exact:false})).toBeVisible();
 await page.getByLabel('Public board link').fill('https://www.pinterest.com/contemporist/architecture/');
 await page.getByRole('button',{name:'Import pins',exact:true}).click();
 await expect(page.getByRole('status').filter({hasText:'Pinterest: queued'})).toBeVisible();
 expect(writes.find(w=>w.path.endsWith('/pinterest'))?.body).toEqual({url:'https://www.pinterest.com/contemporist/architecture/'});
 });


test('an already collecting board resumes polling and explains an empty result and cooldown',async({page})=>{
 let state={...board(1,'Precise sewing patterns'),pin_count:0,last_status:'collecting',last_result:'',retry_after:0};
 let details=0;
 await page.route('**/api/hub/boards**',route=>{
  const path=new URL(route.request().url()).pathname;
  if(path==='/api/hub/boards')return route.fulfill({json:{boards:[state]}});
  if(path.endsWith('/pinterest'))return route.fulfill({json:{import:null}});
  details++;return route.fulfill({json:{board:state,pins:[],next_cursor:null,has_more:false}});
 });
 await freshPage(page,'/boards?board=1');
 const activity=page.getByRole('region',{name:'Board curator activity'});
 await expect(activity).toContainText('Curator is checking images');
 await expect(page.getByRole('button',{name:'Finding images…'}).first()).toBeDisabled();
 const before=details;
 state={...state,last_status:'no_matches',last_result:'Checked 12 images with vision. None met your instructions.',retry_after:2};
 await expect.poll(()=>details,{timeout:12000}).toBeGreaterThan(before);
 await expect(activity).toContainText('No matching images this time');
 await expect(activity).toContainText('Checked 12 images with vision. None met your instructions.');
 await expect(page.getByRole('button',{name:/Find images \(\d+s\)/}).first()).toBeDisabled();
 await expect(page.getByRole('button',{name:'Find images',exact:true}).first()).toBeEnabled({timeout:6000});
 await expect(page.getByText('Try again in a few minutes')).toHaveCount(0);
 await page.screenshot({path:'/tmp/watchcraft-board-curator-result.png',fullPage:true});
});

test('queued and failed curator searches stay visible without reporting a successful search as an error',async({page})=>{
 let state={...board(1,'Heart dresses'),pin_count:0,last_status:'queued',last_result:'',retry_after:0};
 await page.route('**/api/hub/boards**',route=>{
  const path=new URL(route.request().url()).pathname;
  if(path==='/api/hub/boards')return route.fulfill({json:{boards:[state]}});
  if(path.endsWith('/pinterest'))return route.fulfill({json:{import:null}});
  return route.fulfill({json:{board:state,pins:[],next_cursor:null,has_more:false}});
 });
 await freshPage(page,'/boards?board=1');
 const activity=page.getByRole('region',{name:'Board curator activity'});
 await expect(activity).toContainText('Curator queued');
 await expect(page.getByRole('button',{name:'Queued…'}).first()).toBeDisabled();
 state={...state,last_status:'error',error:'The vision model is unavailable. No unverified images were added.',last_result:'The search could not finish.'};
 await expect(activity).toContainText('Curator needs attention',{timeout:12000});
 await expect(activity).toContainText('The vision model is unavailable. No unverified images were added.');
 await expect(page.getByRole('button',{name:'Find images',exact:true}).first()).toBeEnabled();
});

test('search finds images across boards by what the picture shows',async({page})=>{
 await setup(page);await freshPage(page,'/boards');
 await page.getByLabel("Search your boards by what's in the picture").fill('flower dress with stars');
 await page.getByRole('button',{name:'Search',exact:true}).click();
 const results=page.getByRole('region',{name:'Search results'});
 await expect(results).toContainText('Best matches for “flower dress with stars”');
 await expect(results).toContainText('4 of 5 searchable');
 await expect(results.getByRole('button',{name:'View Product 123'})).toBeVisible();
 await expect(page.getByRole('heading',{name:'Garden inspiration'})).toHaveCount(0);
 await results.getByRole('button',{name:'on Pottery ideas'}).click();
 await expect(page.getByRole('region',{name:'Search results'})).toHaveCount(0);
 await expect(page.getByRole('heading',{name:'Pottery ideas',exact:true})).toBeVisible();
 await page.getByLabel("Search your boards by what's in the picture").fill('purple cat');
 await page.getByRole('button',{name:'Search',exact:true}).click();
 await expect(page.getByText('No matching images')).toBeVisible();
 await page.getByRole('button',{name:'Clear'}).click();
 await expect(page.getByRole('region',{name:'Search results'})).toHaveCount(0);
});

test('review pins: confirm fits and teach the curator what does not fit',async({page})=>{
 const writes=await setup(page);await freshPage(page,'/boards?board=1');
 const fits=page.getByRole('group',{name:'Actions for Quiet courtyard'}).getByRole('button',{name:'Fits'});
 await fits.click();await expect(fits).toHaveAttribute('aria-pressed','true');
 expect(writes.at(-1)).toMatchObject({method:'PUT',body:{verdict:'good'}});
 await page.getByRole('group',{name:'Actions for Stone garden path'}).getByRole('button',{name:'Not a fit'}).click();
 await page.getByRole('button',{name:'Text, quote or poster image'}).click();
 await expect(page.getByLabel('Tell the curator (optional)')).toHaveValue('Text, quote or poster image');
 await page.getByRole('button',{name:'Remove and teach curator'}).click();
 await expect(page.getByText('Your curator will avoid images like this.')).toBeVisible();
 await expect(page.getByRole('button',{name:'View Stone garden path'})).toHaveCount(0);
 expect(writes.at(-1)).toMatchObject({method:'PUT',path:'/api/hub/boards/1/pins/1/feedback',body:{verdict:'bad',note:'Text, quote or poster image'}});
 await page.getByRole('button',{name:'To review'}).click();
 await expect(page.getByRole('button',{name:'View Bonsai detail'})).toBeVisible();
 await expect(page.getByRole('button',{name:'View Quiet courtyard'})).toHaveCount(0);
 // Marking Fits while reviewing keeps the image where it is.
 const bonsai=page.getByRole('group',{name:'Actions for Bonsai detail'}).getByRole('button',{name:'Fits'});
 await bonsai.click();await expect(bonsai).toHaveAttribute('aria-pressed','true');
 await expect(page.getByRole('button',{name:'View Bonsai detail'})).toBeVisible();
 // Remove takes an image off the board without a verdict for the curator.
 await page.getByRole('button',{name:'All',exact:true}).click();
 const puts=writes.filter(w=>w.method==='PUT').length;
 await page.getByRole('button',{name:'Remove Bonsai detail'}).click();
 await expect(page.getByRole('button',{name:'View Bonsai detail'})).toHaveCount(0);
 await expect(page.getByText('Your curator was not told.')).toBeVisible();
 expect(writes.at(-1)).toMatchObject({method:'PATCH',path:'/api/hub/boards/1/pins/2',body:{hidden:true}});
 expect(writes.filter(w=>w.method==='PUT').length).toBe(puts);
 // Saved lists hearted images and every image marked Fits.
 await page.getByRole('button',{name:/^Saved/}).click();
 await expect(page.getByRole('button',{name:'View Quiet courtyard'})).toBeVisible();
});

test('the grid never reshuffles: loading more, hiding and reviewing keep every image in place',async({page})=>{
 await setup(page,false,rows=>{rows.splice(0,rows.length,...Array.from({length:24},(_,i)=>({...pin(24-i,`Tile ${24-i}`),width:300,height:[200,520,330,410,260,600][i%6]})));},12);
 await page.setViewportSize({width:1280,height:900});await freshPage(page,'/boards?board=1');
 await expect(page.getByRole('button',{name:'View Tile 13',exact:true})).toBeVisible();
 const spots=async()=>page.evaluate(()=>Object.fromEntries([...document.querySelectorAll('article.pin')].map(a=>{const r=a.getBoundingClientRect();return[a.querySelector('.pin-title')!.textContent,Math.round(r.left)];})));
 const before=await spots();expect(new Set(Object.values(before)).size).toBeGreaterThan(2);
 await page.getByRole('button',{name:'More inspiration'}).click();
 await expect(page.getByRole('button',{name:'View Tile 1',exact:true})).toBeAttached();
 const after=await spots();for(const [title,x] of Object.entries(before))expect(after[title],title).toBe(x);
 await page.getByRole('group',{name:'Actions for Tile 20'}).getByRole('button',{name:'Fits'}).click();
 await page.getByRole('group',{name:'Actions for Tile 22'}).getByRole('button',{name:'Not a fit'}).click();
 await page.getByRole('button',{name:'Remove and teach curator'}).click();
 await expect(page.getByRole('button',{name:'View Tile 22',exact:true})).toHaveCount(0);
 const later=await spots();for(const [title,x] of Object.entries(later))expect(x,title).toBe(after[title]);
 expect(Object.keys(later)).toHaveLength(23);
});

test('image viewer: why it was picked, zoom, keyboard browsing, download',async({page})=>{
 await setup(page,false,rows=>{Object.assign(rows[0],{width:320,height:405,vision_seen:'A moss courtyard with a stone lantern',vision_why:'Matches Japanese gardens.'});});
 await freshPage(page,'/boards?board=1');
 await page.getByRole('button',{name:'View Quiet courtyard',exact:true}).click();
 const viewer=page.getByRole('dialog');const why=viewer.getByRole('region',{name:'Why this image is here'});
 await expect(why).toContainText('Why your curator picked it');await expect(why).toContainText('A moss courtyard with a stone lantern');await expect(why).toContainText('Matches Japanese gardens.');
 await expect(viewer.getByText('1 / 3')).toBeVisible();
 await viewer.getByRole('button',{name:'Zoom in'}).click();await expect(viewer.getByRole('button',{name:'Fit to screen'})).toHaveText('140%');
 await viewer.getByRole('button',{name:'Fit to screen'}).click();await expect(viewer.getByRole('button',{name:'Fit to screen'})).toHaveText('100%');
 await page.keyboard.press('ArrowRight');await expect(viewer.getByRole('heading',{name:'Bonsai detail'})).toBeVisible();await expect(viewer.getByText('2 / 3')).toBeVisible();
 await expect(why).toContainText('No curator notes');
 await page.keyboard.press('ArrowLeft');await expect(viewer.getByRole('heading',{name:'Quiet courtyard'})).toBeVisible();
 const download=page.waitForEvent('download');await viewer.getByRole('button',{name:'Download'}).click();
 expect((await download).suggestedFilename()).toBe('Quiet-courtyard.svg'.replace('.svg','.jpg'));
});

test('boards overview: collage cards, filter, sort and back navigation',async({page})=>{
 await setup(page,false,rows=>{rows.forEach(r=>r.saved=r.id===3);});
 await page.route('**/api/hub/boards',async route=>{if(route.request().method()!=='GET')return route.fallback();const many=['Garden inspiration','Pottery ideas','Star dresses','Kitchen','Bonsai','Wedding'].map((name,i)=>({id:i+1,name,interests:'x',instructions:'',enabled:i!==4,interval_hours:12,target_images:100,reference_url:null,reference_name:'',pin_count:10-i,saved_count:i===0?2:0,last_status:i===1?'collecting':i===3?'error':'ok',error:'',retry_after:0,last_result:'',preview_urls:i===0?['/api/hub/boards/1/pins/3/media','/api/hub/boards/1/pins/2/media','/api/hub/boards/1/pins/1/media']:[],last_added:1000+i,created_at:1}));return route.fulfill({json:{boards:many}});});
 await freshPage(page,'/boards');
 const overview=page.getByRole('region',{name:'All boards'});
 await expect(overview.getByRole('heading',{name:'6 boards'})).toBeVisible();
 await expect(overview.getByRole('button',{name:/^Open /})).toHaveCount(6);
 await expect(overview.getByRole('button',{name:'Open Pottery ideas, 9 images'})).toContainText('Curator searching');
 await expect(overview.getByRole('button',{name:'Open Kitchen, 7 images'})).toContainText('Needs attention');
 await expect(overview.getByRole('button',{name:'Open Bonsai, 6 images'})).toContainText('Paused');
 await expect(overview.getByRole('button',{name:/^Open /}).first()).toHaveAccessibleName(/Wedding/); // most recently updated first
 await page.getByLabel('Sort boards').selectOption('name');
 await expect(overview.getByRole('button',{name:/^Open /}).first()).toHaveAccessibleName(/Bonsai/);
 await page.getByLabel('Filter boards').fill('star');
 await expect(overview.getByRole('button',{name:/^Open /})).toHaveCount(1);
 await page.getByLabel('Filter boards').fill('');
 await overview.getByRole('button',{name:'Open Garden inspiration, 10 images'}).click();
 await expect(page).toHaveURL(/board=1/);
 await expect(page.getByRole('heading',{name:'Garden inspiration',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'← All boards'}).click();
 await expect(page.getByRole('region',{name:'All boards'})).toBeVisible();
 await page.goBack();await expect(page.getByRole('heading',{name:'Garden inspiration',exact:true})).toBeVisible();
 await page.setViewportSize({width:390,height:844});await page.goBack();
 await expect(page.getByRole('region',{name:'All boards'})).toBeVisible();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});

test('browser extension: download links, install steps and a connection key',async({page})=>{
 await setup(page);
 let keys:any[]=[];
 await page.route('**/api/ext/keys**',async route=>{const m=route.request().method();if(m==='POST'){const info={id:1,name:'Chrome',hint:'w9Zk',created_at:1,last_used:null};keys=[info];return route.fulfill({json:{key:'wcx_secretkeyw9Zk',info}});}if(m==='DELETE'){keys=[];return route.fulfill({json:{ok:true}});}return route.fulfill({json:{keys}});});
 await freshPage(page,'/boards');
 await page.getByRole('button',{name:'Browser extension'}).click();
 await expect(page.getByRole('dialog')).toContainText('New board…');await expect(page.getByRole('dialog')).toContainText('Updating:');
 const dialog=page.getByRole('dialog',{name:'Add images from any website'});
 await expect(dialog.getByRole('link',{name:'Download for Chrome'})).toHaveAttribute('href','/extensions/watchcraft-chrome.zip');
 await expect(dialog).toContainText('Load unpacked');
 await dialog.getByRole('tab',{name:'Firefox'}).click();
 await expect(dialog.getByRole('link',{name:'Download for Firefox'})).toHaveAttribute('href','/extensions/watchcraft-firefox.zip');
 await expect(dialog).toContainText('Load Temporary Add-on');
 await dialog.getByRole('button',{name:'Create connection key'}).click();
 await expect(dialog.getByText('wcx_secretkeyw9Zk')).toBeVisible();
 await expect(dialog.getByRole('region',{name:'Connected browsers'})).toContainText('…w9Zk');
 await dialog.getByRole('button',{name:'Revoke'}).click();
 await expect(dialog.getByRole('region',{name:'Connected browsers'})).toHaveCount(0);
 const zip=await page.request.get('/extensions/watchcraft-firefox.zip');expect(zip.status()).toBe(200);expect((await zip.body()).subarray(0,2).toString()).toBe('PK');
});

test('desktop mode: Backspace edits text and never closes a dialog; Escape still does',async({page})=>{
 await setup(page);await freshPage(page,'/boards?board=1');
 await page.getByRole('button',{name:'Add image',exact:true}).click();
 const url=page.getByLabel('Image URL');await url.click();await url.fill('ab');
 await page.keyboard.press('Backspace');await page.keyboard.press('Backspace');await page.keyboard.press('Backspace');await page.keyboard.press('Backspace');
 await expect(page.getByRole('dialog')).toBeVisible();await expect(url).toHaveValue('');
 await page.keyboard.press('Escape');await expect(page.getByRole('dialog')).toHaveCount(0);
});

test('reshuffle replaces everything not hearted or thumbs-up, after confirming',async({page})=>{
 const writes=await setup(page,false,rows=>{rows[0].saved=true;(rows[1] as any).feedback='good';});
 await freshPage(page,'/boards?board=1');
 await page.getByRole('button',{name:'Reshuffle'}).click();
 const dialog=page.getByRole('dialog',{name:'Reshuffle this board?'});
 await expect(dialog).toContainText('1 image you have not hearted or given a thumbs up will be replaced');
 await dialog.getByRole('button',{name:'Cancel'}).click();expect(writes.some(w=>w.path.endsWith('/reshuffle'))).toBe(false);
 await page.getByRole('button',{name:'Reshuffle'}).click();
 await page.getByRole('button',{name:'Replace 1 image'}).click();
 await expect(page.getByText('Replacing 1 image. Your curator is finding new ones')).toBeVisible();
 await expect(page.getByRole('button',{name:'View Stone garden path'})).toHaveCount(0);
 await expect(page.getByRole('button',{name:'View Quiet courtyard'})).toBeVisible();
 await expect(page.getByRole('button',{name:'View Bonsai detail'})).toBeVisible();
 expect(writes.at(-1)).toMatchObject({method:'POST',path:'/api/hub/boards/1/reshuffle'});
});

test('grid uses small previews, preloads them gently, and never refetches while scrolling',async({page})=>{
 await setup(page,false,rows=>{rows.splice(0,rows.length,...Array.from({length:30},(_,i)=>({...pin(30-i,`Tile ${30-i}`),width:300,height:[200,520,330,410][i%4]})));});
 const hits:Record<string,number>={};let inflight=0,peak=0;
 page.on('request',r=>{const u=new URL(r.url());if(/\/(thumb|media)$/.test(u.pathname)){hits[u.pathname]=(hits[u.pathname]??0)+1;inflight++;peak=Math.max(peak,inflight);}});
 page.on('requestfinished',r=>{if(/\/(thumb|media)$/.test(new URL(r.url()).pathname))inflight--;});
 page.on('requestfailed',r=>{if(/\/(thumb|media)$/.test(new URL(r.url()).pathname))inflight--;});
 await page.setViewportSize({width:1280,height:800});await freshPage(page,'/boards?board=1');
 // Every preview is fetched in the background, including tiles far below the fold.
 await expect.poll(()=>Object.keys(hits).filter(k=>k.endsWith('/thumb')).length,{timeout:15000}).toBe(30);
 expect(Object.keys(hits).some(k=>k.endsWith('/media'))).toBe(false);
 // Reaching a tile shows its already-downloaded preview at once; scrolling back up refetches nothing.
 const last=page.getByRole('button',{name:'View Tile 1',exact:true});
 await last.scrollIntoViewIfNeeded();await expect(last.locator('img').first()).toBeVisible({timeout:1500});
 await page.evaluate(()=>window.scrollTo(0,0));await page.waitForTimeout(300);
 await page.getByRole('button',{name:'View Tile 1',exact:true}).scrollIntoViewIfNeeded();await page.waitForTimeout(300);
 expect(Object.values(hits).every(n=>n===1)).toBe(true);
 // The viewer loads the full image.
 await page.getByRole('button',{name:'View Tile 30',exact:true}).click();
 await expect.poll(()=>hits['/api/hub/boards/1/pins/30/media']??0).toBe(1);
});

test('child friendly: grown-ups can switch it on; child profiles always have it and lose adult-only tools',async({page})=>{
 const writes=await setup(page);await freshPage(page,'/boards?board=1');
 await page.getByRole('button',{name:'Edit Garden inspiration curator'}).click();
 const kid=page.getByLabel(/Child friendly/);await expect(kid).toBeEnabled();await kid.check();
 await page.getByText('More tuning').click();await expect(page.getByRole('radio',{name:/Balanced/})).toBeDisabled();
 await page.getByRole('button',{name:'Save changes'}).click();
 expect(writes.at(-1)).toMatchObject({method:'PATCH',body:{child_friendly:true,strictness:'strict'}});
 // A child profile: Mood Boards and the kid-safe Hub open, Music stays hidden, only the curator adds images.
 await page.route('**/api/profiles',async route=>{if(route.request().method()!=='GET')return route.fallback();const res=await route.fetch();const list=await res.json();return route.fulfill({json:list.map((p:any,i:number)=>i===0?{...p,role:'child'}:p)});});
 await page.reload();
 await expect(page.getByRole('heading',{name:'Garden inspiration'})).toBeVisible();
 await expect(page.getByRole('button',{name:'Browser extension'})).toHaveCount(0);
 await expect(page.getByRole('button',{name:'Add image',exact:true})).toHaveCount(0);
 await expect(page.getByRole('button',{name:'Import Pinterest'})).toHaveCount(0);
 await page.getByLabel("Search your boards by what's in the picture").fill('cat');await page.getByRole('button',{name:'Search',exact:true}).click();
 await expect(page.getByRole('region',{name:'Search Pinterest'})).toHaveCount(0);
 await expect(page.getByRole('link',{name:'Mood Board',exact:true}).first()).toBeVisible();
 await expect(page.getByRole('link',{name:'News',exact:true}).first()).toBeVisible();
 await expect(page.getByRole('navigation',{name:'Main',exact:true}).locator('a[href="/music"]')).toHaveCount(0);
 await page.getByRole('button',{name:'New board'}).first().click();
 await expect(page.getByLabel(/Child friendly/)).toBeChecked();await expect(page.getByLabel(/Child friendly/)).toBeDisabled();
});

test('open Pinterest search in a new tab and pasted images are stored on the board',async({page})=>{
 const writes=await setup(page);await freshPage(page,'/boards?board=1');
 await expect(page.getByRole('heading',{name:'Garden inspiration',exact:true})).toBeVisible();
 await page.getByLabel("Search your boards by what's in the picture").fill('purple cat');
 await page.getByRole('button',{name:'Search',exact:true}).click();
 const live=page.getByRole('region',{name:'Search Pinterest'});
 await expect(live).toContainText('Look on Pinterest instead');
 const link=live.getByRole('link',{name:/Open Pinterest search/});
 await expect(link).toHaveAttribute('href','https://www.pinterest.com/search/pins/?q=purple%20cat');
 await expect(link).toHaveAttribute('target','_blank');
 await expect(link).toHaveAttribute('rel',/noopener/);
 await expect(page.locator('img[src*="pinimg"], img[src*="pinterest"]')).toHaveCount(0);
 const image=live.getByLabel('Pinterest image address'),pinLink=live.getByLabel('Pin link (optional)'),add=live.getByRole('button',{name:'Add to Garden inspiration'});
 await image.fill('https://www.pinterest.com/pin/123456789/');await add.click();
 await expect(live.getByRole('alert')).toContainText('Copy image address');
 expect(writes.filter(w=>w.path.endsWith('/images'))).toHaveLength(0);
 await image.fill('https://i.pinimg.com/originals/ab/cd/ef/abcdef.jpg');await pinLink.fill('https://www.pinterest.com/pin/123456789/');await add.click();
 await expect(live.getByRole('status')).toHaveText('Added to Garden inspiration.');
 expect(writes.find(w=>w.path==='/api/hub/boards/1/images')?.body).toEqual({url:'https://i.pinimg.com/originals/ab/cd/ef/abcdef.jpg',source_url:'https://www.pinterest.com/pin/123456789/',title:'purple cat'});
 await image.fill('https://i.pinimg.com/originals/ab/cd/ef/abcdef.jpg');await pinLink.fill('https://www.pinterest.com/pin/123456789/');await add.click();
 await expect(live.getByRole('status')).toHaveText('Already on Garden inspiration.');
});

test('Pinterest link-out without an open board only offers the search',async({page})=>{
 await setup(page);await freshPage(page,'/boards');
 await page.getByLabel("Search your boards by what's in the picture").fill('flower dress with stars');
 await page.getByRole('button',{name:'Search',exact:true}).click();
 const live=page.getByRole('region',{name:'Search Pinterest'});
 await expect(live).toContainText('Want more like this?');
 await expect(live).toContainText('Open a board to add images');
 await expect(live.getByLabel('Pinterest image address')).toHaveCount(0);
});

async function livePinterest(page:Page,status:Record<string,unknown>={}){
 const calls:{path:string;q?:string|null;more?:string|null;body?:any}[]=[];let searches=0;
 const st={enabled:true,blocked:false,blocked_until:0,reason:'',last_block_at:0,last_reason:'',hour_left:14,day_left:79,images_left:398,...status};
 await page.route(/\/api\/hub\/boards\/(?:\d+\/)?pinterest\/(status|search|preview|add)/,async route=>{
  const req=route.request(),url=new URL(req.url()),path=url.pathname;
  if(path.endsWith('/status'))return route.fulfill({json:st});
  if(path.includes('/preview/')){calls.push({path});return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="236" height="300"><rect width="236" height="300" fill="#d9b8c4"/></svg>'});}
  if(path.endsWith('/add')){const body=req.postDataJSON();calls.push({path,body});return route.fulfill({json:{pin:pin(20,'Window seat'),existing:false}});}
  searches++;calls.push({path,q:url.searchParams.get('q'),more:url.searchParams.get('more')});
  if(searches===3)return route.fulfill({status:429,json:{detail:'Pinterest requests are spaced out. Try again shortly. Try again in 14 seconds.'}});
  const more=url.searchParams.get('more')==='true';
  const rows=more?[{token:'t3',title:'Attic nook',pin_url:'https://www.pinterest.com/pin/1003/',attribution:'@loft (Pinterest)',width:236,height:300,preview_url:'/api/hub/boards/1/pinterest/preview/t3',on_board:false}]
   :[{token:'t1',title:'Window seat',pin_url:'https://www.pinterest.com/pin/1001/',attribution:'@maker (Pinterest)',width:236,height:300,preview_url:'/api/hub/boards/1/pinterest/preview/t1',on_board:false},
     {token:'t2',title:'Bay window',pin_url:'https://www.pinterest.com/pin/1002/',attribution:'@maker (Pinterest)',width:236,height:280,preview_url:'/api/hub/boards/1/pinterest/preview/t2',on_board:true}];
  return route.fulfill({json:{results:rows,has_more:!more}});
 });
 return calls;
}

test('live Pinterest search: proxied previews, add to board, already on board, more and wait message',async({page})=>{
 await setup(page);const calls=await livePinterest(page);
 const external:string[]=[];page.on('request',r=>{const h=new URL(r.url()).hostname;if(/pinimg|pinterest/.test(h))external.push(r.url());});
 await freshPage(page,'/boards?board=1');
 await page.getByLabel("Search your boards by what's in the picture").fill('reading nook');
 await page.getByRole('button',{name:'Search',exact:true}).click();
 const live=page.getByRole('region',{name:'Search Pinterest'});
 await live.getByRole('button',{name:'Search Pinterest live'}).click();
 await expect(live.getByText('Live from Pinterest')).toBeVisible();
 const grid=live.getByRole('list',{name:'Pinterest results'});
 await expect(grid.getByRole('listitem')).toHaveCount(2);
 await expect(grid.getByRole('img',{name:'Window seat'})).toBeVisible();
 expect(await grid.getByRole('img',{name:'Window seat'}).getAttribute('src')).toMatch(/^blob:/);
 await expect(grid.getByRole('listitem').nth(1)).toContainText('On this board');
 await grid.getByRole('listitem').first().getByRole('button',{name:'Add to Garden inspiration'}).click();
 await expect(grid.getByRole('listitem').first()).toContainText('Added');
 expect(calls.find(c=>c.path.endsWith('/add'))?.body).toEqual({token:'t1'});
 expect(calls[0]).toMatchObject({path:'/api/hub/boards/1/pinterest/search',q:'reading nook'});
 await live.getByRole('button',{name:'More from Pinterest'}).click();
 await expect(grid.getByRole('listitem')).toHaveCount(3);
 expect(calls.filter(c=>c.q).at(-1)?.more).toBe('true');
 await live.getByRole('button',{name:'Search Pinterest live'}).click();
 await expect(live.getByRole('alert')).toContainText('Try again in 14 seconds');
 expect(external).toEqual([]);
});

test('live Pinterest search is hidden when off and explains a self-pause',async({page})=>{
 await setup(page);
 await freshPage(page,'/boards?board=1');
 await page.getByLabel("Search your boards by what's in the picture").fill('reading nook');
 await page.getByRole('button',{name:'Search',exact:true}).click();
 const live=page.getByRole('region',{name:'Search Pinterest'});
 await expect(live.getByRole('link',{name:/Open Pinterest search/})).toBeVisible();
 await expect(live.getByRole('button',{name:'Search Pinterest live'})).toHaveCount(0);
 await livePinterest(page,{enabled:false,blocked:true,blocked_until:Math.floor(Date.now()/1000)+86400,reason:'HTTP 403 from Pinterest',last_block_at:Math.floor(Date.now()/1000)});
 await page.reload();
 await page.getByLabel("Search your boards by what's in the picture").fill('reading nook');
 await page.getByRole('button',{name:'Search',exact:true}).click();
 await expect(page.getByRole('region',{name:'Search Pinterest'})).toContainText('Pinterest paused itself: HTTP 403 from Pinterest');
 await expect(page.getByRole('region',{name:'Search Pinterest'}).getByRole('button',{name:'Search Pinterest live'})).toHaveCount(0);
});
