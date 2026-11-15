<script lang="ts">
 import { onMount } from 'svelte';
 import Modal from '$lib/components/Modal.svelte';
 import Avatar from '$lib/components/Avatar.svelte';
 import { toast } from '$lib/stores/toast.svelte';
 import { session } from '$lib/stores/session.svelte';
 import { boardMediaBlob } from '$lib/hub/BoardImage.svelte';
 import type { BoardPin } from '$lib/hub/boards';
 import { shareApi, type ShareLink, type ShareTarget } from '$lib/hub/share';

 // board + optional pin: share that image or the whole board. No board: manage every public link.
 let { board, pin, onClose }: { board?: {id: number; name: string}; pin?: BoardPin; onClose: () => void } = $props();

 let tab = $state<'profile' | 'link'>('profile');  // Without a board only the link list shows.
 let targets = $state<ShareTarget[] | null>(null);
 let links = $state<ShareLink[]>([]);
 let busy = $state(false), error = $state('');
 let expires = $state<'' | '1' | '7' | '30'>('7');
 let includeSource = $state(false);
 let created = $state('');
 const canLink = $derived(session.hubAllowed);
 const what = $derived(pin ? 'this image' : 'this board');
 const title = $derived(board ? (pin ? 'Share image' : `Share “${board.name}”`) : 'Public links');

 onMount(() => {
  if (board) shareApi.targets().then(r => { targets = r.profiles; }).catch(() => { targets = []; });
  void loadLinks();
 });

 async function loadLinks() {
  try {
   const all = (await shareApi.links(board?.id)).links;
   links = pin ? all.filter(l => l.pin_id === pin.id) : board ? all.filter(l => l.kind === 'board') : all;
  } catch { /* the list is optional */ }
 }
 function fail(e: unknown) { error = (e as {message?: string})?.message || 'Something went wrong. Try again.'; }

 async function send(t: ShareTarget) {
  if (!board) return;
  busy = true; error = '';
  try {
   const r = await shareApi.toProfile(board.id, t.id, pin?.id);
   toast.success(r.added ? `Sent ${pin ? 'the image' : `${r.added} image${r.added === 1 ? '' : 's'}`} to ${r.recipient}.` : `${r.recipient} already has ${pin ? 'this image' : 'these images'}.`);
   onClose();
  } catch (e) { fail(e); } finally { busy = false; }
 }

 async function makeLink() {
  if (!board) return;
  busy = true; error = '';
  try {
   const r = await shareApi.createLink(board.id, {pin_id: pin?.id, expires_days: expires ? Number(expires) : null, include_source: includeSource});
   created = location.origin + r.path;
   await loadLinks();
  } catch (e) { fail(e); } finally { busy = false; }
 }
 async function copy() {
  try { await navigator.clipboard.writeText(created); toast.success('Link copied.'); }
  catch { error = 'Copy did not work here. Select the link and copy it yourself.'; }
 }
 async function shareLink() {
  try { await navigator.share({title: pin?.title || board?.name, url: created}); }
  catch (e) { if ((e as Error)?.name !== 'AbortError') error = 'Sharing is not available here. Copy the link instead.'; }
 }
 // Prepared up front: the share sheet must open straight from the tap, or browsers refuse it.
 let prepared: File | null = null;
 async function prepareFile() {
  if (!pin) return null;
  const blob = await boardMediaBlob(pin.image_url);
  const ext = (blob.type.split('/')[1] || 'jpg').replace('jpeg', 'jpg');
  return new File([blob], `${(pin.title || 'image').replace(/[^\w.-]+/g, '-').slice(0, 60)}.${ext}`, {type: blob.type || 'image/jpeg'});
 }
 $effect(() => { if (pin && typeof navigator !== 'undefined' && 'share' in navigator) prepareFile().then(f => { prepared = f; }).catch(() => {}); });
 async function shareFile() {
  if (!pin) return;
  try {
   const file = prepared ?? (prepared = await prepareFile());
   if (file && navigator.canShare?.({files: [file]})) await navigator.share({files: [file], title: pin.title});
   else error = 'This device cannot share files. Use Download in the viewer instead.';
  } catch (e) {
   const name = (e as Error)?.name;
   if (name !== 'AbortError') error = name === 'NotAllowedError' ? 'Image ready. Tap Share file again.' : `Could not share this image (${[name, (e as Error)?.message].filter(Boolean).join(': ').slice(0, 120)}).`;
  }
 }
 async function revoke(l: ShareLink) {
  busy = true; error = '';
  try { await shareApi.revoke(l.id); links = links.filter(x => x.id !== l.id); toast.info('Link revoked. It stops working right away.'); }
  catch (e) { fail(e); } finally { busy = false; }
 }
 const day = (t: number) => new Date(t * 1000).toLocaleDateString(undefined, {day: 'numeric', month: 'short', year: 'numeric'});
 const canDeviceShare = typeof navigator !== 'undefined' && !!navigator.share;
</script>

<Modal portal size="sm" {title} {onClose}>
 <div class="share">
  {#if board}
   <div class="tabs" role="tablist" aria-label="How to share">
    <button role="tab" aria-selected={tab === 'profile'} class:on={tab === 'profile'} onclick={() => { tab = 'profile'; error = ''; }}>To a profile</button>
    <button role="tab" aria-selected={tab === 'link'} class:on={tab === 'link'} onclick={() => { tab = 'link'; error = ''; }}>Get link</button>
   </div>
  {/if}

  {#if tab === 'profile' && board}
   <div role="tabpanel" aria-label="To a profile">
    <p class="muted">{pin ? 'They get a copy in their “Shared with you” board.' : 'They get their own copy of this board, with the curator paused.'}</p>
    {#if targets === null}<p class="muted">Loading profiles…</p>
    {:else if !targets.length}<p class="muted">No other profile in this household can use Mood Boards.</p>
    {:else}
     <ul class="targets">
      {#each targets as t (t.id)}
       <li><button class="target" disabled={busy} onclick={() => send(t)} aria-label={`Send ${what} to ${t.name}`}>
        <Avatar emoji={t.emoji} color={t.color} size="2.2rem" /><span>{t.name}</span><span class="go">{pin ? 'Send image' : 'Send copy'}</span>
       </button></li>
      {/each}
     </ul>
    {/if}
    {#if pin && canDeviceShare}<button class="btn pill" onclick={shareFile}>Share file with an app…</button>{/if}
   </div>
  {:else}
   <div role="tabpanel" aria-label="Get link">
    {#if board}
     {#if !canLink}<p class="muted">Only grown-up profiles can make public links. Share with a profile instead.</p>
     {:else if created}
      <p>Anyone with this link can see {what}{includeSource ? ' and its source links' : ''}. It is shown only once.</p>
      <input class="url" readonly value={created} aria-label="Shareable link" onfocus={(e) => e.currentTarget.select()} />
      <div class="row"><button class="btn primary" onclick={copy}>Copy link</button>{#if canDeviceShare}<button class="btn" onclick={shareLink}>Share…</button>{/if}<button class="btn" onclick={() => { created = ''; }}>Done</button></div>
     {:else}
      <p class="muted">Anyone with the link can view {what} without signing in. Nothing else on your boards is shown.</p>
      <label class="field">Expires
       <select bind:value={expires} aria-label="Link expires">
        <option value="1">After 1 day</option><option value="7">After 7 days</option><option value="30">After 30 days</option><option value="">Never</option>
       </select>
      </label>
      <label class="check"><input type="checkbox" bind:checked={includeSource} />Include links to the original sources</label>
      <button class="btn primary" disabled={busy} onclick={makeLink}>Create link</button>
     {/if}
    {/if}
    {#if links.length}
     <h3>{board ? 'Active links' : 'Your public links'}</h3>
     <ul class="links" aria-label="Public links">
      {#each links as l (l.id)}
       <li>
        <span class="what"><strong>{l.kind === 'pin' ? (l.pin_title || 'Image') : l.board_name}</strong>
         <span class="muted small">…{l.hint} · {l.expired ? 'expired' : l.expires_at ? `until ${day(l.expires_at)}` : 'no expiry'} · {l.views} view{l.views === 1 ? '' : 's'}</span></span>
        <button class="btn pill" disabled={busy} onclick={() => revoke(l)} aria-label={`Revoke link ending ${l.hint}`}>Revoke</button>
       </li>
      {/each}
     </ul>
    {:else if !board}<p class="muted">You have no public links.</p>{/if}
   </div>
  {/if}
  {#if error}<p class="error" role="alert">{error}</p>{/if}
 </div>
</Modal>

<style>
 .share{display:flex;flex-direction:column;gap:.9rem}
 .share p{margin:0}
 .tabs{display:flex;gap:.25rem;padding:.25rem;border-radius:var(--r-pill);background:var(--surface-2)}
 .tabs button{flex:1;border:0;padding:.55rem .8rem;border-radius:var(--r-pill);background:transparent;color:inherit;font:inherit;cursor:pointer}
 .tabs button.on{background:var(--accent);color:var(--accent-ink);font-weight:600}
 [role=tabpanel]{display:flex;flex-direction:column;gap:.8rem}
 .targets,.links{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:.4rem}
 .target{width:100%;display:flex;align-items:center;gap:.7rem;padding:.55rem .7rem;border:1px solid var(--line);border-radius:var(--r-md);background:transparent;color:inherit;font:inherit;cursor:pointer;text-align:left}
 .target:hover:not(:disabled){background:var(--surface-2)}
 .target .go{margin-left:auto;font-size:.85rem;color:var(--muted)}
 .url{width:100%;padding:.6rem .7rem;border-radius:var(--r-md);border:1px solid var(--line);background:var(--surface-2);color:inherit;font:inherit;font-size:.85rem}
 .row{display:flex;flex-wrap:wrap;gap:.5rem}
 .field{display:flex;align-items:center;justify-content:space-between;gap:.7rem}
 .field select{padding:.45rem .6rem;border-radius:var(--r-md);border:1px solid var(--line);background:var(--surface-2);color:inherit;font:inherit}
 .check{display:flex;align-items:center;gap:.5rem}
 h3{margin:.4rem 0 0;font-size:.95rem}
 .links li{display:flex;align-items:center;gap:.6rem;justify-content:space-between;padding:.5rem 0;border-top:1px solid var(--line)}
 .what{display:flex;flex-direction:column;min-width:0}
 .what strong{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .small{font-size:.8rem}
 .error{color:#ff8f8f}
</style>
