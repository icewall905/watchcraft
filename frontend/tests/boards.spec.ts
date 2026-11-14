import { test, expect, type Page } from '@playwright/test';
import { freshPage } from './helpers';
const board=(id:number,name:string)=>({id,name,interests:'Japanese gardens, bonsai',instructions:'',enabled:true,interval_hours:12,pin_count:3,last_status:'ok',error:''});
const pin=(id:number,title:string,bid=1,gif=false)=>({id,board_id:bid,title,image_url:`/api/hub/boards/${bid}/pins/${id}/media`,source_url:`https://commons.wikimedia.org/wiki/File:Garden${id}.jpg`,media_type:gif?'gif':'image',attribution:'Garden photographer',license:'CC BY-SA 4.0',saved:false});
async function setup(page:Page,empty=false){
 let boards=empty?[]:[board(1,'Garden inspiration'),board(2,'Pottery ideas')];
 let pins=[pin(3,'Quiet courtyard'),pin(2,'Bonsai detail',1,true),pin(1,'Stone garden path')];
 let importJob:any=null;
 const writes:{method:string;path:string;body:Record<string,unknown>}[]=[];
 await page.route('**/api/hub/boards**',async route=>{
  const req=route.request(),url=new URL(req.url()),path=url.pathname,method=req.method();
  if(path.endsWith('/media')){const id=Number(path.split('/').at(-2));return route.fulfill({contentType:'image/svg+xml',body:`<svg xmlns="http://www.w3.org/2000/svg" width="320" height="${240+id*55}" viewBox="0 0 320 400"><rect width="320" height="400" fill="#c1d4c8"/><circle cx="150" cy="160" r="${65+id*5}" fill="#446f59"/><path d="M40 400Q130 220 280 400" fill="#d7ccac"/><path d="M160 340L150 140" stroke="#5e4a3c" stroke-width="12"/></svg>`});}
  const body=method==='POST'||method==='PATCH'?req.postDataJSON()??{}:{};
  if(method!=='GET')writes.push({method,path,body});
  if(path==='/api/hub/boards'){if(method==='POST'){const made={...board(8,body.name),...body};boards.push(made);return route.fulfill({json:{board:made}});}return route.fulfill({json:{boards}});}
  const id=Number(path.split('/')[4]);
  if(path.endsWith('/pinterest')){if(method==='POST')importJob={id:1,board_id:id,status:'queued',imported:0,skipped:0,total:0,error:''};return route.fulfill({json:{import:importJob}});}
  if(path.endsWith('/refresh'))return route.fulfill({json:{queued:false,status:'Try again in a few minutes'}});
  if(path.includes('/pins/')){const pid=Number(path.split('/').at(-1)),row=pins.find(p=>p.id===pid)!;Object.assign(row,body);row.image_url=`/api/hub/boards/${row.board_id}/pins/${row.id}/media`;if(body.hidden)pins=pins.filter(p=>p.id!==pid);return route.fulfill({json:{pin:row}});}
  if(method==='PATCH'){const b=boards.find(b=>b.id===id)!;Object.assign(b,body);return route.fulfill({json:{board:b}});}
  if(method==='DELETE'){boards=boards.filter(b=>b.id!==id);return route.fulfill({json:{ok:true}});}
  let rows=pins.filter(p=>p.board_id===id);if(url.searchParams.get('saved')==='true')rows=rows.filter(p=>p.saved);
  return route.fulfill({json:{board:boards.find(b=>b.id===id),pins:rows,next_cursor:null,has_more:false}});
 });return writes;
}

test('create and edit a board with its own curator',async({page})=>{
 const writes=await setup(page,true);await freshPage(page,'/boards');
 await page.getByRole('button',{name:'Create your first board'}).click();
 await page.getByLabel('Board name',{exact:true}).fill('Small gardens');
 await page.getByLabel('Interests and hobbies').fill('Japanese gardens, bonsai');
 await page.getByLabel('What should your curator look for?').fill('Quiet spaces with stone paths');
 await page.getByRole('button',{name:'Create board',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Small gardens',exact:true})).toBeVisible();
 expect(writes[0].body).toMatchObject({name:'Small gardens',interests:'Japanese gardens, bonsai',instructions:'Quiet spaces with stone paths',enabled:true,interval_hours:12});
 await page.getByRole('button',{name:'Edit Small gardens curator'}).click();
 await page.getByLabel('Find new images automatically').uncheck();await page.getByRole('button',{name:'Save changes'}).click();
 await expect(page.getByText('Automatic discovery paused.')).toBeVisible();
});

test('browse local pins save move hide and respect refresh cooldown',async({page})=>{
 const writes=await setup(page);await freshPage(page,'/boards');
 await expect(page.getByRole('link',{name:'Mood Board',exact:true}).first()).toHaveAttribute('href','/boards');
 const view=page.getByRole('button',{name:'View Quiet courtyard',exact:true});await expect(view).toBeVisible();
 await page.getByRole('button',{name:'Save Quiet courtyard',exact:true}).click();
 await expect(page.getByRole('button',{name:'Unsave Quiet courtyard',exact:true})).toHaveAttribute('aria-pressed','true');
 await view.click();await expect(page.getByRole('link',{name:'View original source'})).toHaveAttribute('href',/commons.wikimedia/);
 await expect(page.getByText('CC BY-SA 4.0')).toBeVisible();
 await page.getByRole('button',{name:'Move to board',exact:true}).click();await page.getByLabel('Destination board').selectOption('2');await page.getByRole('button',{name:'Move image',exact:true}).click();
 await expect(view).toHaveCount(0);expect(writes.some(w=>w.body.board_id===2)).toBe(true);
 await page.getByRole('button',{name:'View Stone garden path',exact:true}).click();await page.getByRole('button',{name:'Hide image',exact:true}).click();
 await expect(page.getByRole('button',{name:'View Stone garden path',exact:true})).toHaveCount(0);
 await page.getByRole('button',{name:'Find images',exact:true}).click();await expect(page.getByRole('status').filter({hasText:'Try again in a few minutes'})).toBeVisible();
});

test('responsive masonry and reduced-motion GIF preview',async({page})=>{
 await setup(page);await page.emulateMedia({reducedMotion:'reduce'});await freshPage(page,'/boards');await page.setViewportSize({width:1440,height:1000});
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
  await freshPage(page,'/boards');await expect.poll(()=>held).toBe(true);
  await page.getByRole('button',{name:/Switch profile/}).click();await page.getByRole('button',{name:'Board isolated profile',exact:true}).click();
  await expect(page.getByRole('heading',{name:'What inspires you?'})).toBeVisible();release?.();
  await expect(page.getByRole('button',{name:'View Private garden pin'})).toHaveCount(0);
  await expect(page.getByRole('heading',{name:'Private garden board'})).toHaveCount(0);
 }finally{release?.();await request.delete(`/api/profiles/${other.id}`);}
});

 test('import public Pinterest board shows scope and queued status',async({page})=>{
 const writes=await setup(page);await freshPage(page,'/boards');
 await page.getByRole('button',{name:'Import Pinterest',exact:true}).click();
 await expect(page.getByText('Import up to 25', {exact:false})).toBeVisible();
 await page.getByLabel('Public board link').fill('https://www.pinterest.com/contemporist/architecture/');
 await page.getByRole('button',{name:'Import pins',exact:true}).click();
 await expect(page.getByRole('status').filter({hasText:'Pinterest: queued'})).toBeVisible();
 expect(writes.find(w=>w.path.endsWith('/pinterest'))?.body).toEqual({url:'https://www.pinterest.com/contemporist/architecture/'});
 });
