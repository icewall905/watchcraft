<script lang="ts">
 import { onMount, untrack } from 'svelte';
 import { page } from '$app/state';
 import { goto } from '$app/navigation';
 import { session } from '$lib/stores/session.svelte';
 import CommonsSwitch from '$lib/commonroom/CommonsSwitch.svelte';
 import { crApi, type Thread, type PlatformInfo, type Post, type Mode, type Kind } from '$lib/hub/commonroom';
 import { KINDS, MODES } from '$lib/commonroom/util';
 import PostCard from '$lib/commonroom/PostCard.svelte';
 import PostView from '$lib/commonroom/PostView.svelte';
 import ThreadEditor from '$lib/commonroom/ThreadEditor.svelte';
 let threads = $state<Thread[]>([]), platforms = $state<PlatformInfo[]>([]), child = $state(false), selected = $state<'all' | number>('all');
 let newPosts = $state<Post[]>([]), deepPosts = $state<Post[]>([]), section = $state<'new' | 'deep'>('new'), cursor = $state(''), hasMore = $state(false), building = $state(false);
 let loaded = $state(false), loading = $state(false), error = $state(''), boundary = $state(0), editing = $state<Thread | 'new' | null>(null), open = $state<Post | null>(null), running = $state(false);
 let wallWidth = $state(0), ended = $state(false);
 // The view (Today, Hot, …) is a per-viewer convenience; storage can be missing in private windows.
 const MODE_KEY = 'commons.mode';
 const readMode = (): Mode => { try { const m = localStorage.getItem(MODE_KEY); return MODES.some((x) => x.key === m) ? m as Mode : 'today'; } catch { return 'today'; } };
 let mode = $state<Mode>(readMode()), kind = $state<Kind | ''>('');
 function setMode(m: Mode) { if (m === mode) return; mode = m; try { localStorage.setItem(MODE_KEY, m); } catch { /* ignore */ } void start(selected); }
 // A badge counts picks since the last New visit, so a chip with a badge opens New (just this once, not saved).
 function openThread(thread: 'all' | number, fresh: number) { if (fresh) mode = 'new'; void start(thread); }
 function setKind(k: Kind | '') { kind = kind === k ? '' : k; void start(selected); }
 const COL_PX = 384, GAP_PX = 14; // 24rem columns
 const cols = $derived(Math.max(1, Math.min(4, Math.floor((wallWidth + GAP_PX) / (COL_PX + GAP_PX)))));
 let sentinel = $state<HTMLDivElement>();
 let generation = 0, identity = '', controller: AbortController | null = null;
 const allowed = () => session.hubAllowed && session.ready && !session.needsLogin && !session.choosing;
 const stamp = () => `${session.profileId}:${session.version}`;
 const msg = (e: unknown) => e instanceof Error ? e.message : 'Could not load the Common Room.';
 const current = $derived(selected === 'all' ? null : threads.find((t) => t.id === selected) ?? null);
 const allNew = $derived(threads.reduce((n, t) => n + (t.new_count || 0), 0));
 const shown = $derived(newPosts.length + deepPosts.length);
 const activeStatus = (s?: string) => s === 'queued' || s === 'running' || s === 'collecting';
 const gathering = $derived(mode !== 'saved' && !kind && !shown && !loading && loaded && !hasMore && (selected === 'all' ? threads.some((t) => activeStatus(t.last_status) || !t.last_run) : !current?.last_run || activeStatus(current.last_status)));
 const statusText = $derived((selected === 'all' ? threads.find((t) => activeStatus(t.last_status))?.last_status : current?.last_status) || '');

 async function loadThreads() {
  const d = await crApi.threads(); threads = d.threads; platforms = d.platforms; child = d.child;
  if (selected !== 'all' && !threads.some((t) => t.id === selected)) selected = 'all';
 }
 async function start(thread: 'all' | number) {
  controller?.abort(); controller = null; const token = ++generation;
  newPosts = []; deepPosts = []; section = mode === 'new' ? 'new' : 'deep'; cursor = ''; hasMore = false; building = false; error = ''; loading = false; selected = thread; loaded = false; ended = false;
  // Only the New view starts a visit: browsing Today or Hot must not use up what is new.
  if (mode === 'new') {
   try {
    const v = await crApi.visit(thread);
    if (token !== generation) return;
    boundary = v.boundary;
    threads = threads.map((t) => thread === 'all' || t.id === thread ? { ...t, new_count: 0 } : t);  // this visit catches them up
   } catch (e) { if (token === generation) { error = msg(e); loaded = true; } return; }
  }
  hasMore = true; await more(token);
  if (token === generation) loaded = true;
 }
 // Pull pages until something shows: New pages first, then the endless Deep section.
 async function more(token = generation) {
  if (loading || !allowed()) return;
  loading = true; controller = new AbortController(); const signal = controller.signal, owner = stamp();
  try {
   for (let guard = 0; guard < 6; guard++) {
    const view = mode === 'new' ? {section, boundary} : {mode};
    const page = await crApi.feed({thread: selected, ...view, kind: kind || undefined, cursor: cursor || undefined}, signal);
    if (token !== generation || owner !== stamp()) return;
    const have = new Set([...newPosts, ...deepPosts].map((p) => p.pick_id));
    const fresh = page.posts.filter((p) => !have.has(p.pick_id));
    if (section === 'new') newPosts = [...newPosts, ...fresh]; else deepPosts = [...deepPosts, ...fresh];
    building = page.building; cursor = page.next_cursor || cursor; hasMore = page.has_more; ended = !!page.end;
    if (mode !== 'new') break;
    if (page.has_more) { if (fresh.length || section === 'deep') break; continue; }
    if (section === 'new') { section = 'deep'; cursor = ''; hasMore = true; building = false; if (deepPosts.length) break; continue; }
    break;
   }
  } catch (e) { if (!signal.aborted && token === generation) error = msg(e); }
  finally { if (token === generation) { loading = false; controller = null; } }
 }
 $effect(() => {
  const key = `${session.profileId}:${session.version}:${session.profile?.pin_locked}:${session.needsLogin}:${session.choosing}:${session.ready}`;
  if (key !== identity) { identity = key; untrack(() => { if (!allowed()) return; error = ''; void loadThreads().then(() => start('all'), (e) => { error = msg(e); loaded = true; }); }); }
 });
 // A Front Page card opens one post directly (?post=<post id>).
 $effect(() => {
  const id = Number(page.url.searchParams.get('post')) || 0;
  if (!id || !session.ready || !allowed()) return;
  untrack(() => { void crApi.post(id).then((p) => { open = p; }, () => {}); });
 });
 function closePost() {
  open = null;
  if (page.url.searchParams.has('post')) { const u = new URL(page.url); u.searchParams.delete('post'); void goto(u, {replaceState: true, keepFocus: true, noScroll: true}); }
 }
 $effect(() => {
  const node = sentinel;
  if (!node || !hasMore || loading) return;
  const o = new IntersectionObserver((e) => { if (e.some((x) => x.isIntersecting)) void more(); }, {rootMargin: '1500px'});
  o.observe(node);
  return () => o.disconnect();
 });
 // Deep section dry while the curator is still building: retry the same cursor every 20 s while visible.
 onMount(() => {
  const timer = window.setInterval(() => {
   if (document.visibilityState === 'hidden' || !allowed() || loading || !loaded) return;
   if (section === 'deep' && !hasMore && building) { hasMore = true; void more(); }
   if (gathering || (current && activeStatus(current.last_status))) { void loadThreads().catch(() => {}); if (gathering) void start(selected); }
  }, 20000);
  return () => { window.clearInterval(timer); controller?.abort(); flush(true); };
 });

 // Impressions: a card counts as seen once most of it was on screen. Batched, ~2 s debounce.
 const pending = new Set<number>(), sent = new Set<number>();
 let flushTimer: ReturnType<typeof setTimeout> | null = null, seenObs: IntersectionObserver | null = null;
 function flush(keepalive = false) {
  if (flushTimer) { clearTimeout(flushTimer); flushTimer = null; }
  if (!pending.size) return;
  const ids = [...pending]; pending.clear();
  void crApi.seen(ids, keepalive).catch(() => {});
 }
 function seen(node: HTMLElement, id: number) {
  if (typeof IntersectionObserver === 'undefined') return {};
  seenObs ??= new IntersectionObserver((entries) => {
   for (const e of entries) if (e.isIntersecting) {
    const pid = Number((e.target as HTMLElement).dataset.pick); seenObs?.unobserve(e.target);
    if (!pid || sent.has(pid)) continue; sent.add(pid); pending.add(pid);
    flushTimer ??= setTimeout(() => flush(), 2000);
   }
  }, {threshold: 0.6});
  node.dataset.pick = String(id); seenObs.observe(node);
  return {destroy() { seenObs?.unobserve(node); }};
 }
 onMount(() => {
  const hide = () => { if (document.visibilityState === 'hidden') flush(true); };
  document.addEventListener('visibilitychange', hide); window.addEventListener('pagehide', hide);
  return () => { document.removeEventListener('visibilitychange', hide); window.removeEventListener('pagehide', hide); seenObs?.disconnect(); };
 });

 const gone = (p: Post) => { newPosts = newPosts.filter((x) => x.pick_id !== p.pick_id); deepPosts = deepPosts.filter((x) => x.pick_id !== p.pick_id); };
 async function runNow() {
  if (!current || running) return;
  running = true;
  try { await crApi.run(current.id); threads = threads.map((t) => t.id === current.id ? {...t, last_status: 'queued'} : t); } catch (e) { error = msg(e); }
  finally { running = false; }
 }
 async function saved(t: Thread) {
  const isNew = editing === 'new'; editing = null;
  try { await loadThreads(); } catch { /* keep list */ }
  if (isNew) { void crApi.run(t.id).catch(() => {}); await start(t.id); }
 }
 async function deleted() { editing = null; try { await loadThreads(); } catch { /* keep */ } await start('all'); }
</script>

<svelte:head><title>The Commons · The Hub</title></svelte:head>
{#snippet wall(list: Post[])}
 <!-- Desktop: posts dealt left to right into columns that each stack tightly (no holes under short cards);
      new pages append to the same columns, so nothing already on screen moves. -->
 <div class="feed" style:--cols={cols}>
  {#each Array.from({ length: cols }, (_, c) => list.filter((_, i) => i % cols === c)) as column, c (c)}
   <div class="col">{#each column as p (p.pick_id)}<div class="item" use:seen={p.pick_id}><PostCard post={p} onOpen={(x) => { open = x; }} onGone={gone} /></div>{/each}</div>
  {/each}
 </div>
{/snippet}

<div class="cr" bind:clientWidth={wallWidth}>
 <header class="head">
  <div><p class="eyebrow">Explore</p><h1>The Commons</h1></div>
  {#if current}<div class="acts">
   <button class="btn" disabled={running || activeStatus(current.last_status)} onclick={runNow}>{activeStatus(current.last_status) ? 'Gathering…' : 'Run now'}</button>
   <button class="btn" onclick={() => { editing = current; }}>Edit thread</button>
  </div>{/if}
 </header>
 <CommonsSwitch />
 {#if session.isAdmin && session.settings && !session.settings.commonroom?.reddit_client_id}
  <p class="keys small">Reddit runs in limited mode (public hot and top lists, no comments or search). <a href="/settings?tab=commonroom">Add Reddit keys in Settings</a></p>
 {/if}
 {#if loaded || threads.length}
 <nav class="chips" aria-label="Threads">
  <button type="button" class="chip" class:on={selected === 'all'} aria-pressed={selected === 'all'} onclick={() => openThread('all', allNew)}>All{#if allNew}<span class="badge" title="{allNew} new since your last New visit">{allNew}</span>{/if}</button>
  {#each threads as t (t.id)}
   <button type="button" class="chip" class:on={selected === t.id} aria-pressed={selected === t.id} onclick={() => openThread(t.id, t.new_count)}>{t.name}{#if t.new_count}<span class="badge" title="{t.new_count} new since your last New visit">{t.new_count}</span>{/if}</button>
  {/each}
  <button type="button" class="chip add" onclick={() => { editing = 'new'; }}>+ Thread</button>
 </nav>
 {#if threads.length}
 <div class="views">
  <div class="modes" role="tablist" aria-label="View">
   {#each MODES as m (m.key)}
    <button type="button" role="tab" class="mode" class:on={mode === m.key} aria-selected={mode === m.key} title={m.hint} onclick={() => setMode(m.key)}>{m.label}</button>
   {/each}
  </div>
  <nav class="kinds" aria-label="Kind of post">
   {#each KINDS as k (k.key)}
    <button type="button" class="kind" class:on={kind === k.key} aria-pressed={kind === k.key} onclick={() => setKind(k.key)}>{k.icon} {k.label}</button>
   {/each}
  </nav>
 </div>
 {/if}
 {/if}
 {#if error}<p class="err" role="alert">{error} <button class="btn sm" onclick={() => start(selected)}>Retry</button></p>{/if}

 {#if loaded && !threads.length && !error}
  <section class="empty">
   <h2>Start your first Thread</h2>
   <p>A Thread is one interest, like "retro computing" or "home espresso". Your curator finds communities on Reddit, Lemmy, Mastodon, Bluesky, Hacker News and more, then keeps only the posts that fit. Rate posts and it learns your taste.</p>
   <button class="btn primary" onclick={() => { editing = 'new'; }}>Create a thread</button>
  </section>
 {:else}
  {#if newPosts.length}
   <h2 class="sec">New since your last visit</h2>
   {@render wall(newPosts)}
  {/if}
  {#if section === 'deep'}
   {#if mode === 'new' && (shown || !gathering)}<div class="divider" role="separator"><span>You're caught up — deeper picks below</span></div>{/if}
   {@render wall(deepPosts)}
  {/if}
  {#if mode === 'today' && ended && shown}
   <section class="endcard">
    <h2>That's today.</h2>
    <p class="muted">{shown} picks across your threads. Come back tomorrow, or keep going.</p>
    <div class="acts"><button class="btn primary" onclick={() => setMode('hot')}>🔥 What's hot</button><button class="btn" onclick={() => setMode('new')}>Everything new</button></div>
   </section>
  {/if}
  {#if loaded && !loading && !shown && !gathering && !error}
   <section class="empty">{#if mode === 'saved'}<h2>Nothing saved yet</h2><p class="muted">Star a post with ☆ to keep it here.</p>{:else if kind}<h2>No {KINDS.find((k) => k.key === kind)?.label.toLowerCase()} here yet</h2><p class="muted">Posts curated before kinds existed have none; new picks will.</p>{:else}<h2>Nothing here yet</h2>{/if}</section>
  {/if}
  {#if gathering}
   <section class="empty"><h2>Your curator is gathering posts…</h2><p class="muted">{statusText ? `Status: ${statusText}` : 'This can take a few minutes.'}{#if current?.error} · {current.error}{/if}</p></section>
  {:else if loading}<p class="muted center">Loading…</p>
  {:else if section === 'deep' && !hasMore && building}<p class="muted center">Finding more posts, and new places to find them…</p>
  {:else if section === 'deep' && !hasMore && shown && mode !== 'today'}<p class="muted center">That's everything for now.</p>{/if}
  {#if hasMore && !loading}
   <div bind:this={sentinel} class="sentinel"></div>
   <p class="center"><button class="btn" onclick={() => more()}>Load more</button></p>
  {/if}
 {/if}
</div>
{#if open}<PostView post={open} onClose={closePost} onGone={gone} />{/if}
{#if editing}<ThreadEditor thread={editing === 'new' ? null : editing} {platforms} {child} onSaved={saved} onDeleted={deleted} onClose={() => { editing = null; }} />{/if}

<style>
 .cr{max-width:46rem;margin:auto;padding:clamp(1rem,3vw,2rem);display:grid;gap:1rem;min-width:0}
 .keys{margin:.6rem 0 0;color:var(--muted)}
 .head{display:flex;justify-content:space-between;align-items:flex-end;gap:1rem;flex-wrap:wrap}
 .eyebrow{margin:0 0 .3rem;font-size:.75rem;letter-spacing:.14em;text-transform:uppercase;color:var(--ai);font-weight:700}
 h1{margin:0;font-size:clamp(1.8rem,4vw,2.6rem);letter-spacing:-.04em;line-height:1}
 .acts{display:flex;gap:.5rem}
 .chips{display:flex;gap:.5rem;overflow-x:auto;padding-bottom:.3rem;scrollbar-width:thin}
 .chip{flex:none;display:inline-flex;gap:.4rem;align-items:center;padding:.4rem .9rem;border-radius:var(--r-pill);border:1px solid var(--line);background:var(--surface);color:var(--text);font:inherit;font-size:.9rem;cursor:pointer}
 .chip.on{border-color:var(--ai);background:rgba(157,134,255,.18)}
 .chip.add{border-style:dashed;color:var(--muted)}
 .badge{min-width:1.3rem;padding:0 .35rem;border-radius:var(--r-pill);background:var(--accent);color:var(--accent-ink);font-size:.72rem;font-weight:700;text-align:center}
 .sec{margin:.4rem 0 0;font-size:.85rem;text-transform:uppercase;letter-spacing:.1em;color:var(--muted)}
 .feed{display:grid;gap:.9rem}
 /* Desktop: the wall widens into columns, read left to right; a phone keeps the single column. */
 .feed{grid-template-columns:repeat(var(--cols,1),minmax(0,1fr));align-items:start}
 .col{display:grid;gap:.9rem;min-width:0}
 /* Desktop: the wall widens so several columns fit; a phone keeps the single column. */
 @media (min-width:1100px){.cr{max-width:112rem}}
 .divider{display:flex;align-items:center;gap:.8rem;color:var(--muted);font-size:.85rem;margin:.6rem 0}
 .divider::before,.divider::after{content:'';flex:1;height:1px;background:var(--line-strong)}
 .empty{padding:2rem 1rem;text-align:center;border:1px dashed var(--line-strong);border-radius:var(--r-md);display:grid;gap:.7rem;justify-items:center}
 .empty h2{margin:0}.empty p{margin:0;max-width:34rem}
 .center{text-align:center}.muted{color:var(--muted)}.err{color:var(--bad)}
 .sentinel{height:1px}
 .views{display:grid;gap:.6rem}
 .modes{display:flex;gap:.2rem;padding:.25rem;border-radius:var(--r-pill);background:var(--surface);border:1px solid var(--line);width:max-content;max-width:100%;overflow-x:auto}
 .mode{flex:none;padding:.4rem 1rem;border-radius:var(--r-pill);border:0;background:none;color:var(--muted);font:inherit;font-weight:600;cursor:pointer}
 .mode.on{background:var(--ai);color:#fff}
 .kinds{display:flex;gap:.4rem;overflow-x:auto;padding-bottom:.2rem;scrollbar-width:thin}
 .kind{flex:none;padding:.25rem .7rem;border-radius:var(--r-pill);border:1px solid var(--line);background:none;color:var(--text);font:inherit;font-size:.82rem;cursor:pointer}
 .kind.on{border-color:var(--accent);background:color-mix(in srgb, var(--accent) 18%, transparent)}
 .endcard{padding:2rem 1rem;text-align:center;border-radius:var(--r-md);background:linear-gradient(135deg, rgba(157,134,255,.16), transparent);border:1px solid var(--line);display:grid;gap:.6rem;justify-items:center}
 .endcard h2,.endcard p{margin:0}
</style>
