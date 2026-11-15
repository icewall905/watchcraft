<script lang="ts">
 // Live Pinterest search without scraping: Pinterest's robots.txt disallows bots, so the search runs in
 // the user's own browser (a new tab) and the user pastes back the images they want. WatchCraft then
 // stores each image on this server; it never loads Pinterest URLs in this page.
 import { boardsApi } from '$lib/hub/boards';

 let { query, boardId = null, boardName = '', prominent = false, onadded }: {
  query: string; boardId?: number | null; boardName?: string; prominent?: boolean; onadded?: () => void;
 } = $props();

 const PIN = /^https:\/\/(?:www\.)?pinterest\.com\/pin\/[A-Za-z0-9_-]{1,150}\/?(?:[?#].*)?$/i;
 let imageUrl = $state(''), pinLink = $state(''), busy = $state(false), message = $state(''), problem = $state('');
 let searchUrl = $derived(`https://www.pinterest.com/search/pins/?q=${encodeURIComponent(query.trim())}`);

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
  } catch (err) { problem = err instanceof Error ? err.message : 'The image could not be added.'; }
  finally { busy = false; }
 }
</script>

<section class="pinterest" class:prominent aria-label="Search Pinterest">
 <div class="head">
  <p>{prominent ? 'Nothing like this on your boards yet. Look on Pinterest instead.' : 'Want more like this?'}</p>
  <a class="btn pill" class:primary={prominent} href={searchUrl} target="_blank" rel="noopener noreferrer external">Search Pinterest for “{query.trim()}” ↗</a>
 </div>
 {#if boardId}
  <form onsubmit={add}>
   <p class="muted small">Found one you like? Right-click the picture, choose “Copy image address” and paste it here. It is saved to {boardName} and kept on this server.</p>
   <div class="row">
    <input type="url" aria-label="Pinterest image address" maxlength="2000" placeholder="https://i.pinimg.com/originals/…" bind:value={imageUrl} disabled={busy} />
    <input type="url" aria-label="Pin link (optional)" maxlength="2000" placeholder="Pin link (optional)" bind:value={pinLink} disabled={busy} />
    <button class="btn primary pill" disabled={busy || !imageUrl.trim()}>{busy ? 'Adding…' : `Add to ${boardName}`}</button>
   </div>
   {#if message}<p class="good small" role="status">{message}</p>{/if}
   {#if problem}<p class="bad small" role="alert">{problem}</p>{/if}
  </form>
 {:else}
  <p class="muted small">Open a board to paste images from Pinterest into it.</p>
 {/if}
</section>

<style>
 .pinterest { margin-top: 1rem; padding: .9rem 1rem; border: 1px dashed var(--line); border-radius: var(--r-md, 12px); }
 .pinterest.prominent { border-style: solid; border-color: var(--mb-rose, var(--line)); }
 .head { display: flex; gap: .7rem; align-items: center; justify-content: space-between; flex-wrap: wrap; }
 .head p { margin: 0; }
 form { margin-top: .7rem; }
 .row { display: flex; gap: .5rem; flex-wrap: wrap; }
 .row input { flex: 1 1 14rem; min-width: 0; }
 .good { color: var(--good, inherit); }
 .bad { color: var(--bad, inherit); }
</style>
