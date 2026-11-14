<script lang="ts">
 import { onMount, untrack } from 'svelte';
 import { session } from '$lib/stores/session.svelte';
 import Modal from '$lib/components/Modal.svelte';
 import BoardImage from '$lib/hub/BoardImage.svelte';
 import { boardsApi, type MoodBoard, type BoardPin, type BoardInput, type PinterestImport } from '$lib/hub/boards';
 let boards = $state<MoodBoard[]>([]), selected = $state<number | null>(null), pins = $state<BoardPin[]>([]);
 let loading = $state(false), more = $state(false), error = $state(''), notice = $state(''), savedOnly = $state(false);
 let sentinel = $state<HTMLDivElement>();
 let cursor = $state<string | null>(null), hasMore = $state(false), editing = $state(false), editId = $state<number | null>(null);
 let name = $state(''), interests = $state(''), instructions = $state(''), enabled = $state(true), interval = $state(12), saving = $state(false);
 let targetImages = $state(100);
 let preview = $state<BoardPin | null>(null), moving = $state<number | null>(null), moveTo = $state(''), busy = $state<number | null>(null);
 let refreshing = $state(false), clock = $state(Date.now());
 let retryDeadlines = $state<Record<number, number>>({});
 const active = (status?: string) => status === 'queued' || status === 'collecting';
 function rememberRetry(rows: MoodBoard[]) {
  const now = Date.now(); clock = now;
  for (const row of rows) if (typeof row.retry_after === 'number') retryDeadlines[row.id] = now + Math.max(0, row.retry_after) * 1000;
 }
 const activityTitles: Record<string, string> = {queued: 'Curator queued', collecting: 'Curator is checking images', no_matches: 'No matching images this time', error: 'Curator needs attention', partial: 'Search finished with some problems', ok: 'Last search complete', target_reached: 'Image target reached'};
 const activityTitle = (status?: string) => activityTitles[status ?? ''] ?? 'Ready to find images';
 let importing = $state(false), importOpen = $state(false), pinterestUrl = $state(''), importJob = $state<PinterestImport | null>(null);
 let imageOpen = $state(false), imageMode = $state<'manual' | 'reference'>('manual'), imageUrl = $state(''), imageTitle = $state(''), imageFile = $state<File | null>(null), imageBusy = $state(false);
 let controller: AbortController | null = null, generation = 0, identity = '', pollUntil = 0;
 const board = $derived(boards.find(b => b.id === selected));
 const curatorActive = $derived(active(board?.last_status));
 const retrySeconds = $derived(Math.max(0, Math.ceil((((selected ? retryDeadlines[selected] : 0) ?? 0) - clock) / 1000)));
 const findDisabled = $derived(refreshing || curatorActive || retrySeconds > 0);
 const findLabel = $derived(refreshing ? 'Starting…' : board?.last_status === 'queued' ? 'Queued…' : board?.last_status === 'collecting' ? 'Finding images…' : retrySeconds > 0 ? `Find images (${retrySeconds}s)` : 'Find images');
 const allowed = () => session.hubAllowed && session.ready && !session.needsLogin && !session.choosing && !session.profile?.pin_locked;
 const stamp = () => `${session.profileId}:${session.version}`;
 const message = (e: unknown) => e instanceof Error ? e.message : 'Could not load your boards.';
 function clear() { importOpen = false; importing = false; importJob = null; pinterestUrl = ''; imageOpen = false; imageBusy = false; imageUrl = ''; imageTitle = ''; imageFile = null; controller?.abort(); controller = null; generation++; boards = []; pins = []; selected = null; preview = null; editing = false; moving = null; saving = false; busy = null; deleteConfirm = false; notice = ''; error = ''; loading = false; more = false; hasMore = false; cursor = null; pollUntil = 0; refreshing = false; retryDeadlines = {}; }
 async function load(append = false, quiet = false) {
  if (!allowed() || (append && (loading || more || !hasMore || !selected))) return;
  controller?.abort(); controller = new AbortController(); const signal = controller.signal, token = ++generation, owner = stamp();
  if (append) more = true; else if (!quiet) loading = true;
  error = '';
  try {
   const list = await boardsApi.list(signal);
   if (signal.aborted || token !== generation || owner !== stamp() || !allowed()) return;
   boards = list.boards; rememberRetry(boards);
   if (!boards.some(b => b.id === selected)) selected = boards[0]?.id ?? null;
   if (!selected) { pins = []; hasMore = false; return; }
   const data = await boardsApi.detail(selected, {saved: savedOnly, cursor: append ? cursor ?? undefined : undefined}, signal);
   if (signal.aborted || token !== generation || owner !== stamp() || !allowed()) return;
   boards = boards.map(b => b.id === data.board.id ? data.board : b); rememberRetry([data.board]);
   const imported = await boardsApi.pinterestStatus(selected, signal);
   if (signal.aborted || token !== generation || owner !== stamp() || !allowed()) return;
   importJob = imported.import;
   if (importJob && ['queued', 'importing'].includes(importJob.status)) pollUntil = Date.now() + 180000;
   const seen = new Set(append ? pins.map(p => p.id) : []);
   const fresh = data.pins.filter(p => { if (seen.has(p.id)) return false; seen.add(p.id); return true; });
   pins = append ? [...pins, ...fresh] : fresh; cursor = data.next_cursor; hasMore = data.has_more;
  } catch (e) { if (!signal.aborted && token === generation && owner === stamp() && allowed()) error = message(e); }
  finally { if (token === generation && owner === stamp()) { loading = false; more = false; controller = null; } }
 }
 $effect(() => {
  const key = `${session.profileId}:${session.version}:${session.profile?.pin_locked}:${session.needsLogin}:${session.choosing}:${session.ready}:${session.hubAllowed}`;
  if (key !== identity) { identity = key; untrack(() => { clear(); if (allowed()) void load(); }); }
 });
 onMount(() => {
  let lastPoll = 0;
  const timer = window.setInterval(() => {
   clock = Date.now();
   if (allowed() && (active(board?.last_status) || clock < pollUntil) && clock - lastPoll >= 5000 && document.visibilityState !== 'hidden' && !loading && !more && !controller) { lastPoll = clock; void load(false, true); }
  }, 1000);
  const wake = () => { clock = Date.now(); if (document.visibilityState !== 'hidden' && allowed() && (active(board?.last_status) || clock < pollUntil) && !loading && !more && !controller) void load(false, true); };
  document.addEventListener('visibilitychange', wake);
  return () => { window.clearInterval(timer); document.removeEventListener('visibilitychange', wake); controller?.abort(); };
 });
 $effect(() => {
  const node = sentinel;
  if (!node || !hasMore || loading || more) return;
  const observer = new IntersectionObserver(entries => { if (entries.some(e => e.isIntersecting)) void load(true); }, {rootMargin: '400px'});
  observer.observe(node);
  return () => observer.disconnect();
 });
 async function importPinterest(e: SubmitEvent) {
  e.preventDefault(); if (!selected || !allowed() || importing) return;
  importing = true; const id = selected, owner = stamp();
  try { const result = await boardsApi.pinterest(id, pinterestUrl.trim()); if (owner !== stamp() || selected !== id || !allowed()) return; importJob = result.import; importOpen = false; pollUntil = Date.now() + 180000; notice = 'Pinterest import queued. Images will appear as they are saved.'; }
  catch (e) { if (owner === stamp() && selected === id && allowed()) error = message(e); }
  finally { if (owner === stamp() && selected === id) importing = false; }
 }
 function choose(id: number) { importOpen = false; importing = false; selected = id; importJob = null; pins = []; preview = null; notice = ''; void load(); }
 function edit(b?: MoodBoard) { editId = b?.id ?? null; name = b?.name ?? ''; interests = b?.interests ?? ''; instructions = b?.instructions ?? ''; enabled = b?.enabled ?? true; interval = b?.interval_hours ?? 12; targetImages = b?.target_images ?? 100; error = ''; editing = true; }
 async function saveBoard(e: SubmitEvent) {
  e.preventDefault(); if (!allowed() || saving) return; saving = true; const owner = stamp();
  const body: BoardInput = {name: name.trim(), interests: interests.trim(), instructions: instructions.trim(), enabled, interval_hours: Number(interval), target_images: Number(targetImages)};
  try { const result = editId ? await boardsApi.update(editId, body) : await boardsApi.create(body); if (owner !== stamp() || !allowed()) return; selected = result.board.id; editing = false; await load(); notice = editId ? 'Board updated.' : active(result.board.last_status) ? 'Board created. Follow its curator’s progress below.' : 'Board created. Find images to start filling it.'; }
  catch (e) { if (owner === stamp() && allowed()) error = message(e); }
  finally { if (owner === stamp()) saving = false; }
 }
 function openImage(mode: 'manual' | 'reference') { imageMode = mode; imageUrl = ''; imageTitle = ''; imageFile = null; error = ''; imageOpen = true; }
 async function imagePayload(): Promise<{url?: string; data_base64?: string; title?: string}> {
  const title = imageTitle.trim() || undefined;
  if (imageFile) {
   if (!['image/png', 'image/jpeg', 'image/webp', 'image/gif'].includes(imageFile.type)) throw new Error('Choose a PNG, JPEG, WebP or GIF image.');
   if (imageFile.size > 5 * 1024 * 1024) throw new Error('Images must be 5 MB or smaller.');
   const bytes = new Uint8Array(await imageFile.arrayBuffer()); let binary = '';
   for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
   return {data_base64: btoa(binary), title};
  }
  const url = imageUrl.trim();
  if (!url) throw new Error('Add an image URL or choose an image file.');
  try { const parsed = new URL(url); if (parsed.protocol !== 'https:' && parsed.protocol !== 'http:') throw new Error(); } catch { throw new Error('Enter a valid HTTP or HTTPS image URL.'); }
  return {url, title};
 }
 async function submitBoardImage(e: SubmitEvent) {
  e.preventDefault(); if (!selected || !allowed() || imageBusy) return;
  imageBusy = true; error = ''; const id = selected, owner = stamp();
  try {
   const payload = await imagePayload(); if (owner !== stamp() || selected !== id || !allowed()) return;
   if (imageMode === 'reference') {
    const result = await boardsApi.setReference(id, payload);
    if (owner !== stamp() || selected !== id || !allowed()) return;
    boards = boards.map(b => b.id === id ? result.board : b); imageOpen = false; notice = 'Inspiration image saved. Your curator can use it as a visual reference.';
   } else {
    const result = await boardsApi.addImage(id, payload);
    if (owner !== stamp() || selected !== id || !allowed()) return;
    imageOpen = false; notice = 'Image added to the board.'; await load(false, true);
    if (owner !== stamp() || selected !== id || !allowed()) return;
    if (!savedOnly && !pins.some(p => p.id === result.pin.id)) pins = [result.pin, ...pins];
   }
  } catch (e) { if (owner === stamp() && selected === id && allowed()) error = message(e); }
  finally { if (owner === stamp() && selected === id) imageBusy = false; }
 }
 async function deleteReference() {
  if (!selected || !allowed() || imageBusy) return; imageBusy = true; const id = selected, owner = stamp();
  try { const result = await boardsApi.removeReference(id); if (owner !== stamp() || selected !== id || !allowed()) return; boards = boards.map(b => b.id === id ? result.board : b); notice = 'Inspiration image removed.'; }
  catch (e) { if (owner === stamp() && selected === id) error = message(e); }
  finally { if (owner === stamp() && selected === id) imageBusy = false; }
 }
 async function refresh() {
  if (!selected || !allowed() || findDisabled) return;
  refreshing = true; error = ''; notice = ''; const id = selected, owner = stamp();
  try {
   const result = await boardsApi.refresh(id);
   if (owner !== stamp() || selected !== id || !allowed()) return;
   if (result.board) { boards = boards.map(b => b.id === id ? result.board : b); rememberRetry([result.board]); }
   retryDeadlines[id] = Date.now() + Math.max(0, result.retry_after ?? 0) * 1000;
   if (result.queued || result.status === 'collecting') pollUntil = Date.now() + 180000;
   await load(false, true);
  } catch (e) { if (owner === stamp() && selected === id && allowed()) error = message(e); }
  finally { if (owner === stamp() && selected === id) refreshing = false; }
 }

 async function pinAction(p: BoardPin, patch: {saved?: boolean; hidden?: boolean; board_id?: number}) {
  if (!allowed() || busy !== null) return; busy = p.id; const owner = stamp();
  try { await boardsApi.pin(p.board_id, p.id, patch); if (owner !== stamp() || !allowed()) return; if (patch.hidden || patch.board_id || (savedOnly && patch.saved === false)) pins = pins.filter(v => v.id !== p.id); else pins = pins.map(v => v.id === p.id ? {...v, ...patch} : v); if (preview?.id === p.id) preview = patch.hidden || patch.board_id ? null : {...preview, ...patch}; moving = null; }
  catch (e) { if (owner === stamp()) error = message(e); } finally { if (owner === stamp()) busy = null; }
 }
 let deleteConfirm = $state(false);
 let dragging = $state(false);
 const tags = $derived((board?.interests ?? '').split(',').map(t => t.trim()).filter(Boolean).slice(0, 8));
 const target = $derived(Math.max(1, board?.target_images ?? 100));
 const progress = $derived(Math.min(100, Math.round(((board?.pin_count ?? 0) / target) * 100)));
 const tone = $derived(curatorActive ? 'busy' : board?.last_status === 'error' ? 'bad' : board?.last_status === 'ok' || board?.last_status === 'target_reached' ? 'good' : 'idle');
 function pickFile(file: File | null | undefined) { imageFile = file ?? null; if (imageFile) imageUrl = ''; }
 function dropFile(e: DragEvent) { e.preventDefault(); dragging = false; pickFile(e.dataTransfer?.files?.[0]); }
 async function removeBoard() { if (!selected || !allowed()) return; const owner = stamp(); try { await boardsApi.remove(selected); if (owner !== stamp() || !allowed()) return; deleteConfirm = false; selected = null; await load(); } catch(e) { if (owner === stamp()) error = message(e); } }
</script>

<svelte:head><title>Mood boards · WatchCraft</title></svelte:head>
{#snippet icon(name: string)}
 <svg class="i" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  {#if name === 'plus'}<path d="M12 5v14M5 12h14"/>
  {:else if name === 'spark'}<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 17l.7 1.8 1.8.7-1.8.7L19 22l-.7-1.8-1.8-.7 1.8-.7z"/>
  {:else if name === 'image'}<rect x="3" y="4" width="18" height="16" rx="3"/><circle cx="9" cy="10" r="2"/><path d="M21 16l-5-5-9 9"/>
  {:else if name === 'eye'}<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>
  {:else if name === 'pin'}<path d="M12 17v5M8 3h8l-1 6 3 4H6l3-4z"/>
  {:else if name === 'heart'}<path d="M20.8 5.6a5 5 0 0 0-7.1 0L12 7.3l-1.7-1.7a5 5 0 1 0-7.1 7.1L12 21.5l8.8-8.8a5 5 0 0 0 0-7.1z"/>
  {:else if name === 'gear'}<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>
  {:else if name === 'upload'}<path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 16v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>
  {:else if name === 'link'}<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>{/if}
 </svg>
{/snippet}

<section class="boards-page">
 <header class="page-heading">
  <div><p class="eyebrow">Your collections</p><h1>Mood boards</h1><p class="lede">A cosy place for everything that catches your eye.</p></div>
  <button class="btn primary new-board" onclick={() => edit()}>{@render icon('plus')}New board</button>
 </header>
 {#if error && !editing && !imageOpen && !importOpen}<div class="feedback error" role="alert"><span>{error}</span><button class="btn" onclick={() => load()}>Try again</button></div>{/if}
 {#if notice}<p class="feedback good" role="status">{notice}<button class="dismiss" aria-label="Dismiss message" onclick={() => { notice = ''; }}>×</button></p>{/if}

 {#if boards.length}
  <nav class="board-rail" aria-label="Your mood boards">
   {#each boards as b (b.id)}
    <button class="board-chip" class:chosen={selected === b.id} aria-pressed={selected === b.id} onclick={() => choose(b.id)}>
     <span class="cover">{#if b.cover_url}<BoardImage url={b.cover_url} alt="" />{:else}<span class="cover-letter">{b.name.slice(0, 1)}</span>{/if}</span>
     <span class="chip-text"><span class="chip-name">{b.name}</span><span class="chip-count">{b.pin_count ?? 0} images</span></span>
    </button>
   {/each}
  </nav>
 {/if}

 {#if board}
  <section class="board-hero">
   <div class="hero-main">
    <h2>{board.name}</h2>
    {#if tags.length}<ul class="tags" aria-label="Interests">{#each tags as t}<li>{t}</li>{/each}</ul>{/if}
    <div class="goal">
     <div class="goal-text"><span class="image-count">{board.pin_count ?? 0} / {board.target_images ?? 100} images</span><span class="muted">{board.enabled ? `Curator checks every ${board.interval_hours} hours.` : 'Automatic discovery paused.'}</span></div>
     <div class="bar" role="progressbar" aria-label="Images toward target" aria-valuemin="0" aria-valuemax={target} aria-valuenow={board.pin_count ?? 0}><span style:width="{progress}%"></span></div>
    </div>
    <div class="tools" role="toolbar" aria-label="Board actions">
     <button class="btn primary find" disabled={findDisabled} onclick={refresh}>{@render icon('spark')}{findLabel}</button>
     <button class="btn pill" onclick={() => openImage('manual')}>{@render icon('plus')}Add image</button>
     <button class="btn pill" onclick={() => openImage('reference')}>{@render icon('eye')}{board.reference_url ? 'Change inspiration image' : 'Add inspiration image'}</button>
     <button class="btn pill" onclick={() => { error = ''; importOpen = true; }}>{@render icon('pin')}Import Pinterest</button>
     <span class="spacer"></span>
     <div class="segmented" role="group" aria-label="Show">
      <button class:on={!savedOnly} aria-pressed={!savedOnly} onclick={() => { if (savedOnly) { savedOnly = false; void load(); } }}>All</button>
      <button class:on={savedOnly} aria-pressed={savedOnly} onclick={() => { if (!savedOnly) { savedOnly = true; void load(); } }}>{@render icon('heart')}Saved{savedOnly ? ' only' : ''}</button>
     </div>
     <button class="btn pill icon-only" aria-label={`Edit ${board.name} curator`} title="Edit curator" onclick={() => edit(board)}>{@render icon('gear')}</button>
    </div>
   </div>
   {#if board.reference_url}
    <section class="reference-image" aria-label="Mood board inspiration image">
     <div class="ref-pic"><BoardImage url={board.reference_url} alt={board.reference_name || `${board.name} inspiration`} eager /></div>
     <div class="ref-text"><strong>Visual inspiration</strong>{#if board.reference_name}<span class="muted">{board.reference_name}</span>{/if}<p class="muted">Guides the curator's taste. It isn't added as a pin.</p><button class="link-btn" disabled={imageBusy} onclick={deleteReference}>Remove inspiration</button></div>
    </section>
   {/if}
  </section>

  <section class="curator-activity tone-{tone}" aria-label="Board curator activity" aria-busy={curatorActive}>
   <span class="dot" aria-hidden="true"></span>
   <div class="activity-body" role="status" aria-live="polite"><strong>{activityTitle(board.last_status)}</strong>
    {#if curatorActive}<p>{board.last_status === 'queued' ? 'Waiting for the curator to start. This page updates automatically.' : 'Searching sources and checking images against your instructions with vision. Results appear here automatically.'}</p>
    {:else if board.last_result}<p>{board.last_result}</p>
    {:else if board.last_status === 'no_matches'}<p>The search finished, but no images met your instructions. Try another search later or edit the curator to broaden its search.</p>
    {:else if board.last_status === 'target_reached'}<p>This board reached its {board.target_images}-image target. Raise the target in the curator settings to keep collecting.</p>
    {:else if board.last_status === 'ok'}<p>The last search finished. {board.pin_count} images are on this board.</p>
    {:else if !board.last_status}<p>Ask your curator to search for images, or leave automatic discovery enabled.</p>{/if}
    {#if board.error}<p class="error">{board.error}</p>{/if}
    {#if !curatorActive && retrySeconds > 0}<p class="retry">You can search again in {retrySeconds} seconds.</p>{/if}
   </div>
   <a class="activity-link" href="/team">Activity</a>
  </section>
  {#if importJob}<p class="feedback" role="status">Pinterest: {importJob.status === 'complete' ? 'import complete' : importJob.status}. {importJob.imported} imported, {importJob.skipped} skipped.{#if importJob.error} {importJob.error}{/if}{#if importJob.status === 'complete'} Latest feed pins only; Pinterest does not expose the whole board here.{/if}</p>{/if}
 {/if}

 {#if loading}<div class="masonry skeleton" role="status" aria-label="Gathering your images…">{#each [14, 20, 11, 17, 22, 13, 18, 12] as h}<div class="ghost" style:height="{h}rem"></div>{/each}</div>
 {:else if !boards.length}<div class="empty"><div class="empty-art" aria-hidden="true"><span></span><span></span><span></span></div><h2>What inspires you?</h2><p>Give a hobby, interest or idea its own board. Its curator will find images and GIFs for you to collect.</p><button class="btn primary" onclick={() => edit()}>Create your first board</button></div>
 {:else if !pins.length}<div class="empty"><div class="empty-art" aria-hidden="true"><span></span><span></span><span></span></div><h2>{savedOnly ? 'Your favourites belong here' : 'Make room for inspiration'}</h2><p>{savedOnly ? 'Save an image from this board to find it here later.' : curatorActive ? 'Your curator is working. Matching images will appear automatically.' : board?.last_status === 'no_matches' ? 'No matching images were found. You can refine your curator’s instructions or try another search when it is ready.' : board?.last_status === 'error' ? 'Your curator could not finish. See its activity above for the reason.' : 'Your board is ready. Ask its curator to find images, add your own, then save the ones you love.'}</p>{#if !savedOnly}<button class="btn primary" disabled={findDisabled} onclick={refresh}>{findLabel}</button>{/if}</div>
 {:else}
  <div class="masonry">
   {#each pins as pin (pin.id)}
    <article class="pin">
     <div class="frame">
      <button class="picture" aria-label={`View ${pin.title}`} onclick={() => { preview = pin; }}><BoardImage url={pin.image_url} alt={pin.title} animated={pin.media_type === 'gif'} /></button>
      {#if pin.media_type === 'gif'}<span class="badge">GIF</span>{/if}
      <button class="save" class:saved={pin.saved} disabled={busy === pin.id} aria-label={pin.saved ? `Unsave ${pin.title}` : `Save ${pin.title}`} aria-pressed={pin.saved} onclick={() => pinAction(pin, {saved: !pin.saved})}>{@render icon('heart')}<span>{pin.saved ? 'Saved' : 'Save'}</span></button>
     </div>
     <div class="caption"><button class="pin-title" onclick={() => { preview = pin; }}>{pin.title}</button>{#if pin.attribution}<span class="pin-meta">{pin.attribution}</span>{/if}</div>
    </article>
   {/each}
  </div>
  {#if hasMore}<div class="load-more" bind:this={sentinel}><button class="btn pill" disabled={more} onclick={() => load(true)}>{more ? 'Loading…' : 'More inspiration'}</button></div>{/if}
 {/if}
</section>

{#if editing}<Modal title={editId ? 'Edit board curator' : 'Create a mood board'} onClose={() => { if (!saving) editing = false; }}>
 <form onsubmit={saveBoard} class="board-form">
  <label>Board name<input required maxlength="100" bind:value={name} placeholder="My garden ideas" /></label>
  <label>Interests and hobbies<input required maxlength="1000" bind:value={interests} placeholder="Cottage gardens, wildflowers, tiny greenhouses" /><small>Separate interests with commas.</small></label>
  <label>What should your curator look for?<textarea rows="4" maxlength="2000" bind:value={instructions} placeholder="Dresses with heart patterns only. Or: usable sewing patterns for a child’s hooded cape."></textarea><small>Your curator checks images with the configured vision model. Be specific: uncertain matches are excluded. Narrow requests may find fewer images.</small></label>
  <label>Image target<span class="target-row"><input type="range" min="10" max="1000" step="10" aria-hidden="true" tabindex="-1" bind:value={targetImages} /><input class="target-num" type="number" min="1" max="1000" step="1" required bind:value={targetImages} /></span><small>The curator keeps searching until the board holds this many images (up to 1,000).</small></label>
  <div class="form-grid"><label class="check"><input type="checkbox" bind:checked={enabled} />Find new images automatically</label><label>Check every<select bind:value={interval}><option value={6}>6 hours</option><option value={12}>12 hours</option><option value={24}>day</option><option value={72}>3 days</option><option value={168}>week</option></select></label></div>
  {#if error}<p role="alert" class="error">{error}</p>{/if}
  <div class="form-actions">{#if editId}<button type="button" class="btn danger" onclick={() => { editing = false; deleteConfirm = true; }}>Delete board</button>{/if}<button class="btn" type="button" onclick={() => { editing = false; }}>Cancel</button><button class="btn primary" disabled={saving}>{saving ? 'Saving…' : editId ? 'Save changes' : 'Create board'}</button></div>
 </form>
</Modal>{/if}

{#if imageOpen}<Modal title={imageMode === 'reference' ? 'Add visual inspiration' : 'Add an image to this board'} onClose={() => { if (!imageBusy) imageOpen = false; }}>
 <form class="board-form" onsubmit={submitBoardImage}>
  <p class="muted">{imageMode === 'reference' ? 'Show the curator an example of the look you love. It guides searches and stays separate from the board pins.' : 'Save your own find straight to this board. GIFs are welcome.'}</p>
  <label class="dropzone" class:dragging class:has-file={!!imageFile} ondragover={(e) => { e.preventDefault(); dragging = true; }} ondragleave={() => { dragging = false; }} ondrop={dropFile}>
   {@render icon('upload')}
   <span class="dz-title">{imageFile ? imageFile.name : 'Drop an image here or tap to choose'}</span>
   <span class="dz-hint">PNG, JPEG, WebP or GIF · up to 5 MB</span>
   <input class="sr-only" type="file" aria-label="Image file" accept="image/png,image/jpeg,image/webp,image/gif" onchange={(e) => pickFile(e.currentTarget.files?.[0])} />
  </label>
  <p class="or"><span>or paste a link</span></p>
  <label class="with-icon">{@render icon('link')}<input type="url" aria-label="Image URL" maxlength="2000" bind:value={imageUrl} placeholder="https://example.com/image.jpg" disabled={!!imageFile} /></label>
  {#if imageFile}<button type="button" class="link-btn" onclick={() => pickFile(null)}>Use a link instead</button>{/if}
  {#if imageMode === 'manual'}<label>Caption<input maxlength="300" bind:value={imageTitle} placeholder="Optional image title" /></label>{/if}
  {#if error}<p class="error" role="alert">{error}</p>{/if}
  <div class="form-actions"><button type="button" class="btn" disabled={imageBusy} onclick={() => { imageOpen = false; }}>Cancel</button><button class="btn primary" disabled={imageBusy || (!imageUrl.trim() && !imageFile)}>{imageBusy ? 'Saving…' : imageMode === 'reference' ? 'Save inspiration' : 'Add image'}</button></div>
 </form>
</Modal>{/if}

{#if importOpen}<Modal title="Import a Pinterest board" onClose={() => { if (!importing) importOpen = false; }}><form class="board-form" onsubmit={importPinterest}><p class="muted">Import up to 25 of the latest pins exposed by a public board’s feed into this board. Images are saved locally with their original Pinterest links. Existing pins are skipped.</p><label>Public board link<input type="url" required maxlength="1000" bind:value={pinterestUrl} placeholder="https://www.pinterest.com/username/board-name/" /></label><small>Private boards, short links and full-history imports need a Pinterest connection and are not supported by this public feed import.</small>{#if error}<p class="error" role="alert">{error}</p>{/if}<div class="form-actions"><button type="button" class="btn" onclick={() => { importOpen = false; }}>Cancel</button><button class="btn primary" disabled={importing}>{importing ? 'Queuing…' : 'Import pins'}</button></div></form></Modal>{/if}
{#if deleteConfirm}<Modal title="Delete this board?" onClose={() => { deleteConfirm = false; }}><p>This removes the board and its saved pins.</p><div class="form-actions"><button class="btn" onclick={() => { deleteConfirm = false; }}>Keep board</button><button class="btn danger" onclick={removeBoard}>Delete board</button></div></Modal>{/if}

{#if preview}<Modal title={preview.title} size="lg" onClose={() => { preview = null; moving = null; }}>
 <div class="preview">
  <div class="preview-image"><BoardImage url={preview.image_url} alt={preview.title} animated={preview.media_type === 'gif'} eager /></div>
  <aside class="preview-side">
   <button class="btn primary save-big" class:saved={preview.saved} disabled={busy === preview.id} onclick={() => preview && pinAction(preview, {saved: !preview.saved})}>{@render icon('heart')}{preview.saved ? 'Unsave' : 'Save'}</button>
   <p class="credit">{preview.attribution ?? ''}{#if preview.license} · {preview.license}{/if}</p>
   {#if /^https?:\/\//i.test(preview.source_url)}<a class="source" href={preview.source_url} target="_blank" rel="noopener noreferrer">{@render icon('link')}View original source</a>{:else}<span class="muted source">{@render icon('upload')}Uploaded image</span>{/if}
   <div class="preview-actions">
    {#if boards.length > 1}<button class="btn pill" onclick={() => { moving = preview?.id ?? null; moveTo = String(boards.find(b => b.id !== selected)?.id ?? ''); }}>Move to board</button>{/if}
    <button class="btn pill" disabled={busy === preview.id} onclick={() => preview && pinAction(preview, {hidden: true})}>Hide image</button>
   </div>
   {#if moving}<div class="move-form"><label>Destination board<select bind:value={moveTo}>{#each boards.filter(b => b.id !== selected) as b}<option value={b.id}>{b.name}</option>{/each}</select></label><button class="btn primary" disabled={!moveTo || busy !== null} onclick={() => preview && pinAction(preview, {board_id: Number(moveTo)})}>Move image</button></div>{/if}
  </aside>
 </div>
</Modal>{/if}

<style>
 .boards-page{--mb-rose:#ff8fab;--mb-peach:#ffb547;--mb-card:rgba(255,255,255,.04);max-width:1680px;margin:auto;padding:clamp(1rem,3vw,2.5rem);overflow-x:clip}
 .i{width:1.05em;height:1.05em;flex:none}
 .sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}

 .page-heading{display:flex;align-items:flex-end;justify-content:space-between;gap:1rem;flex-wrap:wrap}
 .eyebrow{margin:0 0 .3rem;font-size:.75rem;letter-spacing:.14em;text-transform:uppercase;color:var(--mb-rose);font-weight:700}
 .page-heading h1{font-size:clamp(2rem,4.5vw,3.2rem);letter-spacing:-.045em;margin:0;line-height:1;background:linear-gradient(100deg,var(--text) 30%,var(--mb-rose) 75%,var(--mb-peach));-webkit-background-clip:text;background-clip:text;color:transparent}
 .lede{margin:.55rem 0 0;color:var(--muted)}
 .new-board,.find,.pill{gap:.45rem}
 .new-board{border-radius:var(--r-pill);padding:.7rem 1.2rem;box-shadow:0 10px 30px -12px var(--accent-glow)}

 .board-rail{display:flex;gap:.6rem;overflow-x:auto;padding:1.5rem .1rem 1.1rem;scrollbar-width:none;scroll-snap-type:x proximity}
 .board-rail::-webkit-scrollbar{display:none}
 .board-chip{display:flex;align-items:center;gap:.65rem;padding:.35rem 1rem .35rem .35rem;border-radius:var(--r-pill);border:1px solid var(--line);background:var(--mb-card);color:var(--text);white-space:nowrap;cursor:pointer;scroll-snap-align:start;transition:background .2s var(--ease),border-color .2s var(--ease),transform .2s var(--ease)}
 .board-chip:hover{background:var(--surface-hi);transform:translateY(-1px)}
 .board-chip.chosen{border-color:var(--mb-rose);background:linear-gradient(120deg,rgba(255,143,171,.18),rgba(255,181,71,.12))}
 .cover{width:2.6rem;height:2.6rem;border-radius:50%;overflow:hidden;flex:none;background:linear-gradient(135deg,var(--mb-rose),var(--mb-peach));display:grid;place-items:center}
 .cover :global(.media),.cover :global(img){width:100%;height:100%;object-fit:cover}
 .cover :global(.placeholder){min-height:0;padding:0;font-size:0}
 .cover-letter{font-weight:800;color:var(--accent-ink);text-transform:uppercase}
 .chip-text{display:grid;text-align:left;line-height:1.2}
 .chip-name{font-weight:650;font-size:.92rem}
 .chip-count{font-size:.72rem;color:var(--muted)}

 .board-hero{display:grid;grid-template-columns:1fr auto;gap:1.25rem;align-items:start;padding:1.4rem;border-radius:var(--r-lg);background:linear-gradient(135deg,rgba(255,143,171,.08),rgba(157,134,255,.06) 60%,transparent),var(--surface);border:1px solid var(--line)}
 .hero-main,.board-hero>*{min-width:0}
 .board-hero h2{font-size:clamp(1.4rem,2.6vw,2rem);letter-spacing:-.03em;margin:0}
 .tags{display:flex;flex-wrap:wrap;gap:.35rem;list-style:none;margin:.7rem 0 0;padding:0}
 .tags li{font-size:.78rem;padding:.25rem .65rem;border-radius:var(--r-pill);background:rgba(255,255,255,.06);border:1px solid var(--line);color:var(--muted)}
 .goal{margin:1rem 0 0;max-width:34rem}
 .goal-text{display:flex;justify-content:space-between;gap:1rem;flex-wrap:wrap;font-size:.8rem}
 .image-count{font-weight:700;font-variant-numeric:tabular-nums}
 .bar{height:.4rem;border-radius:var(--r-pill);background:rgba(255,255,255,.08);margin-top:.45rem;overflow:hidden}
 .bar span{display:block;height:100%;border-radius:inherit;background:linear-gradient(90deg,var(--mb-rose),var(--mb-peach));transition:width .6s var(--ease)}
 .tools{display:flex;gap:.45rem;flex-wrap:wrap;align-items:center;margin-top:1.15rem}
 .tools .btn{border-radius:var(--r-pill)}
 .spacer{flex:1}
 .icon-only{width:2.6rem;padding:0;justify-content:center}
 .segmented{display:inline-flex;padding:.2rem;border-radius:var(--r-pill);background:rgba(0,0,0,.25);border:1px solid var(--line)}
 .segmented button{display:inline-flex;align-items:center;gap:.35rem;border:0;background:none;color:var(--muted);padding:.45rem .9rem;border-radius:var(--r-pill);font:inherit;font-size:.85rem;cursor:pointer}
 .segmented button.on{background:var(--surface-hi);color:var(--text)}

 .reference-image{display:flex;gap:.9rem;align-items:center;max-width:22rem;padding:.7rem;border-radius:var(--r-md);background:rgba(0,0,0,.2);border:1px solid var(--line)}
 .ref-pic{width:5.5rem;height:5.5rem;border-radius:var(--r-sm);overflow:hidden;flex:none;background:var(--surface-solid)}
 .ref-pic :global(.media),.ref-pic :global(img){width:100%;height:100%;object-fit:cover}
 .ref-pic :global(.placeholder){min-height:5.5rem;padding:.3rem}
 .ref-text{display:grid;gap:.15rem;font-size:.85rem}
 .ref-text p{margin:.2rem 0;font-size:.78rem;line-height:1.4}
 .link-btn{justify-self:start;border:0;background:none;padding:0;color:var(--mb-rose);font:inherit;font-size:.8rem;cursor:pointer;text-decoration:underline;text-underline-offset:3px}

 .curator-activity{display:flex;gap:.8rem;align-items:flex-start;padding:.85rem 1.1rem;margin:1rem 0 1.4rem;border-radius:var(--r-md);background:var(--mb-card);border:1px solid var(--line)}
 .dot{width:.6rem;height:.6rem;border-radius:50%;margin-top:.4rem;flex:none;background:var(--faint)}
 .tone-busy .dot{background:var(--info);animation:pulse 1.4s ease-in-out infinite}
 .tone-good .dot{background:var(--ok)}
 .tone-bad .dot{background:var(--bad)}
 @keyframes pulse{0%,100%{box-shadow:0 0 0 0 rgba(108,198,255,.6)}50%{box-shadow:0 0 0 .45rem rgba(108,198,255,0)}}
 @media(prefers-reduced-motion:reduce){.tone-busy .dot{animation:none}}
 .activity-body{flex:1;min-width:0;font-size:.9rem}
 .activity-body p{margin:.25rem 0 0;color:var(--muted);line-height:1.5}
 .activity-link{font-size:.8rem;color:var(--muted);white-space:nowrap}
 .retry{font-size:.8rem}

 .masonry{columns:5 15rem;column-gap:1rem}
 .pin{break-inside:avoid;margin:0 0 1.2rem;animation:rise .45s var(--ease) both}
 @keyframes rise{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
 @media(prefers-reduced-motion:reduce){.pin{animation:none}}
 .frame{position:relative;border-radius:var(--r-lg);overflow:hidden;background:var(--surface);isolation:isolate}
 .picture{display:block;width:100%;padding:0;border:0;background:none;cursor:zoom-in;min-height:5rem}
 .picture :global(img){display:block;width:100%;height:auto;transition:transform .5s var(--ease),filter .3s}
 .frame::after{content:'';position:absolute;inset:0;background:linear-gradient(180deg,rgba(0,0,0,.35),transparent 30%);opacity:0;transition:opacity .25s;pointer-events:none}
 .frame:hover::after,.frame:focus-within::after{opacity:1}
 .frame:hover .picture :global(img){transform:scale(1.03)}
 .badge{position:absolute;left:.6rem;bottom:.6rem;font-size:.65rem;font-weight:800;letter-spacing:.08em;padding:.2rem .45rem;border-radius:.4rem;background:rgba(0,0,0,.6);color:#fff;pointer-events:none}
 .save{position:absolute;top:.6rem;right:.6rem;z-index:1;display:inline-flex;align-items:center;gap:.35rem;padding:.5rem .85rem;border:0;border-radius:var(--r-pill);background:var(--mb-rose);color:#2a0612;font:inherit;font-weight:700;font-size:.8rem;cursor:pointer;opacity:0;transform:translateY(-4px);transition:opacity .2s,transform .2s var(--ease),background .2s}
 .frame:hover .save,.frame:focus-within .save,.save.saved{opacity:1;transform:none}
 .save.saved{background:rgba(20,17,46,.85);color:var(--mb-rose)}
 .save.saved :global(.i){fill:currentColor}
 @media(hover:none){.save{opacity:1;transform:none;padding:.45rem;font-size:0;gap:0}.save :global(.i){width:1.1rem;height:1.1rem}}
 .caption{padding:.5rem .3rem 0;display:grid;gap:.1rem}
 .pin-title{text-align:left;padding:0;border:0;background:none;color:var(--text);font:inherit;font-weight:600;font-size:.88rem;line-height:1.35;cursor:pointer;display:-webkit-box;-webkit-line-clamp:2;line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
 .pin-meta{font-size:.72rem;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .skeleton .ghost{break-inside:avoid;margin:0 0 1.2rem;border-radius:var(--r-lg);background:linear-gradient(100deg,var(--surface) 30%,var(--surface-hi) 50%,var(--surface) 70%);background-size:200% 100%;animation:shimmer 1.6s linear infinite}
 @keyframes shimmer{to{background-position:-200% 0}}

 .empty{text-align:center;padding:4rem 1rem;max-width:36rem;margin:auto}
 .empty h2{letter-spacing:-.03em}
 .empty p{color:var(--muted);line-height:1.7}
 .empty-art{position:relative;width:9rem;height:6.5rem;margin:0 auto 1.4rem}
 .empty-art span{position:absolute;width:4.4rem;height:5.6rem;border-radius:1rem;background:linear-gradient(135deg,var(--mb-rose),var(--mb-peach));opacity:.85;box-shadow:var(--shadow-lg)}
 .empty-art span:nth-child(1){left:0;top:.8rem;transform:rotate(-10deg);opacity:.45}
 .empty-art span:nth-child(2){left:2.3rem;top:0;background:linear-gradient(135deg,var(--ai),var(--mb-rose))}
 .empty-art span:nth-child(3){right:0;top:.9rem;transform:rotate(9deg);opacity:.6;background:linear-gradient(135deg,var(--info),var(--ai))}
 .load-more{text-align:center;padding:2rem}

 .feedback{display:flex;align-items:center;justify-content:space-between;gap:1rem;padding:.75rem 1rem;background:var(--surface);border:1px solid var(--line);border-radius:var(--r-md);margin:1rem 0}
 .feedback.good{border-color:rgba(61,220,151,.35)}
 .dismiss{border:0;background:none;color:var(--muted);font-size:1.2rem;cursor:pointer;line-height:1}
 .error{color:var(--danger,#ff929d)}

 .board-form{display:grid;gap:1rem}
 .board-form p{margin:0}
 .board-form label,.move-form label{display:grid;gap:.4rem;font-size:.9rem}
 .board-form small{color:var(--muted)}
 .board-form .check{display:flex;align-items:center;gap:.6rem}
 .check input{width:auto}
 .form-grid{display:grid;grid-template-columns:1fr 1fr;gap:1rem;align-items:end}
 .board-form input:not([type=checkbox]):not([type=range]),.board-form textarea,.board-form select,.move-form select{width:100%;background:var(--surface-solid);border:1px solid var(--line-strong);border-radius:var(--r-md);padding:.7rem;color:var(--text);font:inherit}
 .board-form input:focus,.board-form textarea:focus,.board-form select:focus{outline:2px solid var(--mb-rose);outline-offset:1px}
 .target-row{display:flex;align-items:center;gap:.8rem}
 .target-row input[type=range]{flex:1;accent-color:var(--mb-rose)}
 .board-form .target-num{width:6rem;flex:none;text-align:center;font-variant-numeric:tabular-nums}
 .dropzone{display:grid!important;place-items:center;gap:.35rem!important;text-align:center;padding:1.6rem 1rem;border:2px dashed var(--line-strong);border-radius:var(--r-lg);background:rgba(255,255,255,.02);cursor:pointer;transition:border-color .2s,background .2s}
 .dropzone :global(.i){width:1.8rem;height:1.8rem;color:var(--mb-rose)}
 .dropzone:hover,.dropzone.dragging{border-color:var(--mb-rose);background:rgba(255,143,171,.07)}
 .dropzone.has-file{border-style:solid;border-color:var(--ok)}
 .dropzone:focus-within{outline:2px solid var(--mb-rose);outline-offset:2px}
 .dz-title{font-weight:650;word-break:break-all}
 .dz-hint{font-size:.78rem;color:var(--muted)}
 .or{display:flex;align-items:center;gap:.8rem;color:var(--muted);font-size:.8rem;margin:-.3rem 0!important}
 .or::before,.or::after{content:'';flex:1;height:1px;background:var(--line-strong)}
 .with-icon{position:relative;display:block!important}
 .with-icon :global(.i){position:absolute;left:.8rem;top:50%;transform:translateY(-50%);color:var(--muted)}
 .board-form .with-icon input{padding-left:2.4rem}
 .form-actions{display:flex;align-items:center;gap:.6rem;justify-content:flex-end;flex-wrap:wrap;margin-top:.5rem}
 .form-actions .btn{border-radius:var(--r-pill)}
 .form-actions .danger{margin-right:auto}

 .preview{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(14rem,1fr);gap:1.5rem;align-items:start}
 .preview-image{border-radius:var(--r-lg);overflow:hidden;background:rgba(0,0,0,.3)}
 .preview-image :global(img){display:block;max-width:100%;height:auto;max-height:70vh;object-fit:contain;margin:auto}
 .preview-side{display:grid;gap:.9rem;align-content:start}
 .save-big{justify-content:center;gap:.5rem;border-radius:var(--r-pill);padding:.8rem 1.2rem;background:var(--mb-rose);color:#2a0612}
 .save-big.saved{background:var(--surface-hi);color:var(--mb-rose)}
 .save-big.saved :global(.i){fill:currentColor}
 .credit{margin:0;color:var(--muted);font-size:.85rem;line-height:1.5}
 .source{display:inline-flex;align-items:center;gap:.45rem;font-size:.9rem}
 .preview-actions{display:flex;gap:.5rem;flex-wrap:wrap}
 .move-form{display:grid;gap:.6rem}
 button:focus-visible,a:focus-visible{outline:2px solid var(--mb-rose);outline-offset:3px}

 @media(max-width:900px){
  .board-hero{grid-template-columns:1fr}
  .reference-image{max-width:none}
  .preview{grid-template-columns:1fr}
 }
 @media(max-width:650px){
  .boards-page{padding:1rem .75rem}
  .page-heading{align-items:center}
  .eyebrow{display:none}
  .new-board{padding:.6rem .9rem}
  .board-hero{padding:1rem;border-radius:var(--r-md)}
  .tools{flex-wrap:nowrap;overflow-x:auto;margin:1rem -1rem 0;padding:0 1rem .2rem;scrollbar-width:none}
  .tools::-webkit-scrollbar{display:none}
  .tools .btn,.segmented{flex:none;font-size:.82rem}
  .spacer{display:none}
  .goal-text{flex-direction:column;gap:.15rem}
  .page-heading h1{font-size:2.1rem}
  .lede{font-size:.9rem}
  .masonry{columns:2;column-gap:.6rem}
  .pin{margin-bottom:.8rem}
  .frame{border-radius:var(--r-md)}
  .caption{padding:.4rem .15rem 0}
  .pin-title{font-size:.8rem}
  .form-grid{grid-template-columns:1fr}
  .curator-activity{padding:.75rem .85rem}
 }
</style>
