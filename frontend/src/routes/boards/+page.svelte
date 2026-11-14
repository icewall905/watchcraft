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
 let preview = $state<BoardPin | null>(null), moving = $state<number | null>(null), moveTo = $state(''), busy = $state<number | null>(null);
 let refreshing = $state(false);
 let importing = $state(false), importOpen = $state(false), pinterestUrl = $state(''), importJob = $state<PinterestImport | null>(null);
 let controller: AbortController | null = null, generation = 0, identity = '', pollUntil = 0;
 const board = $derived(boards.find(b => b.id === selected));
 const allowed = () => session.hubAllowed && session.ready && !session.needsLogin && !session.choosing && !session.profile?.pin_locked;
 const stamp = () => `${session.profileId}:${session.version}`;
 const message = (e: unknown) => e instanceof Error ? e.message : 'Could not load your boards.';
 function clear() { importOpen = false; importing = false; importJob = null; pinterestUrl = ''; controller?.abort(); generation++; boards = []; pins = []; selected = null; preview = null; editing = false; moving = null; saving = false; busy = null; deleteConfirm = false; notice = ''; error = ''; loading = false; more = false; hasMore = false; cursor = null; pollUntil = 0; refreshing = false; }
 async function load(append = false, quiet = false) {
  if (!allowed() || (append && (loading || more || !hasMore || !selected))) return;
  controller?.abort(); controller = new AbortController(); const signal = controller.signal, token = ++generation, owner = stamp();
  if (append) more = true; else if (!quiet) loading = true;
  error = '';
  try {
   const list = await boardsApi.list(signal);
   if (signal.aborted || token !== generation || owner !== stamp() || !allowed()) return;
   boards = list.boards;
   if (!boards.some(b => b.id === selected)) selected = boards[0]?.id ?? null;
   if (!selected) { pins = []; hasMore = false; return; }
   const data = await boardsApi.detail(selected, {saved: savedOnly, cursor: append ? cursor ?? undefined : undefined}, signal);
   if (signal.aborted || token !== generation || owner !== stamp() || !allowed()) return;
   boards = boards.map(b => b.id === data.board.id ? data.board : b);
   const imported = await boardsApi.pinterestStatus(selected, signal);
   if (signal.aborted || token !== generation || owner !== stamp() || !allowed()) return;
   importJob = imported.import;
   if (importJob && ['queued', 'importing'].includes(importJob.status)) pollUntil = Date.now() + 180000;
   const seen = new Set(append ? pins.map(p => p.id) : []);
   const fresh = data.pins.filter(p => { if (seen.has(p.id)) return false; seen.add(p.id); return true; });
   pins = append ? [...pins, ...fresh] : fresh; cursor = data.next_cursor; hasMore = data.has_more;
  } catch (e) { if (!signal.aborted && token === generation && owner === stamp() && allowed()) error = message(e); }
  finally { if (token === generation && owner === stamp()) { loading = false; more = false; } }
 }
 $effect(() => {
  const key = `${session.profileId}:${session.version}:${session.profile?.pin_locked}:${session.needsLogin}:${session.choosing}:${session.ready}:${session.hubAllowed}`;
  if (key !== identity) { identity = key; untrack(() => { clear(); if (allowed()) void load(); }); }
 });
 onMount(() => { const timer = window.setInterval(() => { if (allowed() && Date.now() < pollUntil && document.visibilityState !== 'hidden' && !loading && !more) void load(false, true); }, 10000); return () => { window.clearInterval(timer); controller?.abort(); }; });
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
 function edit(b?: MoodBoard) { editId = b?.id ?? null; name = b?.name ?? ''; interests = b?.interests ?? ''; instructions = b?.instructions ?? ''; enabled = b?.enabled ?? true; interval = b?.interval_hours ?? 12; error = ''; editing = true; }
 async function saveBoard(e: SubmitEvent) {
  e.preventDefault(); if (!allowed() || saving) return; saving = true; const owner = stamp();
  const body: BoardInput = {name: name.trim(), interests: interests.trim(), instructions: instructions.trim(), enabled, interval_hours: Number(interval)};
  try { const result = editId ? await boardsApi.update(editId, body) : await boardsApi.create(body); if (owner !== stamp() || !allowed()) return; selected = result.board.id; editing = false; await load(); notice = editId ? 'Board updated.' : 'Board created. Find images to start filling it.'; }
  catch (e) { if (owner === stamp() && allowed()) error = message(e); }
  finally { if (owner === stamp()) saving = false; }
 }
 async function refresh() { if (!selected || !allowed() || refreshing) return; refreshing = true; const id = selected, owner = stamp(); try { const result = await boardsApi.refresh(id); if (owner !== stamp() || !allowed()) return; notice = result.queued === false ? result.status ?? 'Your curator is already collecting images.' : 'Your curator is looking for new images. You can keep browsing.'; pollUntil = Date.now() + 180000; await load(false, true); } catch (e) { if (owner === stamp()) error = message(e); } finally { if (owner === stamp()) refreshing = false; } }
 async function pinAction(p: BoardPin, patch: {saved?: boolean; hidden?: boolean; board_id?: number}) {
  if (!allowed() || busy !== null) return; busy = p.id; const owner = stamp();
  try { await boardsApi.pin(p.board_id, p.id, patch); if (owner !== stamp() || !allowed()) return; if (patch.hidden || patch.board_id || (savedOnly && patch.saved === false)) pins = pins.filter(v => v.id !== p.id); else pins = pins.map(v => v.id === p.id ? {...v, ...patch} : v); if (preview?.id === p.id) preview = patch.hidden || patch.board_id ? null : {...preview, ...patch}; moving = null; }
  catch (e) { if (owner === stamp()) error = message(e); } finally { if (owner === stamp()) busy = null; }
 }
 let deleteConfirm = $state(false);
 async function removeBoard() { if (!selected || !allowed()) return; const owner = stamp(); try { await boardsApi.remove(selected); if (owner !== stamp() || !allowed()) return; deleteConfirm = false; selected = null; await load(); } catch(e) { if (owner === stamp()) error = message(e); } }
</script>

<svelte:head><title>Mood boards · WatchCraft</title></svelte:head>
<section class="boards-page">
 <header class="page-heading"><div><h1>Mood boards</h1><p class="muted">A place for the things that catch your eye.</p></div><button class="btn primary" onclick={() => edit()}>New board</button></header>
 {#if error}<div class="feedback error" role="alert">{error}<button class="btn" onclick={() => load()}>Try again</button></div>{/if}
 {#if notice}<p class="feedback" role="status">{notice}</p>{/if}
 {#if boards.length}
  <nav class="board-rail" aria-label="Your mood boards">{#each boards as b}<button class="btn" class:chosen={selected === b.id} aria-pressed={selected === b.id} onclick={() => choose(b.id)}>{b.name}<span>{b.pin_count ?? 0}</span></button>{/each}</nav>
 {/if}
 {#if board}
  <div class="board-heading"><div><h2>{board.name}</h2><p class="muted interests">{board.interests}</p></div><div class="tools"><button class="btn" onclick={() => { error = ''; importOpen = true; }}>Import Pinterest</button><button class="btn" class:chosen={savedOnly} aria-pressed={savedOnly} onclick={() => { savedOnly = !savedOnly; void load(); }}>Saved{savedOnly ? ' only' : ''}</button><button class="btn" disabled={refreshing} onclick={refresh}>{refreshing ? 'Starting…' : 'Find images'}</button><button class="btn" aria-label={`Edit ${board.name} curator`} onclick={() => edit(board)}>Edit curator</button></div></div>
  <p class="curator-note muted">{board.enabled ? `Curator checks every ${board.interval_hours} hours.` : 'Automatic discovery paused.'}{#if board.last_status}{' '}Last check: {board.last_status}
 .{/if}</p>{#if board.error}<p class="feedback error" role="status">{board.error}</p>{/if}
  {#if importJob}<p class="feedback" role="status">Pinterest: {importJob.status === 'complete' ? 'import complete' : importJob.status}. {importJob.imported} imported, {importJob.skipped} skipped.{#if importJob.error} {importJob.error}{/if}{#if importJob.status === 'complete'} Latest feed pins only; Pinterest does not expose the whole board here.{/if}</p>{/if}
 {/if}
 {#if loading}<p class="empty" role="status">Gathering your images…</p>
 {:else if !boards.length}<div class="empty"><h2>What inspires you?</h2><p>Give a hobby, interest or idea its own board. Its curator will find images and GIFs for you to collect.</p><button class="btn primary" onclick={() => edit()}>Create your first board</button></div>
 {:else if !pins.length}<div class="empty"><h2>{savedOnly ? 'Your favourites belong here' : 'Make room for inspiration'}</h2><p>{savedOnly ? 'Save an image from this board to find it here later.' : 'Your board is ready. Ask its curator to find images, then save the ones you love.'}</p>{#if !savedOnly}<button class="btn primary" onclick={refresh}>Find images</button>{/if}</div>
 {:else}
  <div class="masonry">{#each pins as pin (pin.id)}<article class="pin"><button class="picture" aria-label={`View ${pin.title}`} onclick={() => { preview = pin; }}><BoardImage url={pin.image_url} alt={pin.title} animated={pin.media_type === 'gif'} /></button><div class="caption"><button class="pin-title" onclick={() => { preview = pin; }}>{pin.title}</button><div class="pin-meta"><span>{pin.attribution ?? ''}</span><button class="btn" class:saved={pin.saved} disabled={busy === pin.id} aria-label={pin.saved ? `Unsave ${pin.title}` : `Save ${pin.title}`} aria-pressed={pin.saved} onclick={() => pinAction(pin, {saved: !pin.saved})}>{pin.saved ? 'Saved' : 'Save'}</button></div></div></article>{/each}</div>
  {#if hasMore}<div class="load-more" bind:this={sentinel}><button class="btn" disabled={more} onclick={() => load(true)}>{more ? 'Loading…' : 'More inspiration'}</button></div>{/if}
 {/if}
</section>

{#if editing}<Modal title={editId ? 'Edit board curator' : 'Create a mood board'} onClose={() => { if (!saving) editing = false; }}>
 <form onsubmit={saveBoard} class="board-form"><label>Board name<input required maxlength="100" bind:value={name} placeholder="My garden ideas" /></label><label>Interests and hobbies<input required maxlength="1000" bind:value={interests} placeholder="Cottage gardens, wildflowers, tiny greenhouses" /><small>Separate interests with commas.</small></label><label>What should your curator look for?<textarea rows="4" maxlength="2000" bind:value={instructions} placeholder="Dresses with heart patterns only. Or: usable sewing patterns for a child’s hooded cape."></textarea><small>Your curator checks images with the configured vision model. Be specific: uncertain matches are excluded. Narrow requests may find fewer images.</small></label><label class="check"><input type="checkbox" bind:checked={enabled} />Find new images automatically</label><label>Check every<select bind:value={interval}><option value={6}>6 hours</option><option value={12}>12 hours</option><option value={24}>day</option><option value={72}>3 days</option><option value={168}>week</option></select></label>{#if error}<p role="alert" class="error">{error}</p>{/if}<div class="form-actions">{#if editId}<button type="button" class="btn danger" onclick={() => { editing = false; deleteConfirm = true; }}>Delete board</button>{/if}<button class="btn" type="button" onclick={() => { editing = false; }}>Cancel</button><button class="btn primary" disabled={saving}>{saving ? 'Saving…' : editId ? 'Save changes' : 'Create board'}</button></div></form>
 </Modal>{/if}
{#if importOpen}<Modal title="Import a Pinterest board" onClose={() => { if (!importing) importOpen = false; }}><form class="board-form" onsubmit={importPinterest}><p>Import up to 25 of the latest pins exposed by a public board’s feed into this board. Images are saved locally with their original Pinterest links. Existing pins are skipped.</p><label>Public board link<input type="url" required maxlength="1000" bind:value={pinterestUrl} placeholder="https://www.pinterest.com/username/board-name/" /></label><small>Private boards, short links and full-history imports need a Pinterest connection and are not supported by this public feed import.</small>{#if error}<p class="error" role="alert">{error}</p>{/if}<div class="form-actions"><button type="button" class="btn" onclick={() => { importOpen = false; }}>Cancel</button><button class="btn primary" disabled={importing}>{importing ? 'Queuing…' : 'Import pins'}</button></div></form></Modal>{/if}
{#if deleteConfirm}<Modal title="Delete this board?" onClose={() => { deleteConfirm = false; }}><p>This removes the board and its saved pins.</p><div class="form-actions"><button class="btn" onclick={() => { deleteConfirm = false; }}>Keep board</button><button class="btn danger" onclick={removeBoard}>Delete board</button></div></Modal>{/if}
{#if preview}<Modal title={preview.title} size="lg" onClose={() => { preview = null; moving = null; }}><div class="preview-image"><BoardImage url={preview.image_url} alt={preview.title} animated={preview.media_type === 'gif'} eager /></div><p class="muted">{preview.attribution ?? ''}{#if preview.license} · {preview.license}{/if}</p><div class="preview-actions"><a href={preview.source_url} target="_blank" rel="noopener noreferrer">View original source</a><button class="btn" disabled={busy === preview.id} onclick={() => preview && pinAction(preview, {saved: !preview.saved})}>{preview.saved ? 'Unsave' : 'Save'}</button>{#if boards.length > 1}<button class="btn" onclick={() => { moving = preview?.id ?? null; moveTo = String(boards.find(b => b.id !== selected)?.id ?? ''); }}>Move to board</button>{/if}<button class="btn" disabled={busy === preview.id} onclick={() => preview && pinAction(preview, {hidden: true})}>Hide image</button></div>{#if moving}<div class="move-form"><label>Destination board<select bind:value={moveTo}>{#each boards.filter(b => b.id !== selected) as b}<option value={b.id}>{b.name}</option>{/each}</select></label><button class="btn" disabled={!moveTo || busy !== null} onclick={() => preview && pinAction(preview, {board_id: Number(moveTo)})}>Move image</button></div>{/if}</Modal>{/if}

<style>
 .boards-page{max-width:1600px;margin:auto;padding:clamp(1rem,3vw,2.5rem)}
 .page-heading,.board-heading{display:flex;align-items:center;justify-content:space-between;gap:1rem;flex-wrap:wrap}
 .page-heading h1{font-size:clamp(2rem,4vw,3rem);letter-spacing:-.045em;margin:0}
 .page-heading p{margin:.5rem 0 0}
 .board-rail{display:flex;gap:.5rem;overflow-x:auto;padding:1.6rem 0 1rem}
 .board-rail button{white-space:nowrap;border-radius:999px;padding:.7rem 1rem}
 .board-rail span{margin-left:.65rem;opacity:.6;font-size:.8em}
 .chosen{background:var(--surface-hi);border-color:var(--accent);color:var(--text)}
 .board-heading{margin-top:.5rem}
 .board-heading h2{font-size:1.35rem;margin:0}
 .interests{margin:.35rem 0;max-width:65ch}
 .tools{display:flex;gap:.45rem;flex-wrap:wrap}
 .curator-note{font-size:.8rem;margin:.65rem 0 1.5rem}
 .masonry{columns:4;column-gap:1rem}
 .pin{break-inside:avoid;margin:0 0 1.25rem;overflow:hidden}
 .picture{display:block;width:100%;padding:0;border:0;background:var(--surface);border-radius:var(--r-lg);overflow:hidden;min-height:5rem}
 .picture :global(img){display:block;width:100%;height:auto}
 .caption{padding:.65rem .25rem 0}
 .pin-title{text-align:left;padding:0;border:0;background:none;color:var(--text);font-weight:600;font-size:.9rem;line-height:1.4;display:-webkit-box;-webkit-line-clamp:2;line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
 .pin-meta{display:flex;align-items:center;justify-content:space-between;gap:.4rem;margin-top:.35rem;font-size:.75rem;color:var(--muted)}
 .pin-meta span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .pin-meta button{font-size:.75rem;padding:.3rem .65rem;border-radius:999px}
 .saved{color:var(--accent)}
 .empty{text-align:center;padding:5rem 1rem;max-width:38rem;margin:auto}
 .empty p{color:var(--muted);line-height:1.7}
 .load-more{text-align:center;padding:2rem}
 .feedback{padding:.8rem 1rem;background:var(--surface);border-radius:var(--r-md);margin:1rem 0}
 .feedback button{margin-left:1rem}
 .error{color:var(--danger,#ff929d)}
 .board-form{display:grid;gap:1rem}
 .board-form label,.move-form label{display:grid;gap:.4rem;font-size:.9rem}
 .board-form small{color:var(--muted)}
 .board-form .check{display:flex;align-items:center;gap:.6rem}
 .check input{width:auto}
 .board-form input:not([type=checkbox]),.board-form textarea,.board-form select,.move-form select{width:100%;background:var(--surface-solid);border:1px solid var(--line-strong);border-radius:var(--r-md);padding:.7rem;color:var(--text)}
 .form-actions,.preview-actions{display:flex;align-items:center;gap:.6rem;justify-content:flex-end;flex-wrap:wrap;margin-top:1rem}
 .danger{color:var(--danger,#ff929d)}
 .form-actions .danger{margin-right:auto}
 .preview-image :global(img){display:block;max-width:100%;height:auto;max-height:65vh;object-fit:contain;margin:auto}
 .move-form{display:flex;align-items:end;gap:.6rem;margin-top:1rem}
 .move-form label{flex:1}
 .preview-actions a{margin-right:auto}button:focus-visible,a:focus-visible{outline:2px solid var(--accent);outline-offset:4px}
 @media(min-width:1500px){.masonry{columns:5}}
 @media(max-width:1100px){.masonry{columns:3}}
 @media(max-width:650px){.masonry{columns:2;column-gap:.65rem}
 .pin{margin-bottom:1rem}
 .tools button{font-size:.8rem;padding:.5rem .65rem}
 .page-heading .primary{padding:.65rem .8rem}
 .curator-note{line-height:1.5}
 .pin-meta{align-items:start}
 .pin-meta span{max-width:6rem}
 .caption{padding:.5rem .1rem 0}
 .preview-actions{justify-content:start}}
</style>
