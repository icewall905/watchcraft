<script lang="ts" module>
 import { profileHeaders } from '$lib/api';
 // Only this authenticated, throttled preview route is loaded; never a provider URL.
 const PREVIEW = /^\/api\/hub\/boards\/\d+\/pinterest\/preview\/[\w-]{1,40}$/;
 async function previewBlob(url: string): Promise<string> {
  if (!PREVIEW.test(url)) throw new Error('Preview unavailable');
  const r = await fetch(url, {credentials: 'same-origin', headers: profileHeaders()});
  if (!r.ok) throw new Error('Preview unavailable');
  return URL.createObjectURL(await r.blob());
 }
</script>

<script lang="ts">
 // Pinterest for a board. When the owner opted in (Settings), "Search Pinterest live" runs a slow,
 // budgeted server-side search with previews proxied through The Hub. Always available: open
 // Pinterest's own search in a new tab and paste back an image address.
 import { onDestroy } from 'svelte';
 import { boardsApi, type PinterestResult, type PinterestStatus } from '$lib/hub/boards';

 let { query, boardId = null, boardName = '', prominent = false, onadded }: {
  query: string; boardId?: number | null; boardName?: string; prominent?: boolean; onadded?: () => void;
 } = $props();

 const PIN = /^https:\/\/(?:www\.)?pinterest\.com\/pin\/[A-Za-z0-9_-]{1,150}\/?(?:[?#].*)?$/i;
 let imageUrl = $state(''), pinLink = $state(''), busy = $state(false), message = $state(''), problem = $state('');
 let status = $state<PinterestStatus | null>(null);
 let results = $state<PinterestResult[] | null>(null), hasMore = $state(false), liveBusy = $state(false), liveError = $state('');
 let added = $state<Record<string, 'adding' | 'added' | 'already'>>({});
 let previews = $state<Record<string, string>>({});
 let searchedFor = '';
 let searchUrl = $derived(`https://www.pinterest.com/search/pins/?q=${encodeURIComponent(query.trim())}`);
 const errorText = (err: unknown, fallback: string) => err instanceof Error && err.message ? err.message : fallback;

 $effect(() => { if (boardId) boardsApi.pinterestLiveStatus().then(s => { status = s; }, () => { status = null; }); });
 onDestroy(() => { for (const u of Object.values(previews)) URL.revokeObjectURL(u); });

 function loadPreviews(rows: PinterestResult[]) {
  // One at a time: the server spaces image requests anyway; this keeps the order natural.
  (async () => { for (const r of rows) { try { previews[r.token] = await previewBlob(r.preview_url); } catch { /* placeholder stays */ } } })();
 }

 async function live(more = false) {
  if (!boardId) return;
  const q = more ? searchedFor : query.trim();
  if (!q) return;
  liveBusy = true; liveError = '';
  try {
   const page = await boardsApi.pinterestLive(boardId, q, more);
   searchedFor = q;
   results = more ? [...(results ?? []), ...page.results] : page.results;
   hasMore = page.has_more;
   loadPreviews(page.results);
  } catch (err) {
   liveError = errorText(err, 'Pinterest could not be searched right now.');
   if (/paused/i.test(liveError)) boardsApi.pinterestLiveStatus().then(s => { status = s; }, () => {});
  } finally { liveBusy = false; }
 }

 async function addLive(r: PinterestResult) {
  if (!boardId) return;
  added[r.token] = 'adding';
  try {
   const res = await boardsApi.pinterestAdd(boardId, r.token);
   added[r.token] = res.existing ? 'already' : 'added';
   onadded?.();
  } catch (err) { delete added[r.token]; liveError = errorText(err, 'The image could not be added.'); }
 }

 async function add(e: SubmitEvent) {
  e.preventDefault();
  const url = imageUrl.trim(), pin = pinLink.trim();
  message = ''; problem = '';
  if (!boardId) return;
  if (PIN.test(url)) { problem = 'That is a pin link. Open the pin, right-click the picture and choose “Copy image address”, then paste that here. You can put the pin link in the second box.'; return; }
  if (!/^https?:\/\//i.test(url)) { problem = 'Paste an image address that starts with https://'; return; }
  if (pin && !PIN.test(pin)) { problem = 'The pin link should look like https://www.pinterest.com/pin/123…'; return; }
  busy = true;
  try {
   const result = await boardsApi.addImage(boardId, {url, source_url: pin || undefined, title: query.trim().slice(0, 300)});
   message = result.existing ? `Already on ${boardName}.` : `Added to ${boardName}.`;
   imageUrl = ''; pinLink = '';
   onadded?.();
  } catch (err) { problem = errorText(err, 'The image could not be added.'); }
  finally { busy = false; }
 }
</script>

<section class="pinterest" class:prominent aria-label="Search Pinterest">
 <div class="head">
  <p>{prominent ? 'Nothing like this on your boards yet. Look on Pinterest instead.' : 'Want more like this?'}</p>
  <div class="buttons">
   {#if boardId && status?.enabled && !status.blocked}<button type="button" class="btn pill" class:primary={prominent} disabled={liveBusy || !query.trim()} onclick={() => live()}>{liveBusy && !results ? 'Searching Pinterest…' : 'Search Pinterest live'}</button>{/if}
   <a class="btn pill" class:primary={prominent && !status?.enabled} href={searchUrl} target="_blank" rel="noopener noreferrer external">Open Pinterest search ↗</a>
  </div>
 </div>
 {#if boardId && status?.blocked}<p class="bad small" role="status">Pinterest paused itself: {status.reason}. Live search is off until {new Date(status.blocked_until * 1000).toLocaleString()}. You can still open Pinterest and paste images.</p>{/if}
 {#if liveError}<p class="bad small" role="alert">{liveError}</p>{/if}
 {#if results}
  <p class="live">Live from Pinterest — not saved until you add it</p>
  {#if results.length}
   <ul class="grid" aria-label="Pinterest results">
    {#each results as r (r.token)}
     <li class="card">
      <div class="thumb" style:aspect-ratio={r.width && r.height ? `${r.width} / ${r.height}` : '3 / 4'}>{#if previews[r.token]}<img src={previews[r.token]} alt={r.title} loading="lazy" />{/if}</div>
      <p class="title" title={r.title}>{r.title}</p>
      <p class="muted small">{r.attribution}</p>
      {#if r.on_board || added[r.token] === 'already'}<span class="tag">On this board</span>
      {:else if added[r.token] === 'added'}<span class="tag good">Added</span>
      {:else}<button type="button" class="btn pill small" disabled={added[r.token] === 'adding'} onclick={() => addLive(r)}>{added[r.token] === 'adding' ? 'Adding…' : `Add to ${boardName}`}</button>{/if}
     </li>
    {/each}
   </ul>
   {#if hasMore}<button type="button" class="btn pill" disabled={liveBusy} onclick={() => live(true)}>{liveBusy ? 'Loading…' : 'More from Pinterest'}</button>{/if}
  {:else}<p class="muted small">Pinterest found no image pins for this.</p>{/if}
 {/if}
 {#if boardId}
  <form onsubmit={add}>
   <p class="muted small">Found one on Pinterest yourself? Right-click the picture, choose “Copy image address” and paste it here. It is saved to {boardName} and kept on this server.</p>
   <div class="row">
    <input type="url" aria-label="Pinterest image address" maxlength="2000" placeholder="https://i.pinimg.com/originals/…" bind:value={imageUrl} disabled={busy} />
    <input type="url" aria-label="Pin link (optional)" maxlength="2000" placeholder="Pin link (optional)" bind:value={pinLink} disabled={busy} />
    <button class="btn primary pill" disabled={busy || !imageUrl.trim()}>{busy ? 'Adding…' : `Add to ${boardName}`}</button>
   </div>
   {#if message}<p class="good small" role="status">{message}</p>{/if}
   {#if problem}<p class="bad small" role="alert">{problem}</p>{/if}
  </form>
 {:else}
  <p class="muted small">Open a board to add images from Pinterest to it.</p>
 {/if}
</section>

<style>
 .pinterest { margin-top: 1rem; padding: .9rem 1rem; border: 1px dashed var(--line); border-radius: var(--r-md, 12px); }
 .pinterest.prominent { border-style: solid; border-color: var(--mb-rose, var(--line)); }
 .head { display: flex; gap: .7rem; align-items: center; justify-content: space-between; flex-wrap: wrap; }
 .head p { margin: 0; }
 .buttons { display: flex; gap: .5rem; flex-wrap: wrap; }
 .live { font-size: .75rem; font-weight: 700; text-transform: uppercase; letter-spacing: .08em; color: var(--info, inherit); margin: .8rem 0 .4rem; }
 .grid { list-style: none; padding: 0; margin: 0 0 .7rem; display: grid; gap: .7rem; grid-template-columns: repeat(auto-fill, minmax(9.5rem, 1fr)); }
 .card { display: flex; flex-direction: column; gap: .25rem; min-width: 0; }
 .thumb { width: 100%; max-height: 16rem; overflow: hidden; border-radius: 10px; background: var(--mb-card, var(--line)); }
 .thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
 .title { margin: 0; font-size: .85rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
 .card p.muted { margin: 0; }
 .tag { font-size: .8rem; font-weight: 600; }
 form { margin-top: .7rem; }
 .row { display: flex; gap: .5rem; flex-wrap: wrap; }
 .row input { flex: 1 1 14rem; min-width: 0; }
 .good { color: var(--good, inherit); }
 .bad { color: var(--bad, inherit); }
</style>
