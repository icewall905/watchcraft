<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { focusable } from '$lib/nav';
	import { hub } from '$lib/hub/api';
	import { errMsg, isGalleryType, lsGet, lsSet } from '$lib/hub/util';
	import type { Collection, Item } from '$lib/hub/types';
	import ContentCard from '$lib/hub/ContentCard.svelte';

	const LIMIT = 30;
	const views = [['all', 'All saved'], ['favorites', 'Favorites'], ['later', 'Read later'], ['archive', 'Archive'], ['collections', 'Collections']] as const;
	const modes = [['auto', 'Auto'], ['grid', 'Grid'], ['list', 'List'], ['compact', 'Compact'], ['gallery', 'Gallery']] as const;
	type Mode = (typeof modes)[number][0];

	let items = $state<Item[]>([]);
	let total = $state(0);
	let collections = $state<Collection[]>([]);
	let loading = $state(true);
	let more = $state(false);
	let error = $state('');
	let mode = $state<Mode>('auto');
	let ready = $state(false);
	let seq = 0;
	let note = $state('');

	let importUrl = $state('');
	let importing = $state(false);
	let importError = $state('');

	let newName = $state('');
	let editing = $state<number | null>(null);
	let editName = $state('');
	let colError = $state('');

	const view = $derived(page.url.searchParams.get('view') ?? 'all');
	const colId = $derived(Number(page.url.searchParams.get('collection')) || null);
	const activeCol = $derived(collections.find((c) => c.id === colId) ?? null);
	const showCollections = $derived(view === 'collections' && !colId);
	const key = $derived(`${view}|${colId}`);

	const eff = $derived<'grid' | 'list' | 'compact' | 'gallery'>(
		mode !== 'auto' ? mode : items.length && items.every((i) => isGalleryType(i.type)) ? 'gallery' : 'grid'
	);
	const layout = $derived(eff === 'list' ? 'row' : eff === 'compact' ? 'compact' : 'card');

	onMount(() => {
		const m = lsGet('wc.library.mode');
		if (modes.some(([k]) => k === m)) mode = m as Mode;
		ready = true;
		loadCollections();
	});
	async function loadCollections() {
		try { collections = await hub.collections(); } catch (e) { colError = errMsg(e); }
	}

	function params() {
		const q: Record<string, string | number | boolean | undefined> = { section: 'library', limit: LIMIT, sort: 'newest' };
		if (colId) q.collection = colId;
		else if (view === 'favorites') q.favorite = true;
		else if (view === 'later') q.read_later = true;
		else if (view === 'archive') q.archived = true;
		else q.saved = true;
		return q;
	}
	async function load(append = false) {
		const id = ++seq;
		if (append) more = true; else loading = true;
		error = '';
		try {
			const r = await hub.items({ ...params(), offset: append ? items.length : 0 });
			if (id !== seq) return;
			items = append ? [...items, ...r.items.filter((n) => !items.some((o) => o.id === n.id))] : r.items;
			total = r.total;
		} catch (e) { if (id === seq) error = errMsg(e); }
		finally { if (id === seq) { loading = false; more = false; } }
	}
	$effect(() => { if (ready && !showCollections) { void key; load(false); } else if (showCollections) { loading = false; } });

	function go(v: string, col?: number) {
		const u = new URL(page.url);
		u.searchParams.delete('collection');
		if (v === 'all') u.searchParams.delete('view'); else u.searchParams.set('view', v);
		if (col) u.searchParams.set('collection', String(col));
		goto(u, { keepFocus: true, noScroll: true });
	}
	function setMode(m: Mode) { mode = m; lsSet('wc.library.mode', m); }

	function changed(it: Item) {
		// drop items that no longer belong to this view
		const s = it.state;
		const keep = colId ? true : view === 'favorites' ? s.favorite : view === 'later' ? s.read_later : view === 'archive' ? s.archived : s.saved && !s.archived;
		if (!keep || s.dismissed) { items = items.filter((x) => x.id !== it.id); total = Math.max(0, total - 1); }
	}
	async function archive(i: number) {
		const it = items[i];
		try {
			const state = await hub.setState(it.id, { archived: !it.state.archived });
			items[i] = { ...it, state };
			changed(items[i]);
		} catch (e) { note = errMsg(e); }
	}
	async function addTo(it: Item, id: number) {
		if (!id) return;
		try {
			await hub.addToCollection(id, it.id);
			note = `Added to ${collections.find((c) => c.id === id)?.name ?? 'collection'}.`;
			loadCollections();
		} catch (e) { note = errMsg(e); }
	}
	async function removeFrom(it: Item) {
		if (!colId) return;
		try { await hub.removeFromCollection(colId, it.id); items = items.filter((x) => x.id !== it.id); total = Math.max(0, total - 1); loadCollections(); }
		catch (e) { note = errMsg(e); }
	}

	async function doImport(e: SubmitEvent) {
		e.preventDefault();
		const url = importUrl.trim();
		if (!url || importing) return;
		importing = true; importError = '';
		try {
			const it = await hub.importUrl(url);
			importUrl = '';
			note = `Imported "${it.title}".`;
			if (view === 'all' && !colId) { items = [it, ...items.filter((x) => x.id !== it.id)]; total += 1; }
		} catch (err) { importError = errMsg(err); }
		finally { importing = false; }
	}

	async function createCol(e: SubmitEvent) {
		e.preventDefault();
		const name = newName.trim();
		if (!name) return;
		colError = '';
		try { const c = await hub.createCollection({ name }); collections = [...collections, c]; newName = ''; }
		catch (err) { colError = errMsg(err); }
	}
	async function renameCol(c: Collection) {
		const name = editName.trim();
		if (!name || name === c.name) { editing = null; return; }
		try { const u = await hub.updateCollection(c.id, { name }); collections = collections.map((x) => (x.id === c.id ? { ...x, ...u } : x)); editing = null; }
		catch (err) { colError = errMsg(err); }
	}
	async function pinCol(c: Collection) {
		try { const u = await hub.updateCollection(c.id, { pinned: !c.pinned }); collections = collections.map((x) => (x.id === c.id ? { ...x, ...u } : x)); }
		catch (err) { colError = errMsg(err); }
	}
	async function delCol(c: Collection) {
		if (!confirm(`Delete collection "${c.name}"? Items stay in your library.`)) return;
		try { await hub.deleteCollection(c.id); collections = collections.filter((x) => x.id !== c.id); if (colId === c.id) go('collections'); }
		catch (err) { colError = errMsg(err); }
	}

	function exportJson() {
		const blob = new Blob([JSON.stringify(items.map((i) => ({ id: i.id, title: i.title, url: i.url, type: i.type, publisher: i.publisher, published_at: i.published_at, summary: i.summary, tags: i.state.tags, notes: i.state.notes, meta: i.meta })), null, 2)], { type: 'application/json' });
		const a = document.createElement('a');
		a.href = URL.createObjectURL(blob);
		a.download = `watchcraft-${activeCol ? activeCol.name.replace(/\W+/g, '-').toLowerCase() : view}.json`;
		document.body.appendChild(a); a.click(); a.remove();
		setTimeout(() => URL.revokeObjectURL(a.href), 1000);
	}
</script>

<svelte:head><title>Library · WatchCraft</title></svelte:head>

<div class="page">
	<header class="head">
		<div>
			<h1>{activeCol ? activeCol.name : 'Library'}</h1>
			<p class="muted">{activeCol ? (activeCol.description || 'Collection') : "Everything you've chosen to keep."}</p>
		</div>
		{#if !showCollections}
			<div class="hstack">
				<div class="seg" role="group" aria-label="Layout">
					{#each modes as [k, name]}<button class="btn" aria-pressed={mode === k} use:focusable onclick={() => setMode(k)}>{name}</button>{/each}
				</div>
				<button class="btn" disabled={!items.length} use:focusable onclick={exportJson}>Export JSON</button>
			</div>
		{/if}
	</header>

	<div class="tabs" role="tablist" aria-label="Library views">
		{#each views as [k, name]}
			<button role="tab" aria-selected={view === k && !colId} class="chip tab" class:on={view === k && !colId} use:focusable onclick={() => go(k)}>{name}</button>
		{/each}
		{#if activeCol}<span class="chip on tab">{activeCol.name}</span>{/if}
	</div>

	<form class="import" onsubmit={doImport}>
		<input class="input" type="url" bind:value={importUrl} placeholder="Import a page by URL (https://…)" aria-label="URL to import" aria-invalid={!!importError} />
		<button class="btn" disabled={!importUrl.trim() || importing} use:focusable>{importing ? 'Importing…' : 'Import'}</button>
	</form>
	{#if importError}<p class="err" role="alert">Couldn't import: {importError}</p>{/if}
	{#if note}<p class="note small" role="status">{note}</p>{/if}

	{#if showCollections}
		<form class="hstack" onsubmit={createCol}>
			<input class="input newc" bind:value={newName} placeholder="New collection name" aria-label="New collection name" maxlength="80" />
			<button class="btn primary" disabled={!newName.trim()} use:focusable>Create collection</button>
		</form>
		{#if colError}<p class="err" role="alert">{colError}</p>{/if}
		{#if !collections.length}
			<div class="empty"><span class="big-emoji">🗂</span><h2>No collections yet</h2><p>Group saved items into collections like "Dinner ideas" or "Security reading".</p></div>
		{:else}
			<div class="grid-cards cols">
				{#each [...collections].sort((a, b) => Number(b.pinned) - Number(a.pinned)) as c (c.id)}
					<article class="col">
						{#if editing === c.id}
							<form class="hstack" onsubmit={(e) => { e.preventDefault(); renameCol(c); }}>
								<input class="input" bind:value={editName} aria-label="Collection name" />
								<button class="btn primary" use:focusable>Save</button>
								<button type="button" class="btn ghost" use:focusable onclick={() => (editing = null)}>Cancel</button>
							</form>
						{:else}
							<a class="open" href={`/library?view=collections&collection=${c.id}`} use:focusable>
								<h3>{c.pinned ? '📌 ' : ''}{c.name}</h3>
								<p class="muted small">{c.count} item{c.count === 1 ? '' : 's'}</p>
							</a>
							<div class="hstack">
								<button class="btn ghost" use:focusable onclick={() => { editing = c.id; editName = c.name; }}>Rename</button>
								<button class="btn ghost" aria-pressed={c.pinned} use:focusable onclick={() => pinCol(c)}>{c.pinned ? 'Unpin' : 'Pin'}</button>
								<button class="btn ghost danger" use:focusable onclick={() => delCol(c)}>Delete</button>
							</div>
						{/if}
					</article>
				{/each}
			</div>
		{/if}
	{:else if loading}
		<div class={eff === 'list' || eff === 'compact' ? 'stack' : 'grid-cards'} aria-busy="true" aria-label="Loading">
			{#each Array(6) as _}<div class="skeleton sk"></div>{/each}
		</div>
	{:else if error}
		<div class="empty" role="alert"><p>Couldn't load your library: {error}</p><button class="btn primary" use:focusable onclick={() => load(false)}>Retry</button></div>
	{:else if !items.length}
		<div class="empty">
			<span class="big-emoji">📚</span>
			<h2>{view === 'favorites' ? 'No favorites yet' : view === 'later' ? 'Nothing queued' : view === 'archive' ? 'Archive is empty' : colId ? 'Empty collection' : 'Your library is empty'}</h2>
			<p>Save items from Discover or News, or import a page by URL above.</p>
			<a class="btn primary" href="/discover" use:focusable>Browse Discover</a>
		</div>
	{:else}
		<p class="faint small">{total} item{total === 1 ? '' : 's'}</p>
		<div class={eff === 'list' || eff === 'compact' ? 'stack' : eff === 'gallery' ? 'gallery' : 'grid-cards'}>
			{#each items as it, i (it.id)}
				<ContentCard bind:item={items[i]} {layout} onchange={changed}>
					{#snippet extra(item)}
						<button class="btn ghost" use:focusable onclick={() => archive(i)}>{item.state.archived ? 'Unarchive' : 'Archive'}</button>
						{#if colId}
							<button class="btn ghost" use:focusable onclick={() => removeFrom(item)}>Remove from collection</button>
						{:else if collections.length}
							<select class="input addc" aria-label={`Add "${item.title}" to collection`} value="" onchange={(e) => { addTo(item, Number(e.currentTarget.value)); e.currentTarget.value = ''; }}>
								<option value="">Add to collection…</option>
								{#each collections as c}<option value={c.id}>{c.name}</option>{/each}
							</select>
						{/if}
					{/snippet}
				</ContentCard>
			{/each}
		</div>
		{#if items.length < total}
			<div class="moreb"><button class="btn big" disabled={more} use:focusable onclick={() => load(true)}>{more ? 'Loading…' : `Load more (${total - items.length} left)`}</button></div>
		{/if}
	{/if}
</div>

<style>
	.page { display: grid; gap: 1rem; align-content: start; }
	.head { display: flex; justify-content: space-between; align-items: end; gap: 1rem; flex-wrap: wrap; }
	.seg { display: inline-flex; flex-wrap: wrap; }
	.seg .btn { border-radius: 0; min-height: 2.75rem; padding: .4rem .8rem; }
	.seg .btn:first-child { border-radius: var(--r-pill) 0 0 var(--r-pill); }
	.seg .btn:last-child { border-radius: 0 var(--r-pill) var(--r-pill) 0; }
	.seg .btn[aria-pressed='true'] { background: var(--accent); color: var(--accent-ink); }
	.tabs { display: flex; gap: .5rem; flex-wrap: wrap; }
	.tab { min-height: 2.75rem; padding: .4rem 1rem; }
	.import { display: flex; gap: .5rem; max-width: 44rem; }
	.import .input { flex: 1; }
	.newc { max-width: 22rem; }
	.err { color: var(--bad); font-size: .88rem; }
	.note { color: var(--ok); }
	.gallery { columns: 16rem; column-gap: 1rem; }
	.gallery > :global(*) { break-inside: avoid; margin-bottom: 1rem; }
	.col { border: 1px solid var(--line); background: var(--surface); border-radius: var(--r-lg); padding: 1rem 1.1rem; display: grid; gap: .5rem; }
	.col h3 { margin-bottom: .2rem; }
	.open { display: block; min-height: 2.75rem; }
	.addc { width: auto; min-height: 2.75rem; font-size: .85rem; padding: .2rem .5rem; background: rgba(10, 8, 25, .85); }
	.sk { height: 16rem; }
	.moreb { display: grid; place-items: center; padding: 1rem; }
	@media (max-width: 760px) { .tabs { overflow-x: auto; flex-wrap: nowrap; } .import { flex-direction: column; } }
</style>
