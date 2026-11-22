<script lang="ts">
 import { onMount, untrack } from 'svelte';
 import { page } from '$app/state';
 import { goto } from '$app/navigation';
 import { session } from '$lib/stores/session.svelte';
 import Modal from '$lib/components/Modal.svelte';
 import BoardImage from '$lib/hub/BoardImage.svelte';
 import PinterestSearch from '$lib/hub/PinterestSearch.svelte';
 import PinViewer from '$lib/hub/PinViewer.svelte';
 import PinColumns from '$lib/hub/PinColumns.svelte';
 import { preloadImages, stopPreloading } from '$lib/hub/preload';
 import ShareDialog from '$lib/hub/ShareDialog.svelte';
 import BoardSources from '$lib/hub/BoardSources.svelte';
 import ShareNotices from '$lib/hub/ShareNotices.svelte';
 import Avatar from '$lib/components/Avatar.svelte';
 import { shareApi } from '$lib/hub/share';
 import { boardsApi, type MoodBoard, type BoardPin, type BoardInput, type PinterestImport, type FoundPin, type BoardSearch, type ExtensionKey } from '$lib/hub/boards';
 let boards = $state<MoodBoard[]>([]), selected = $state<number | null>(null), pins = $state<BoardPin[]>([]);
 let loading = $state(false), more = $state(false), error = $state(''), notice = $state(''), savedOnly = $state(false), reviewOnly = $state(false);
 let sentinel = $state<HTMLDivElement>();
 let cursor = $state<string | null>(null), hasMore = $state(false), editing = $state(false), editId = $state<number | null>(null);
 let name = $state(''), interests = $state(''), instructions = $state(''), enabled = $state(true), interval = $state(12), saving = $state(false);
 let targetImages = $state(100), avoid = $state(''), seeds = $state(''), media = $state<'any' | 'photos' | 'gifs'>('any'), minWidth = $state(0), strictness = $state<'strict' | 'balanced'>('strict'), childFriendly = $state(false), aiImages = $state<'ignore' | 'mark' | 'reject'>('reject');
 let waiting = $state<BoardPin[]>([]), layout = $state(0); // `layout` re-flows the grid only when the list is replaced
 // After removing the open image from view, keep browsing: show the next one instead of closing.
 function following(p: BoardPin) { const list = found ?? pins; const i = list.findIndex(v => v.id === p.id); return list[i + 1] ?? list[i - 1] ?? null; }
 function showWaiting() { const ids = new Set(pins.map(p => p.id)); pins = [...waiting.filter(w => !ids.has(w.id)), ...pins]; waiting = []; window.scrollTo({top: 0, behavior: 'smooth'}); }
 let sharing = $state<{board?: {id: number; name: string}; pin?: BoardPin} | null>(null);
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
 let sourcesOpen = $state(false);
 let importing = $state(false), importOpen = $state(false), pinterestUrl = $state(''), importJob = $state<PinterestImport | null>(null);
 let imageOpen = $state(false), imageMode = $state<'manual' | 'reference'>('manual'), imageUrl = $state(''), imageTitle = $state(''), imageFile = $state<File | null>(null), imageBusy = $state(false);
 let query = $state(''), searching = $state(false), found = $state<FoundPin[] | null>(null), searchInfo = $state<BoardSearch | null>(null);
 let searchController: AbortController | null = null;
 async function runSearch(e?: SubmitEvent) {
  e?.preventDefault(); const q = query.trim(); if (!allowed()) return;
  if (!q) { clearSearch(); return; }
  searchController?.abort(); searchController = new AbortController(); const signal = searchController.signal, owner = stamp();
  searching = true; error = '';
  try { const result = await boardsApi.search(q, signal); if (owner !== stamp() || signal.aborted) return; searchInfo = result; found = result.pins; }
  catch (err) { if (owner === stamp() && !signal.aborted) error = message(err); }
  finally { if (owner === stamp() && !signal.aborted) searching = false; }
 }
 function clearSearch() { searchController?.abort(); searchController = null; query = ''; found = null; searchInfo = null; searching = false; }
 let controller: AbortController | null = null, generation = 0, identity = '', pollUntil = 0;
 const board = $derived(boards.find(b => b.id === selected));
 const curatorActive = $derived(active(board?.last_status));
 const retrySeconds = $derived(Math.max(0, Math.ceil((((selected ? retryDeadlines[selected] : 0) ?? 0) - clock) / 1000)));
 const findDisabled = $derived(refreshing || curatorActive || retrySeconds > 0);
 const findLabel = $derived(refreshing ? 'Starting…' : board?.last_status === 'queued' ? 'Queued…' : board?.last_status === 'collecting' ? 'Finding images…' : retrySeconds > 0 ? `Find images (${retrySeconds}s)` : 'Find images');
 const allowed = () => session.boardsAllowed && session.ready && !session.needsLogin && !session.choosing && !session.profile?.pin_locked;
 const stamp = () => `${session.profileId}:${session.version}`;
 const message = (e: unknown) => e instanceof Error ? e.message : 'Could not load your boards.';
 function clear() { clearSearch(); sourcesOpen = false; waiting = []; importOpen = false; importing = false; importJob = null; pinterestUrl = ''; imageOpen = false; imageBusy = false; imageUrl = ''; imageTitle = ''; imageFile = null; controller?.abort(); controller = null; generation++; boards = []; pins = []; selected = null; preview = null; editing = false; moving = null; saving = false; busy = null; deleteConfirm = false; notice = ''; error = ''; loading = false; more = false; hasMore = false; cursor = null; pollUntil = 0; refreshing = false; retryDeadlines = {}; }
 async function load(append = false, quiet = false) {
  if (!allowed() || (append && (loading || more || !hasMore || !selected))) return;
  controller?.abort(); controller = new AbortController(); const signal = controller.signal, token = ++generation, owner = stamp();
  if (append) more = true; else if (!quiet) loading = true;
  error = '';
  try {
   const list = await boardsApi.list(signal);
   if (signal.aborted || token !== generation || owner !== stamp() || !allowed()) return;
   boards = list.boards; rememberRetry(boards);
   if (selected && !boards.some(b => b.id === selected)) { selected = null; void goto('/boards', {replaceState: true, keepFocus: true}); }
   if (!selected) { pins = []; hasMore = false; return; }
   const data = await boardsApi.detail(selected, {saved: savedOnly, unreviewed: reviewOnly, cursor: append ? cursor ?? undefined : undefined}, signal);
   if (signal.aborted || token !== generation || owner !== stamp() || !allowed()) return;
   boards = boards.map(b => b.id === data.board.id ? data.board : b); rememberRetry([data.board]);
   // Kid profiles have no Pinterest import (403); a status hiccup must never blank the board either.
   const imported = session.isChild ? {import: null} : await boardsApi.pinterestStatus(selected, signal).catch(() => ({import: null}));
   if (signal.aborted || token !== generation || owner !== stamp() || !allowed()) return;
   importJob = imported.import;
   if (importRunning(importJob) && !importJob?.note) pollUntil = Date.now() + 180000;
   if (quiet && !append && pins.length) {
    // Background poll: keep what is on screen (and every page already scrolled to). Only genuinely new pins
    // are added; unchanged pins keep their object so no tile re-renders or refetches its image.
    const known = new Map(pins.map(p => [p.id, p]));
    const newest = Math.max(...pins.map(p => p.id));
    const incoming = data.pins.filter(p => p.id > newest && !known.has(p.id));
    pins = pins.map(p => { const u = data.pins.find(v => v.id === p.id); return u && (u.saved !== p.saved || u.feedback !== p.feedback) ? u : p; });
    if (incoming.length) {
     if (window.scrollY > 400) waiting = [...incoming, ...waiting.filter(w => !incoming.some(i => i.id === w.id))];
     else pins = [...incoming, ...pins];
    }
   } else {
    const seen = new Set(append ? pins.map(p => p.id) : []);
    const fresh = data.pins.filter(p => { if (seen.has(p.id)) return false; seen.add(p.id); return true; });
    if (!append) layout++; pins = append ? [...pins, ...fresh] : fresh; cursor = data.next_cursor; hasMore = data.has_more; waiting = [];
   }
  } catch (e) { if (!signal.aborted && token === generation && owner === stamp() && allowed()) error = message(e); }
  finally { if (token === generation && owner === stamp()) { loading = false; more = false; controller = null; } }
 }
 $effect(() => {
  const key = `${session.profileId}:${session.version}:${session.profile?.pin_locked}:${session.needsLogin}:${session.choosing}:${session.ready}:${session.hubAllowed}`;
  if (key !== identity) { identity = key; untrack(() => { clear(); selected = urlBoard(); if (allowed()) void load(); }); }
 });
 onMount(() => {
  let lastPoll = 0;
  const timer = window.setInterval(() => {
   clock = Date.now();
   if (allowed() && (active(board?.last_status) || (!selected && boards.some(b => active(b.last_status))) || clock < pollUntil) && clock - lastPoll >= 5000 && document.visibilityState !== 'hidden' && !loading && !more && !controller) { lastPoll = clock; void load(false, true); }
  }, 1000);
  const wake = () => { clock = Date.now(); if (document.visibilityState !== 'hidden' && allowed() && (active(board?.last_status) || (!selected && boards.some(b => active(b.last_status))) || clock < pollUntil) && !loading && !more && !controller) void load(false, true); };
  document.addEventListener('visibilitychange', wake);
  return () => { window.clearInterval(timer); document.removeEventListener('visibilitychange', wake); controller?.abort(); };
 });
 $effect(() => {
  const node = sentinel;
  if (!node || !hasMore || loading || more) return;
  const observer = new IntersectionObserver(entries => { if (entries.some(e => e.isIntersecting)) void load(true); }, {rootMargin: '1500px'});
  observer.observe(node);
  return () => observer.disconnect();
 });
 function importRunning(job: PinterestImport | null) { return !!job && ['queued', 'importing', 'waiting'].includes(job.status); }
 function importText(job: PinterestImport) {
  const counts = `${job.imported} new` + (job.existing ? `, ${job.existing} already here` : '') + (job.skipped ? `, ${job.skipped} skipped` : '');
  if (job.status === 'complete') return `Pinterest import complete: ${counts}.${job.error ? ' ' + job.error : ''}`;
  if (job.status === 'error') return `Pinterest import stopped at ${job.done}${job.total ? ' / ' + job.total : ''} (${counts}). ${job.error}`;
  const of = job.total ? `${job.done} / ${job.total}` : `${job.done}`;
  const later = job.note && job.next_at ? ` ${job.note} Resumes around ${new Date(job.next_at * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})}.` : '';
  return `Pinterest: importing ${of} (${counts}), continues in background.${later}`;
 }
 async function importPinterest(e: SubmitEvent) {
  e.preventDefault(); if (!selected || !allowed() || importing) return;
  importing = true; const id = selected, owner = stamp();
  try { const result = await boardsApi.pinterest(id, pinterestUrl.trim()); if (owner !== stamp() || selected !== id || !allowed()) return; importJob = result.import; importOpen = false; pollUntil = Date.now() + 180000; notice = 'Pinterest import started. It continues slowly in the background; images appear as they are saved.'; }
  catch (e) { if (owner === stamp() && selected === id && allowed()) error = message(e); }
  finally { if (owner === stamp() && selected === id) importing = false; }
 }
 const urlBoard = () => Number(page.url.searchParams.get('board')) || null;
 // The open board lives in the URL (?board=7): browser Back returns to the overview, links can be shared.
 let overviewY = 0;  // Back from a board returns to the same spot in the board list.
 function choose(id: number | null) { if (id && !selected) overviewY = window.scrollY; void goto(id ? `/boards?board=${id}` : '/boards', {keepFocus: true, noScroll: false}); }
 function open(id: number | null) { importOpen = false; sourcesOpen = false; importing = false; selected = id; importJob = null; pins = []; preview = null; notice = ''; waiting = []; savedOnly = false; reviewOnly = false; const y = id ? 0 : overviewY; void load().then(() => requestAnimationFrame(() => window.scrollTo({top: y}))); }
 $effect(() => { const id = urlBoard(); untrack(() => { if (id !== selected && identity) open(id); }); });
 let boardFilter = $state(''), boardSort = $state<'recent' | 'name' | 'size'>('recent');
 try { const saved = localStorage.getItem('wc-boards-sort'); if (saved === 'name' || saved === 'size' || saved === 'recent') boardSort = saved; } catch { /* storage unavailable */ }
 $effect(() => { const v = boardSort; try { localStorage.setItem('wc-boards-sort', v); } catch { /* storage unavailable */ } });
 const shownBoards = $derived.by(() => {
  const q = boardFilter.trim().toLowerCase();
  const rows = q ? boards.filter(b => `${b.name} ${b.interests}`.toLowerCase().includes(q)) : [...boards];
  const recent = (b: MoodBoard) => Math.max(b.last_added ?? 0, b.created_at ?? 0);
  return rows.sort(boardSort === 'name' ? (a, b) => a.name.localeCompare(b.name) : boardSort === 'size' ? (a, b) => (b.pin_count ?? 0) - (a.pin_count ?? 0) : (a, b) => recent(b) - recent(a));
 });
 const hue = (id: number) => (id * 67) % 360;
 function edit(b?: MoodBoard) { editId = b?.id ?? null; name = b?.name ?? ''; interests = b?.interests ?? ''; instructions = b?.instructions ?? ''; enabled = b?.enabled ?? true; interval = b?.interval_hours ?? 12; targetImages = b?.target_images ?? 100; avoid = b?.avoid ?? ''; seeds = b?.seed_searches ?? ''; media = b?.media ?? 'any'; minWidth = b?.min_width ?? 0; strictness = b?.strictness ?? 'strict'; childFriendly = session.isChild || (b?.child_friendly ?? false); aiImages = b?.ai_images ?? 'reject'; error = ''; editing = true; }
 async function saveBoard(e: SubmitEvent) {
  e.preventDefault(); if (!allowed() || saving) return; saving = true; const owner = stamp();
  const body: BoardInput = {name: name.trim(), interests: interests.trim(), instructions: instructions.trim(), enabled, interval_hours: Number(interval), target_images: Number(targetImages), avoid: avoid.trim(), seed_searches: seeds.trim(), media, min_width: Number(minWidth), strictness: childFriendly ? 'strict' : strictness, child_friendly: session.isChild || childFriendly, ai_images: aiImages};
  try { const result = editId ? await boardsApi.update(editId, body) : await boardsApi.create(body); if (owner !== stamp() || !allowed()) return; selected = result.board.id; void goto(`/boards?board=${result.board.id}`, {keepFocus: true}); editing = false; await load(); notice = editId ? 'Board updated.' : active(result.board.last_status) ? 'Board created. Follow its curator’s progress below.' : 'Board created. Find images to start filling it.'; }
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
 // Download every loaded preview in the background (a few at a time) so scrolling never shows a blank tile.
 $effect(() => { const urls = (found ?? pins).map(p => p.thumb_url ?? p.image_url); preloadImages(urls); });
 $effect(() => () => stopPreloading());
 let reshuffleOpen = $state(false), reshuffling = $state(false);
 const replaceable = $derived(board ? Math.max(0, (board.pin_count ?? 0) - (board.saved_count ?? 0)) : 0);
 // Swap every image that is not hearted or thumbs-up for new finds. The curator is not taught from them.
 async function reshuffle() {
  if (!selected || !allowed() || reshuffling) return;
  reshuffling = true; error = ''; const id = selected, owner = stamp();
  try {
   const result = await boardsApi.reshuffle(id);
   if (owner !== stamp() || selected !== id || !allowed()) return;
   boards = boards.map(b => b.id === id ? result.board : b); rememberRetry([result.board]);
   reshuffleOpen = false; pollUntil = Date.now() + 180000;
   notice = result.replaced ? `Replacing ${result.replaced} image${result.replaced === 1 ? '' : 's'}. Your curator is finding new ones; the ${result.kept} you liked stay.` : 'Nothing to replace: every image is liked. Your curator will look for more.';
   void load();
  } catch (e) { if (owner === stamp()) error = message(e); } finally { if (owner === stamp()) reshuffling = false; }
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
  try { const after = following(p); await boardsApi.pin(p.board_id, p.id, patch); if (owner !== stamp() || !allowed()) return; if (patch.hidden || patch.board_id) pins = pins.filter(v => v.id !== p.id); else pins = pins.map(v => v.id === p.id ? {...v, ...patch} : v); if (found) found = patch.hidden ? found.filter(v => v.id !== p.id) : found.map(v => v.id === p.id ? {...v, ...patch, board_name: patch.board_id ? boards.find(b => b.id === patch.board_id)?.name ?? v.board_name : v.board_name} : v); if (preview?.id === p.id) preview = patch.hidden || patch.board_id ? after : {...preview, ...patch}; moving = null; }
  catch (e) { if (owner === stamp()) error = message(e); } finally { if (owner === stamp()) busy = null; }
 }
 // Remove from the board without a verdict: the curator learns nothing from it (Not a fit teaches it).
 async function remove(p: BoardPin) { await pinAction(p, {hidden: true}); if (!pins.some(v => v.id === p.id)) notice = 'Removed from the board. Your curator was not told.'; }
 let extOpen = $state(false), extKeys = $state<ExtensionKey[]>([]), extNew = $state(''), extBrowser = $state<'chrome' | 'firefox'>('chrome'), extBusy = $state(false), extCopied = $state('');
 async function openExtension() {
  extOpen = true; extNew = ''; extCopied = ''; error = '';
  extBrowser = /firefox/i.test(navigator.userAgent) ? 'firefox' : 'chrome';
  try { extKeys = (await boardsApi.extKeys()).keys; } catch (e) { error = message(e); }
 }
 async function createKey() {
  extBusy = true; error = '';
  try { const r = await boardsApi.createExtKey(extBrowser === 'firefox' ? 'Firefox' : 'Chrome'); extNew = r.key; extKeys = [r.info, ...extKeys]; }
  catch (e) { error = message(e); } finally { extBusy = false; }
 }
 async function revokeKey(k: ExtensionKey) {
  try { await boardsApi.revokeExtKey(k.id); extKeys = extKeys.filter(v => v.id !== k.id); } catch (e) { error = message(e); }
 }
 async function copy(text: string, what: string) { try { await navigator.clipboard.writeText(text); extCopied = what; } catch { extCopied = ''; } }
 let reviewing = $state<BoardPin | null>(null), reviewNote = $state('');
 const REASONS = ['Costume / party look', 'Wrong era', 'Too young / teen', 'Advert or stock photo', 'Messy collage', 'Boring', 'Too revealing', 'Wrong fit or cut', 'High heels', 'AI generated', 'Low quality or blurry', 'Text, quote or poster image', 'Off-topic for this board'];
 const noteParts = (n: string) => n.split(' + ').map(x => x.trim()).filter(Boolean);
 const reasonOn = (n: string, r: string) => noteParts(n).includes(r);
 function toggleReason(r: string) { const parts = noteParts(reviewNote); reviewNote = (parts.includes(r) ? parts.filter(x => x !== r) : [...parts, r]).join(' + ').slice(0, 300); }
 async function review(p: BoardPin, verdict: '' | 'good' | 'bad', note = '') {
  if (!allowed() || busy !== null) return; busy = p.id; const owner = stamp();
  try {
   const after = following(p);
   const result = await boardsApi.review(p.board_id, p.id, {verdict, note});
   if (owner !== stamp() || !allowed()) return;
   const gone = verdict === 'bad'; // Fits stays where it is (also in To review) so nothing jumps.
   pins = gone ? pins.filter(v => v.id !== p.id) : pins.map(v => v.id === p.id ? result.pin : v);
   if (found) found = verdict === 'bad' ? found.filter(v => v.id !== p.id) : found.map(v => v.id === p.id ? {...v, ...result.pin} : v);
   boards = boards.map(b => b.id === result.board.id ? result.board : b);
   if (preview?.id === p.id) preview = verdict === 'bad' ? after : result.pin;
   reviewing = null; reviewNote = '';
   notice = verdict === 'bad' ? 'Removed. Your curator will avoid images like this.' : verdict === 'good' ? 'Thanks. Your curator will look for more like this.' : '';
  } catch (e) { if (owner === stamp()) error = message(e); } finally { if (owner === stamp()) busy = null; }
 }
 let deleteConfirm = $state(false);
 let dragging = $state(false);
 const tags = $derived((board?.interests ?? '').split(',').map(t => t.trim()).filter(Boolean).slice(0, 8));
 const target = $derived(Math.max(1, board?.target_images ?? 100));
 const progress = $derived(Math.min(100, Math.round(((board?.pin_count ?? 0) / target) * 100)));
 const tone = $derived(curatorActive ? 'busy' : board?.last_status === 'error' ? 'bad' : board?.last_status === 'ok' || board?.last_status === 'target_reached' ? 'good' : 'idle');
 function pickFile(file: File | null | undefined) { imageFile = file ?? null; if (imageFile) imageUrl = ''; }
 function dropFile(e: DragEvent) { e.preventDefault(); dragging = false; pickFile(e.dataTransfer?.files?.[0]); }
 // Shared boards: a member leaves; a grown-up shows a picture the kid check hid to the kids, or hides one.
 const mine = (b: MoodBoard | null | undefined) => !b || b.role !== 'member';
 let leaveConfirm = $state(false), kidBusy = $state(false);
 async function leaveBoard() {
  if (!selected || !allowed() || session.profileId == null) return; const owner = stamp();
  try { await shareApi.removeMember(selected, session.profileId); if (owner !== stamp() || !allowed()) return; leaveConfirm = false; selected = null; void goto('/boards', {keepFocus: true}); await load(); }
  catch (e) { if (owner === stamp()) error = message(e); }
 }
 async function kidToggle(p: BoardPin, visible: boolean) {
  kidBusy = true; error = ''; const owner = stamp();
  try {
   const r = await boardsApi.kid(p.board_id, p.id, visible); if (owner !== stamp()) return;
   pins = pins.map(v => v.id === p.id ? {...v, ...r.pin} : v);
   if (preview?.id === p.id) preview = {...preview, ...r.pin};
   boards = boards.map(b => b.id === r.board.id ? r.board : b);
  } catch (e) { if (owner === stamp()) error = message(e); } finally { kidBusy = false; }
 }
 async function removeBoard() { if (!selected || !allowed()) return; const owner = stamp(); try { await boardsApi.remove(selected); if (owner !== stamp() || !allowed()) return; deleteConfirm = false; selected = null; void goto('/boards', {keepFocus: true}); await load(); } catch(e) { if (owner === stamp()) error = message(e); } }
</script>

<svelte:head><title>Mood boards · The Hub</title></svelte:head>
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
  {:else if name === 'puzzle'}<path d="M10 3h4v3a2 2 0 1 0 4 0V3h3v7h-3a2 2 0 1 0 0 4h3v7h-7v-3a2 2 0 1 0-4 0v3H3v-7h3a2 2 0 1 0 0-4H3V3h7z"/>
  {:else if name === 'check'}<path d="M5 12l5 5L20 7"/>
  {:else if name === 'x'}<path d="M6 6l12 12M18 6L6 18"/>
  {:else if name === 'shuffle'}<path d="M16 3h5v5"/><path d="M4 20 21 3"/><path d="M21 16v5h-5"/><path d="M15 15l6 6"/><path d="M4 4l5 5"/>
  {:else if name === 'up'}<path d="M7 10v11"/><path d="M15 5.9 14 10h5.8a2 2 0 0 1 1.9 2.6l-2.3 8a2 2 0 0 1-1.9 1.4H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.8a2 2 0 0 0 1.8-1.1L12 2a3.1 3.1 0 0 1 3 3.9z"/>
  {:else if name === 'down'}<path d="M17 14V3"/><path d="M9 18.1 10 14H4.2a2 2 0 0 1-1.9-2.6l2.3-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.8a2 2 0 0 0-1.8 1.1L12 22a3.1 3.1 0 0 1-3-3.9z"/>
  {:else if name === 'trash'}<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
  {:else if name === 'search'}<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/>
  {:else if name === 'link'}<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>{/if}
 </svg>
{/snippet}

<section class="boards-page">
 <header class="page-heading">
  <div><p class="eyebrow">Your collections</p><h1>Mood boards</h1><p class="lede">A cosy place for everything that catches your eye.</p></div>
  <div class="heading-actions">{#if !session.isChild}<button class="btn pill" onclick={() => { sharing = {}; }}>{@render icon('link')}Shared links</button><button class="btn pill" onclick={openExtension}>{@render icon('puzzle')}Browser extension</button>{/if}<button class="btn primary new-board" onclick={() => edit()}>{@render icon('plus')}New board</button></div>
 </header>
 {#if error && !editing && !imageOpen && !importOpen}<div class="feedback error" role="alert"><span>{error}</span><button class="btn" onclick={() => load()}>Try again</button></div>{/if}
 {#if !selected}<ShareNotices onOpen={(id) => choose(id)} />{/if}
 {#if notice}<p class="feedback good" role="status">{notice}<button class="dismiss" aria-label="Dismiss message" onclick={() => { notice = ''; }}>×</button></p>{/if}

 {#if boards.length}
  <form class="board-search" role="search" onsubmit={runSearch}>
   <label class="search-field">{@render icon('search')}<input type="search" aria-label="Search your boards by what's in the picture" maxlength="300" bind:value={query} placeholder="Find an image… e.g. flower dress with stars" oninput={() => { if (!query.trim() && found) clearSearch(); }} /></label>
   <button class="btn primary pill" disabled={searching || !query.trim()}>{searching ? 'Searching…' : 'Search'}</button>
   {#if found}<button type="button" class="btn pill" onclick={clearSearch}>Clear</button>{/if}
  </form>
  {#if selected && board}
   <nav class="board-nav" aria-label="Board navigation">
    <button class="btn pill back" onclick={() => choose(null)}>← All boards</button>
    {#if boards.length > 1}<label class="switch"><span class="sr-only">Switch board</span><select value={selected} onchange={(e) => choose(Number(e.currentTarget.value))}>{#each [...boards].sort((a, b) => a.name.localeCompare(b.name)) as b (b.id)}<option value={b.id}>{b.name}</option>{/each}</select></label>{/if}
   </nav>
  {/if}
 {/if}

 {#if found && searchInfo}
  <section class="search-results" aria-label="Search results" aria-busy={searching}>
   <div class="results-head" role="status"><strong>{found.length ? `Best matches for “${query.trim()}”` : 'No matching images'}</strong>
    {#if searchInfo.pins_total > searchInfo.indexed}<span class="muted">Still learning your images: {searchInfo.indexed} of {searchInfo.pins_total} searchable so far.</span>{/if}
   </div>
   {#if found.length}
    <PinColumns items={found} resetKey={query}>
     {#snippet item(pin)}
      <article class="pin">
       <div class="frame">
        <button class="picture" aria-label={`View ${pin.title}`} onclick={() => { preview = pin; }}><BoardImage url={pin.thumb_url ?? pin.image_url} alt={pin.title} animated={pin.media_type === 'gif'} width={pin.width} height={pin.height} /></button>
        {#if pin.media_type === 'gif'}<span class="badge">GIF</span>{/if}{#if pin.ai_flag}<span class="badge ai" title={`AI generated: ${pin.ai_flag}`}>AI</span>{/if}{#if pin.kid_state === 'hidden'}<span class="badge kidhide" title={`Hidden from kids: ${pin.kid_reason}`}>Hidden from kids</span>{/if}
         </div>
       <div class="caption"><button class="pin-title" onclick={() => { preview = pin; }}>{pin.title}</button><button class="pin-meta board-link" onclick={() => { clearSearch(); choose(pin.board_id); }}>on {pin.board_name}</button>
        <div class="actions" role="group" aria-label={`Actions for ${pin.title}`}><span class="gap"></span><button class="act heart" class:on={pin.saved} disabled={busy === pin.id} title={pin.saved ? 'Saved — click to unsave' : 'Save'} aria-label={pin.saved ? `Unsave ${pin.title}` : `Save ${pin.title}`} aria-pressed={pin.saved} onclick={() => pinAction(pin, {saved: !pin.saved})}>{@render icon('heart')}</button></div></div>
      </article>
     {/snippet}
    </PinColumns>
   {:else}<div class="empty"><h2>Nothing found yet</h2><p>{searchInfo.indexed ? 'Try describing the picture differently, for example its colours, pattern or object.' : 'Your images are still being prepared for search. Try again in a few minutes.'}</p></div>{/if}
   {#if !session.isChild}<PinterestSearch query={query} boardId={board?.id ?? null} boardName={board?.name ?? ''} prominent={!found.length} onadded={() => load(false, true)} />{/if}
  </section>
 {:else if searchInfo && !searchInfo.available}
  <p class="feedback" role="status">Image search is not available: {searchInfo.reason}<button class="dismiss" aria-label="Dismiss message" onclick={clearSearch}>×</button></p>
 {/if}

 {#if !found && !selected && boards.length}
  <section class="overview" aria-label="All boards">
   <div class="overview-bar">
    <h2>{boards.length} board{boards.length === 1 ? '' : 's'}</h2>
    {#if boards.length > 4}<label class="board-filter">{@render icon('search')}<input type="search" aria-label="Filter boards" placeholder="Filter boards" bind:value={boardFilter} /></label>{/if}
    <label class="sort"><span class="sr-only">Sort boards</span><select bind:value={boardSort} aria-label="Sort boards"><option value="recent">Recently updated</option><option value="name">A–Z</option><option value="size">Most images</option></select></label>
   </div>
   <div class="board-grid">
    {#each shownBoards as b (b.id)}
     <button class="board-card" onclick={() => choose(b.id)} aria-label={`Open ${b.name}, ${b.pin_count ?? 0} images`} style:--hue={hue(b.id)}>
      <span class="collage n{Math.min(4, b.preview_urls?.length ?? 0)}" aria-hidden="true">
       {#each (b.preview_urls ?? []).slice(0, 4) as url (url)}<span class="cell"><BoardImage {url} alt="" fit /></span>{:else}<span class="monogram">{b.name.slice(0, 1).toUpperCase()}</span>{/each}
      </span>
      <span class="card-body">
       <span class="card-name">{b.name}</span>
       <span class="card-meta">{#if b.child_friendly}<span class="kid-badge" title="Child friendly">🧸</span> {/if}{#if b.members?.length || b.role === 'member'}<span class="shared-badge" title={`Shared with ${[b.owner, ...(b.members ?? [])].filter(x => x && x.id !== session.profileId).map(x => x?.name).join(', ')}`}>Shared</span> {/if}{b.pin_count ?? 0} images{#if b.saved_count} · {@render icon('heart')}{b.saved_count}{/if}</span>
       {#if active(b.last_status)}<span class="card-status busy">Curator searching…</span>
       {:else if b.last_status === 'error'}<span class="card-status bad">Needs attention</span>
       {:else if !b.enabled}<span class="card-status">Paused</span>{/if}
      </span>
     </button>
    {:else}<p class="muted">No boards match “{boardFilter}”.</p>{/each}
    <button class="board-card add" onclick={() => edit()}><span class="collage add-art" aria-hidden="true">{@render icon('plus')}</span><span class="card-body"><span class="card-name">New board</span><span class="card-meta">Start a new collection</span></span></button>
   </div>
  </section>
 {:else if !found}
 {#if board}
  <section class="board-hero">
   <div class="hero-main">
    <h2>{board.name}</h2>
    {#if board.members?.length || board.role === 'member'}
     <div class="people" aria-label="Working on this board">
      {#each [board.owner, ...(board.members ?? [])].filter(x => !!x) as person (person?.id)}<span class="person" title={person?.id === board.owner?.id ? `${person?.name} (owner)` : person?.name}><Avatar emoji={person?.emoji ?? ''} color={person?.color ?? ''} size="1.6rem" /></span>{/each}
      <span class="muted">{board.role === 'member' ? `Shared by ${board.owner?.name ?? 'someone'}` : `Shared with ${(board.members ?? []).map(m => m.name).join(', ')}`}</span>
      {#if board.role === 'member'}<button class="link-btn" onclick={() => { leaveConfirm = true; }}>Leave board</button>{/if}
     </div>
    {/if}
    {#if board.kid_hidden_count}<p class="kid-note">🧸 {board.kid_hidden_count} image{board.kid_hidden_count === 1 ? ' is' : 's are'} hidden from the kids. Open one to see why, or show it to them anyway.</p>{/if}
    {#if tags.length || board.child_friendly}<ul class="tags" aria-label="Interests">{#if board.child_friendly}<li class="kid">🧸 Child friendly</li>{/if}{#each tags as t}<li>{t}</li>{/each}</ul>{/if}
    <div class="goal">
     <div class="goal-text"><span class="image-count">{board.pin_count ?? 0} / {board.target_images ?? 100} images</span><span class="muted">{board.enabled ? `Curator checks every ${board.interval_hours} hours.` : 'Automatic discovery paused.'}</span></div>
     <div class="bar" role="progressbar" aria-label="Images toward target" aria-valuemin="0" aria-valuemax={target} aria-valuenow={board.pin_count ?? 0}><span style:width="{progress}%"></span></div>
    </div>
    <div class="tools" role="toolbar" aria-label="Board actions">
     <button class="btn primary find" disabled={findDisabled} onclick={refresh}>{@render icon('spark')}{findLabel}</button>
     {#if mine(board)}<button class="btn pill" disabled={curatorActive || reshuffling} title="Replace every image you have not hearted or given a thumbs up" onclick={() => { error = ''; reshuffleOpen = true; }}>{@render icon('shuffle')}Reshuffle</button>{/if}
     <button class="btn pill" onclick={() => openImage('manual')}>{@render icon('plus')}Add image</button>
     {#if !session.isChild}<button class="btn pill" title="Let the source curator research sites for this board" onclick={() => { sourcesOpen = true; }}>{@render icon('puzzle')}Find sources</button>{/if}
     {#if mine(board)}<a class="btn pill" href={`/boards/learned?board=${board.id}`} title="See and correct what this board's curator learned about your taste">{@render icon('spark')}What it learned</a>{/if}
     {#if !session.isChild && mine(board)}
     <button class="btn pill" onclick={() => openImage('reference')}>{@render icon('eye')}{board.reference_url ? 'Change inspiration image' : 'Add inspiration image'}</button>
     <button class="btn pill" onclick={() => { error = ''; importOpen = true; }}>{@render icon('pin')}Import Pinterest</button>
     <button class="btn pill" onclick={() => { sharing = {board: {id: board.id, name: board.name}}; }}>{@render icon('upload')}Share</button>
     {/if}
     <span class="spacer"></span>
     <div class="segmented" role="group" aria-label="Show">
      <button class:on={!savedOnly && !reviewOnly} aria-pressed={!savedOnly && !reviewOnly} onclick={() => { if (savedOnly || reviewOnly) { savedOnly = false; reviewOnly = false; void load(); } }}>All</button>
      <button class:on={savedOnly} aria-pressed={savedOnly} title="Saved and marked Fits" onclick={() => { if (!savedOnly) { savedOnly = true; reviewOnly = false; void load(); } }}>{@render icon('heart')}Saved{savedOnly ? ' only' : ''}</button>
      <button class:on={reviewOnly} aria-pressed={reviewOnly} onclick={() => { if (!reviewOnly) { reviewOnly = true; savedOnly = false; void load(); } }}>{@render icon('check')}To review</button>
     </div>
     {#if mine(board)}<button class="btn pill icon-only" aria-label={`Edit ${board.name} curator`} title="Edit curator" onclick={() => edit(board)}>{@render icon('gear')}</button>{/if}
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
  {#if importJob}<p class="feedback" role="status" data-testid="pinterest-import">{importText(importJob)}</p>{/if}
 {/if}

 {#if loading}<div class="masonry skeleton" role="status" aria-label="Gathering your images…">{#each [14, 20, 11, 17, 22, 13, 18, 12] as h}<div class="ghost" style:height="{h}rem"></div>{/each}</div>
 {:else if !boards.length}<div class="empty"><div class="empty-art" aria-hidden="true"><span></span><span></span><span></span></div><h2>What inspires you?</h2><p>Give a hobby, interest or idea its own board. Its curator will find images and GIFs for you to collect.</p><button class="btn primary" onclick={() => edit()}>Create your first board</button></div>
 {:else if !pins.length}<div class="empty"><div class="empty-art" aria-hidden="true"><span></span><span></span><span></span></div><h2>{reviewOnly ? 'All caught up' : savedOnly ? 'Your favourites belong here' : 'Make room for inspiration'}</h2>{#if reviewOnly}<p>Every image on this board has been reviewed. New finds will show up here.</p>{:else}<p>{savedOnly ? 'Save an image or mark it Fits to find it here later.' : curatorActive ? 'Your curator is working. Matching images will appear automatically.' : board?.last_status === 'no_matches' ? 'No matching images were found. You can refine your curator’s instructions or try another search when it is ready.' : board?.last_status === 'error' ? 'Your curator could not finish. See its activity above for the reason.' : 'Your board is ready. Ask its curator to find images, add your own, then save the ones you love.'}</p>{#if !savedOnly}<button class="btn primary" disabled={findDisabled} onclick={refresh}>{findLabel}</button>{/if}{/if}</div>
 {:else}
  {#if waiting.length}<button class="new-pill" onclick={showWaiting}>↑ {waiting.length} new image{waiting.length === 1 ? '' : 's'}</button>{/if}
  <PinColumns items={pins} resetKey={`${selected}|${layout}`}>
   {#snippet item(pin)}
    <article class="pin">
     <div class="frame">
      <button class="picture" aria-label={`View ${pin.title}`} onclick={() => { preview = pin; }}><BoardImage url={pin.thumb_url ?? pin.image_url} alt={pin.title} animated={pin.media_type === 'gif'} width={pin.width} height={pin.height} /></button>
      {#if pin.media_type === 'gif'}<span class="badge">GIF</span>{/if}{#if pin.ai_flag}<span class="badge ai" title={`AI generated: ${pin.ai_flag}`}>AI</span>{/if}{#if pin.kid_state === 'hidden'}<span class="badge kidhide" title={`Hidden from kids: ${pin.kid_reason}`}>Hidden from kids</span>{/if}
     </div>
     <div class="caption"><button class="pin-title" onclick={() => { preview = pin; }}>{pin.title}</button>{#if pin.attribution}<span class="pin-meta">{pin.attribution}</span>{/if}
      <div class="actions" role="group" aria-label={`Actions for ${pin.title}`}>
       <button class="act up" class:on={pin.feedback === 'good'} aria-pressed={pin.feedback === 'good'} aria-label="Fits" title="Fits this board — the curator looks for more like it" disabled={busy === pin.id} onclick={() => review(pin, pin.feedback === 'good' ? '' : 'good')}>{@render icon('up')}</button>
       <button class="act down" aria-label="Not a fit" title="Not a fit — remove it and teach the curator" disabled={busy === pin.id} onclick={() => { reviewing = pin; reviewNote = ''; }}>{@render icon('down')}</button>
       <span class="gap"></span>
       <button class="act heart" class:on={pin.saved} disabled={busy === pin.id} title={pin.saved ? 'Saved — click to unsave' : 'Save'} aria-label={pin.saved ? `Unsave ${pin.title}` : `Save ${pin.title}`} aria-pressed={pin.saved} onclick={() => pinAction(pin, {saved: !pin.saved})}>{@render icon('heart')}</button>
       <button class="act remove" title="Remove from this board without teaching the curator" aria-label={`Remove ${pin.title}`} disabled={busy === pin.id} onclick={() => remove(pin)}>{@render icon('trash')}</button>
      </div></div>
    </article>
   {/snippet}
  </PinColumns>
  {#if hasMore}<div class="load-more" bind:this={sentinel}><button class="btn pill" disabled={more} onclick={() => load(true)}>{more ? 'Loading…' : 'More inspiration'}</button></div>{/if}
 {/if}
 {/if}
</section>

{#if editing}<Modal portal title={editId ? 'Edit board curator' : 'Create a mood board'} onClose={() => { if (!saving) editing = false; }}>
 <form onsubmit={saveBoard} class="board-form">
  <fieldset><legend>The board</legend>
   <label>Board name<input required maxlength="100" bind:value={name} placeholder="My garden ideas" /></label>
   <label>Interests and hobbies<input required maxlength="1000" bind:value={interests} placeholder="Cottage gardens, wildflowers, tiny greenhouses" /><small>The board's subject. Separate interests with commas.</small></label>
  </fieldset>
  <fieldset><legend>Teach the curator</legend>
   <label>What to look for<textarea rows="3" maxlength="2000" bind:value={instructions} placeholder="Real home setups with visible hardware and tidy cabling."></textarea><small>Checked against every image with the vision model.</small></label>
   <label>What to avoid<textarea rows="2" maxlength="1000" bind:value={avoid} placeholder="Quote cards and text graphics, gaming chairs, product renders, people's faces"></textarea><small>Any image showing these is rejected, even if it otherwise fits.</small></label>
   <label>Seed searches<textarea rows="3" maxlength="1000" bind:value={seeds} placeholder={'10 inch mini rack\nraspberry pi cluster\nhomelab cable management'}></textarea><small>Your own search phrases, one per line. Used first, before the curator's own ideas.</small></label>
  </fieldset>
  <label class="child-toggle"><input type="checkbox" bind:checked={childFriendly} disabled={session.isChild} /><span><strong>🧸 Child friendly</strong><small>{session.isChild ? 'Always on for child profiles.' : 'Only safe image search (no social feeds), strict checking, and every image must be fine for a young child.'}</small></span></label>
  <details class="tuning" open={media !== 'any' || minWidth > 0 || strictness !== 'strict' || aiImages !== 'reject'}>
   <summary>More tuning</summary>
   <div class="choice" role="radiogroup" aria-label="How strict">
    <span class="choice-label">How strict</span>
    <label class="opt"><input type="radio" name="strictness" value="strict" bind:group={strictness} /><span><strong>Strict</strong><small>Only clear matches that meet every instruction.</small></span></label>
    <label class="opt"><input type="radio" name="strictness" value="balanced" bind:group={strictness} disabled={childFriendly} /><span><strong>Balanced</strong><small>Clear matches of the subject; preferences may be partly met.</small></span></label>
   </div>
   <div class="choice" role="radiogroup" aria-label="AI-generated images">
    <span class="choice-label">AI-generated images</span>
    <label class="opt"><input type="radio" name="ai-images" value="reject" bind:group={aiImages} /><span><strong>Reject</strong><small>Leave out images that are or look AI-generated.</small></span></label>
    <label class="opt"><input type="radio" name="ai-images" value="mark" bind:group={aiImages} /><span><strong>Mark</strong><small>Keep them, with an AI badge on the picture.</small></span></label>
    <label class="opt"><input type="radio" name="ai-images" value="ignore" bind:group={aiImages} /><span><strong>Ignore</strong><small>Don't check for AI at all.</small></span></label>
   </div>
   <div class="form-grid">
    <label>Image type<select bind:value={media}><option value="any">Photos and GIFs</option><option value="photos">Photos only</option><option value="gifs">GIFs only</option></select></label>
    <label>Minimum size<select bind:value={minWidth}><option value={0}>Any size</option><option value={600}>600 px wide or more</option><option value={1000}>1000 px wide or more</option><option value={1600}>1600 px wide or more</option></select></label>
   </div>
  </details>
  <fieldset><legend>Schedule</legend>
   <label>Image target<span class="target-row"><input type="range" min="10" max="1000" step="10" aria-hidden="true" tabindex="-1" bind:value={targetImages} /><input class="target-num" type="number" min="1" max="1000" step="1" required bind:value={targetImages} /></span><small>The curator keeps searching until the board holds this many images (up to 1,000).</small></label>
   <div class="form-grid"><label class="check"><input type="checkbox" bind:checked={enabled} />Find new images automatically</label><label>Check every<select bind:value={interval}><option value={6}>6 hours</option><option value={12}>12 hours</option><option value={24}>day</option><option value={72}>3 days</option><option value={168}>week</option></select></label></div>
  </fieldset>
  {#if error}<p role="alert" class="error">{error}</p>{/if}
  <div class="form-actions">{#if editId}<button type="button" class="btn danger" onclick={() => { editing = false; deleteConfirm = true; }}>Delete board</button>{/if}<button class="btn" type="button" onclick={() => { editing = false; }}>Cancel</button><button class="btn primary" disabled={saving}>{saving ? 'Saving…' : editId ? 'Save changes' : 'Create board'}</button></div>
 </form>
</Modal>{/if}

{#if imageOpen}<Modal portal title={imageMode === 'reference' ? 'Add visual inspiration' : 'Add an image to this board'} onClose={() => { if (!imageBusy) imageOpen = false; }}>
 <form class="board-form" onsubmit={submitBoardImage}>
  <p class="muted">{imageMode === 'reference' ? 'Show the curator an example of the look you love. It guides searches and stays separate from the board pins.' : session.isChild ? 'Add your own picture. It gets a quick kid-safety check first.' : 'Save your own find straight to this board. GIFs are welcome.'}</p>
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
  <div class="form-actions"><button type="button" class="btn" disabled={imageBusy} onclick={() => { imageOpen = false; }}>Cancel</button><button class="btn primary" disabled={imageBusy || (!imageUrl.trim() && !imageFile)}>{imageBusy ? (session.isChild && imageMode === 'manual' ? 'Checking…' : 'Saving…') : imageMode === 'reference' ? 'Save inspiration' : 'Add image'}</button></div>
 </form>
</Modal>{/if}

{#if sourcesOpen && board}<BoardSources boardId={board.id} boardName={board.name} onClose={() => { sourcesOpen = false; }} />{/if}
{#if importOpen}<Modal portal title="Import a Pinterest board" onClose={() => { if (!importing) importOpen = false; }}><form class="board-form" onsubmit={importPinterest}><p class="muted">Import every pin of a public board into this board. Pinterest is asked gently, so a large board takes a while: the import continues in the background, pauses when Pinterest asks it to slow down and picks up where it stopped. Images are saved locally with their original Pinterest links. Existing pins are skipped.</p><label>Public board link<input type="url" required maxlength="1000" bind:value={pinterestUrl} placeholder="https://www.pinterest.com/username/board-name/" /></label><small>Private boards and short links are not supported.</small>{#if error}<p class="error" role="alert">{error}</p>{/if}<div class="form-actions"><button type="button" class="btn" onclick={() => { importOpen = false; }}>Cancel</button><button class="btn primary" disabled={importing}>{importing ? 'Starting…' : 'Import board'}</button></div></form></Modal>{/if}
{#if reviewing}<Modal title="Why doesn't this fit?" portal onClose={() => { if (busy === null) reviewing = null; }}>
 <form class="board-form" onsubmit={(e) => { e.preventDefault(); if (reviewing) void review(reviewing, 'bad', reviewNote.trim().slice(0, 300)); }}>
  <p class="muted">The image is removed from the board and your curator learns to skip images like it. Pick every reason that applies.</p>
  {#if reviewing.vision_seen || reviewing.vision_why}<div class="curator-belief">{#if reviewing.vision_seen}<p>Curator saw: {reviewing.vision_seen}</p>{/if}{#if reviewing.vision_why}<p>Why kept: {reviewing.vision_why}</p>{/if}</div>{/if}
  <div class="reason-chips" role="group" aria-label="Common reasons">{#each REASONS as reason}<button type="button" class="chip" class:on={reasonOn(reviewNote, reason)} aria-pressed={reasonOn(reviewNote, reason)} onclick={() => toggleReason(reason)}>{reason}</button>{/each}</div>
  <label>Tell the curator (optional)<input maxlength="300" bind:value={reviewNote} placeholder="e.g. Motivational quote, not homelab hardware" /></label>
  <div class="form-actions"><button type="button" class="btn" onclick={() => { reviewing = null; }}>Cancel</button><button class="btn primary" disabled={busy !== null}>Remove and teach curator</button></div>
 </form>
</Modal>{/if}
{#if extOpen}<Modal portal size="lg" title="Add images from any website" onClose={() => { extOpen = false; }}>
 <div class="ext">
  <p class="muted">With the Hub extension, right-click any image on the web, choose <strong>The Hub</strong> and pick a board, or <strong>New board…</strong> to start one from that image. The image is saved here with a link to where you found it.</p>
  <div class="segmented ext-tabs" role="tablist" aria-label="Browser">
   <button role="tab" aria-selected={extBrowser === 'chrome'} class:on={extBrowser === 'chrome'} onclick={() => { extBrowser = 'chrome'; }}>Chrome, Edge, Brave</button>
   <button role="tab" aria-selected={extBrowser === 'firefox'} class:on={extBrowser === 'firefox'} onclick={() => { extBrowser = 'firefox'; }}>Firefox</button>
  </div>
  <ol class="steps">
   <li><strong>Download the extension</strong>
    <p><a class="btn primary pill" href="/extensions/the-hub-{extBrowser}.zip" download>{@render icon('upload')}Download for {extBrowser === 'firefox' ? 'Firefox' : 'Chrome'}</a></p></li>
   {#if extBrowser === 'chrome'}
    <li><strong>Unzip it</strong><p class="muted">Extract the zip into a folder you will keep, for example <code>Documents/the-hub-extension</code>. Chrome loads it from there.</p></li>
    <li><strong>Load it in Chrome</strong><p class="muted">Open <code>chrome://extensions</code>, switch on <em>Developer mode</em> (top right), click <em>Load unpacked</em> and choose the folder. In Edge use <code>edge://extensions</code>, in Brave <code>brave://extensions</code>.</p></li>
   {:else}
    <li><strong>Install it in Firefox</strong>
     <p class="muted">Regular Firefox only keeps add-ons signed by Mozilla. Pick one:</p>
     <ul class="muted">
      <li><em>Try it now:</em> open <code>about:debugging#/runtime/this-firefox</code>, click <em>Load Temporary Add-on…</em> and choose the zip. It stays until Firefox restarts.</li>
      <li><em>Keep it:</em> in Firefox Developer Edition, Nightly or ESR, set <code>xpinstall.signatures.required</code> to <code>false</code> in <code>about:config</code>, then open <code>about:addons</code>, click the gear and <em>Install Add-on From File…</em></li>
     </ul></li>
   {/if}
   <li><strong>Connect it to The Hub</strong>
    <p class="muted">The extension opens its settings after installing (or click its icon). Paste this address and a connection key:</p>
    <div class="copy-row"><code>{location.origin}</code><button class="btn pill" onclick={() => copy(location.origin, 'address')}>{extCopied === 'address' ? 'Copied' : 'Copy address'}</button></div>
    {#if extNew}
     <div class="copy-row key"><code>{extNew}</code><button class="btn primary pill" onclick={() => copy(extNew, 'key')}>{extCopied === 'key' ? 'Copied' : 'Copy key'}</button></div>
     <p class="muted small">Shown once. It can only list and create boards and add images to them, for this profile.</p>
    {:else}<p><button class="btn pill" disabled={extBusy} onclick={createKey}>{extBusy ? 'Creating…' : 'Create connection key'}</button></p>{/if}
   </li>
  </ol>
  <p class="muted small"><strong>Updating:</strong> download again and replace the old files. Chrome: unzip over the same folder, then click the reload arrow on the extension's card. Firefox: load the new zip. The connection stays.</p>
  {#if extKeys.length}
   <section class="ext-keys" aria-label="Connected browsers"><h3>Connected browsers</h3>
    <ul>{#each extKeys as k (k.id)}<li><span>{k.name} <span class="muted">…{k.hint} · {k.last_used ? 'last used ' + new Date(k.last_used * 1000).toLocaleDateString() : 'not used yet'}</span></span><button class="link-btn" onclick={() => revokeKey(k)}>Revoke</button></li>{/each}</ul>
   </section>
  {/if}
  {#if error}<p class="error" role="alert">{error}</p>{/if}
 </div>
</Modal>{/if}
{#if reshuffleOpen && board}<Modal portal title="Reshuffle this board?" onClose={() => { reshuffleOpen = false; }}>
 <p>{replaceable ? `${replaceable} image${replaceable === 1 ? '' : 's'} you have not hearted or given a thumbs up will be replaced with new finds.` : 'Every image on this board is liked, so nothing will be replaced.'} Images you liked stay where they are.</p>
 <p class="muted small">Replaced images will not come back, and your curator does not learn from them. Use 👎 for images it should avoid.</p>
 <div class="form-actions"><button class="btn" onclick={() => { reshuffleOpen = false; }}>Cancel</button><button class="btn primary" disabled={reshuffling} onclick={reshuffle}>{reshuffling ? 'Reshuffling…' : replaceable ? `Replace ${replaceable} image${replaceable === 1 ? '' : 's'}` : 'Find more'}</button></div>
</Modal>{/if}
{#if leaveConfirm && board}<Modal portal title="Leave this board?" onClose={() => { leaveConfirm = false; }}><p>“{board.name}” stays with {board.owner?.name ?? 'its owner'}. The images you added stay on it.</p><div class="form-actions"><button class="btn" onclick={() => { leaveConfirm = false; }}>Stay</button><button class="btn danger" onclick={leaveBoard}>Leave board</button></div></Modal>{/if}
{#if deleteConfirm}<Modal portal title="Delete this board?" onClose={() => { deleteConfirm = false; }}><p>This removes the board and its saved pins.</p><div class="form-actions"><button class="btn" onclick={() => { deleteConfirm = false; }}>Keep board</button><button class="btn danger" onclick={removeBoard}>Delete board</button></div></Modal>{/if}

{#if preview}<PinViewer pin={preview} pins={found ?? pins} boardName={boards.find(b => b.id === preview?.board_id)?.name ?? ''} busy={busy === preview.id}
 onClose={() => { preview = null; moving = null; }} onNavigate={(p) => { preview = p; moving = null; }}
 onSave={(p) => pinAction(p, {saved: !p.saved})} onFits={(p) => review(p, p.feedback === 'good' ? '' : 'good')}
 onNotFit={(p) => { reviewing = p; reviewNote = ''; }} onHide={(p) => remove(p)}
 onShare={session.isChild || !mine(boards.find(b => b.id === preview?.board_id)) ? undefined : (p) => { sharing = {board: {id: p.board_id, name: boards.find(b => b.id === p.board_id)?.name ?? ''}, pin: p}; }}>
 {#snippet actions()}
  {#if preview?.kid_state === 'hidden' || preview?.kid_state === 'override'}
   <div class="kid-box" role="note">
    <strong>{preview.kid_state === 'hidden' ? '🧸 Hidden from kids' : '🧸 Shown to kids'}</strong>
    {#if preview.kid_reason}<p class="muted">{preview.kid_reason}</p>{/if}
    <button class="btn pill" disabled={kidBusy} onclick={() => preview && kidToggle(preview, preview.kid_state === 'hidden')}>{preview.kid_state === 'hidden' ? 'Show to kids anyway' : 'Hide from kids'}</button>
   </div>
  {:else if preview?.kid_state === 'ok'}
   <button class="btn pill" disabled={kidBusy} onclick={() => preview && kidToggle(preview, false)}>Hide from kids</button>
  {/if}
  {#if preview?.added_by}<p class="muted small">Added by {preview.added_by}</p>{/if}
  {#if boards.length > 1}
   {#if moving}<div class="move-form"><label>Destination board<select bind:value={moveTo}>{#each boards.filter(b => b.id !== preview?.board_id) as b}<option value={b.id}>{b.name}</option>{/each}</select></label><button class="btn primary" disabled={!moveTo || busy !== null} onclick={() => preview && pinAction(preview, {board_id: Number(moveTo)})}>Move image</button></div>
   {:else}<button class="btn pill" onclick={() => { moving = preview?.id ?? null; moveTo = String(boards.find(b => b.id !== preview?.board_id)?.id ?? ''); }}>Move to another board</button>{/if}
  {/if}
 {/snippet}
</PinViewer>{/if}
{#if sharing}<ShareDialog board={sharing.board} pin={sharing.pin} onClose={() => { sharing = null; }} onMembers={() => void load(false, true)} />{/if}

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

 .actions{display:flex;align-items:center;gap:.3rem;margin-top:.4rem}
 .actions .gap{flex:1}
 .act{display:inline-grid;place-items:center;width:2.1rem;height:2.1rem;padding:0;border-radius:50%;border:1px solid var(--line);background:transparent;color:var(--muted);cursor:pointer;transition:color .15s,background .15s,border-color .15s,transform .15s var(--ease)}
 .act :global(.i){width:1.05rem;height:1.05rem}
 .act:hover:not(:disabled){color:var(--text);background:var(--surface-hi)}
 .act:active:not(:disabled){transform:scale(.92)}
 .act:disabled{opacity:.5;cursor:wait}
 .act.up.on{color:#3ddc97;border-color:rgba(61,220,151,.55);background:rgba(61,220,151,.12)}
 .act.up.on :global(.i){fill:rgba(61,220,151,.25)}
 .act.down:hover:not(:disabled),.act.remove:hover:not(:disabled){color:var(--danger,#ff929d);border-color:rgba(255,146,157,.5)}
 .act.heart.on{color:var(--mb-rose);border-color:rgba(255,143,171,.55);background:rgba(255,143,171,.12)}
 .act.heart.on :global(.i){fill:currentColor}
 .pill.on{border-color:rgba(61,220,151,.5);color:#3ddc97}
 .reason-chips{display:flex;flex-wrap:wrap;gap:.4rem}
 .chip{padding:.35rem .8rem;border-radius:var(--r-pill);border:1px solid var(--line);background:var(--mb-card);color:var(--text);font:inherit;font-size:.85rem;cursor:pointer}
 .curator-belief{display:grid;gap:.2rem;font-size:.8rem;color:var(--muted);line-height:1.35;overflow-wrap:anywhere}
 .curator-belief p{margin:0}
 .chip:focus-visible{outline:2px solid var(--mb-rose);outline-offset:2px}
 .chip.on{border-color:var(--mb-rose);background:rgba(255,143,171,.15)}
 .new-pill{position:sticky;top:.8rem;z-index:5;display:block;margin:0 auto .8rem;padding:.55rem 1.1rem;border-radius:var(--r-pill);border:1px solid var(--mb-rose);background:rgba(20,16,30,.9);color:var(--text);font:inherit;cursor:pointer;box-shadow:0 8px 24px -10px rgba(0,0,0,.6);animation:pop .25s var(--ease)}
 @keyframes pop{from{transform:translateY(-6px);opacity:0}}
 .heading-actions{display:flex;gap:.6rem;flex-wrap:wrap}
 .ext{display:grid;gap:1rem}
 .ext p{margin:.3rem 0}
 .ext-tabs{justify-self:start}
 .steps{margin:0;padding-left:1.3rem;display:grid;gap:1rem}
 .steps li::marker{color:var(--mb-rose);font-weight:700}
 .steps ul{margin:.3rem 0 0;padding-left:1.1rem;display:grid;gap:.4rem}
 .steps code,.copy-row code{background:rgba(255,255,255,.06);padding:.1rem .4rem;border-radius:.4rem;font-size:.85em;overflow-wrap:anywhere}
 .copy-row{display:flex;align-items:center;gap:.6rem;flex-wrap:wrap;margin:.4rem 0}
 .copy-row code{flex:1 1 14rem;padding:.5rem .7rem}
 .ext-keys h3{margin:0 0 .4rem;font-size:.95rem}
 .ext-keys ul{list-style:none;margin:0;padding:0;display:grid;gap:.4rem}
 .ext-keys li{display:flex;justify-content:space-between;gap:1rem;align-items:center;padding:.5rem .7rem;border:1px solid var(--line);border-radius:var(--r-md)}
 .small{font-size:.8rem}
 .board-search{display:flex;gap:.6rem;align-items:center;margin-top:1.4rem;flex-wrap:wrap}
 .search-field{flex:1 1 18rem;display:flex;align-items:center;gap:.55rem;padding:.2rem 1rem;border:1px solid var(--line);border-radius:var(--r-pill);background:var(--mb-card);color:var(--muted)}
 .search-field:focus-within{border-color:var(--mb-rose)}
 .search-field input{flex:1;min-width:0;border:0;background:none;color:var(--text);padding:.6rem 0;font:inherit;outline:none}
 .search-results{margin-top:1rem}
 .results-head{display:flex;flex-direction:column;gap:.2rem;margin:.4rem 0 1rem}
 .board-link{border:0;background:none;padding:0;cursor:pointer;text-align:left;font:inherit}
 .board-link:hover{color:var(--mb-rose)}
 .board-nav{display:flex;gap:.6rem;align-items:center;flex-wrap:wrap;margin:1.1rem 0 .4rem}
 .board-nav .back{gap:.3rem}
 .switch select,.sort select{background:var(--surface-solid);border:1px solid var(--line);border-radius:var(--r-pill);color:var(--text);padding:.5rem 2rem .5rem .9rem;font:inherit;max-width:min(70vw,20rem)}
 .overview{margin-top:1.4rem}
 .overview-bar{display:flex;align-items:center;gap:.7rem;flex-wrap:wrap;margin-bottom:1rem}
 .overview-bar h2{margin:0 auto 0 0;font-size:1.1rem}
 .board-filter{display:flex;align-items:center;gap:.5rem;padding:.1rem .9rem;border:1px solid var(--line);border-radius:var(--r-pill);background:var(--mb-card);color:var(--muted);min-width:0;flex:0 1 16rem}
 .board-filter input{border:0;background:none;color:var(--text);font:inherit;padding:.5rem 0;outline:none;min-width:0;width:100%}
 .board-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,13.5rem),1fr));gap:1rem}
 .child-toggle{display:flex;gap:.7rem;align-items:flex-start;padding:.8rem 1rem;border:1px solid var(--line);border-radius:var(--r-md);margin:1rem 0 .5rem;cursor:pointer}
 .child-toggle input{margin-top:.25rem}
 .child-toggle span{display:grid;gap:.15rem}
 .child-toggle small{color:var(--muted);font-weight:400}
 .tags li.kid{border-color:rgba(255,181,71,.5);color:#ffb547}
 .board-card{display:flex;flex-direction:column;text-align:left;padding:0;border:1px solid var(--line);border-radius:var(--r-lg);background:var(--mb-card);color:var(--text);overflow:hidden;cursor:pointer;font:inherit;transition:transform .2s var(--ease),border-color .2s var(--ease),box-shadow .2s var(--ease)}
 .board-card:hover,.board-card:focus-visible{transform:translateY(-3px);border-color:var(--mb-rose);box-shadow:0 14px 30px -18px rgba(0,0,0,.8)}
 .collage{display:grid;gap:2px;aspect-ratio:4/3;background:linear-gradient(135deg,hsl(var(--hue,330) 70% 70% / .35),hsl(calc(var(--hue,330) + 40) 80% 60% / .25));overflow:hidden}
 .collage.n2{grid-template-columns:1fr 1fr}
 .collage.n3{grid-template-columns:2fr 1fr;grid-template-rows:1fr 1fr}
 .collage.n3 .cell:first-child{grid-row:span 2}
 .collage.n4{grid-template-columns:2fr 1fr;grid-template-rows:1fr 1fr 1fr}
 .collage.n4 .cell:first-child{grid-row:span 3}
 .cell{overflow:hidden;min-height:0;background:rgba(0,0,0,.25)}
 .cell :global(.media){width:100%;height:100%}
 .cell :global(img){width:100%!important;height:100%!important;max-width:none!important;max-height:none!important;object-fit:cover}
 .monogram{display:grid;place-items:center;font-size:3rem;font-weight:800;color:rgba(255,255,255,.85);letter-spacing:-.04em}
 .card-body{display:grid;gap:.2rem;padding:.75rem .9rem .9rem}
 .card-name{font-weight:700;line-height:1.25;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .card-meta{display:flex;align-items:center;gap:.3rem;color:var(--muted);font-size:.82rem}
 .card-meta :global(.i){width:.85em;height:.85em;color:var(--mb-rose)}
 .card-status{font-size:.75rem;color:var(--muted)}
 .card-status.busy{color:var(--mb-peach)}
 .card-status.bad{color:var(--danger,#ff929d)}
 .board-card.add{border-style:dashed;background:transparent}
 .add-art{display:grid;place-items:center;background:rgba(255,255,255,.03);font-size:2rem;color:var(--muted)}
 .add-art :global(.i){width:2.2rem;height:2.2rem}
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
 .pin{margin:0 0 1.2rem;animation:rise .45s var(--ease) both}
 @keyframes rise{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
 @media(prefers-reduced-motion:reduce){.pin{animation:none}}
 .frame{position:relative;border-radius:var(--r-lg);overflow:hidden;background:var(--surface);isolation:isolate}
 .picture{display:block;width:100%;padding:0;border:0;background:none;cursor:zoom-in;min-height:5rem}
 .picture :global(img){display:block;width:100%;height:auto;transition:transform .5s var(--ease),filter .3s}
 .frame::after{content:'';position:absolute;inset:0;background:linear-gradient(180deg,rgba(0,0,0,.35),transparent 30%);opacity:0;transition:opacity .25s;pointer-events:none}
 .frame:hover::after,.frame:focus-within::after{opacity:1}
 .frame:hover .picture :global(img){transform:scale(1.03)}
 .badge{position:absolute;left:.6rem;bottom:.6rem;font-size:.65rem;font-weight:800;letter-spacing:.08em;padding:.2rem .45rem;border-radius:.4rem;background:rgba(0,0,0,.6);color:#fff;pointer-events:none}
 .badge.ai{top:.6rem;bottom:auto;background:rgba(124,58,237,.85)}
 .badge.kidhide{left:auto;right:.6rem;background:rgba(214,69,69,.88);letter-spacing:.02em}
 .shared-badge{font-size:.7rem;font-weight:700;padding:.05rem .4rem;border-radius:999px;border:1px solid var(--line);color:var(--muted)}
 .people{display:flex;align-items:center;gap:.35rem;flex-wrap:wrap;margin:.2rem 0 .5rem}
 .people .person{display:inline-flex}
 .people .muted{margin-left:.3rem}
 .kid-note{margin:.2rem 0 .6rem;font-size:.9rem;color:#ffb547}
 .kid-box{display:grid;gap:.35rem;padding:.6rem .8rem;border:1px solid rgba(255,181,71,.5);border-radius:var(--r-md);margin-bottom:.5rem}
 .kid-box p{margin:0}
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

 .board-form{display:grid;gap:1rem;min-width:0}
 .board-form fieldset{display:grid;gap:.9rem;margin:0;padding:0;border:0;min-width:0}
 .board-form legend{padding:0;margin-bottom:.2rem;font-size:.72rem;letter-spacing:.12em;text-transform:uppercase;color:var(--mb-rose);font-weight:700}
 .board-form label{min-width:0}
 .board-form textarea{resize:vertical}
 .tuning{border:1px solid var(--line);border-radius:var(--r-md);padding:.2rem .9rem}
 .tuning[open]{padding-bottom:.9rem}
 .tuning summary{cursor:pointer;padding:.6rem 0;font-weight:600}
 .tuning>*+*{margin-top:.8rem}
 .choice{display:grid;gap:.5rem}
 .choice-label{font-size:.9rem}
 .opt{display:flex!important;align-items:flex-start;gap:.6rem;padding:.6rem .7rem;border:1px solid var(--line);border-radius:var(--r-md);cursor:pointer}
 .opt:has(input:checked){border-color:var(--mb-rose);background:rgba(255,143,171,.08)}
 .opt input{margin-top:.2rem;accent-color:var(--mb-rose)}
 .opt span{display:grid;gap:.15rem}
 .board-form p{margin:0}
 .board-form label,.move-form label{display:grid;gap:.4rem;font-size:.9rem}
 .board-form small{color:var(--muted)}
 .board-form .check{display:flex;align-items:center;gap:.6rem}
 .check input{width:auto}
 .form-grid{display:grid;grid-template-columns:1fr 1fr;gap:1rem;align-items:end}
 .board-form :where(input:not([type=checkbox]):not([type=range]):not([type=radio]),textarea,select),.move-form select{width:100%;box-sizing:border-box;min-width:0;background:var(--surface-solid);border:1px solid var(--line-strong);border-radius:var(--r-md);padding:.7rem;color:var(--text);font:inherit}
 .board-form input:focus,.board-form textarea:focus,.board-form select:focus{outline:2px solid var(--mb-rose);outline-offset:1px}
 .target-row{display:flex;align-items:center;gap:.8rem;min-width:0}
 .target-row input[type=range]{flex:1;min-width:0;accent-color:var(--mb-rose)}
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
