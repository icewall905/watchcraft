<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { focusable } from '$lib/nav';
	import { hub } from '$lib/hub/api';
	import { sinceTs, lsGet, lsSet, errMsg } from '$lib/hub/util';
	import type { Item } from '$lib/hub/types';
	import ContentCard from '$lib/hub/ContentCard.svelte';
	import ItemFilters from '$lib/hub/ItemFilters.svelte';

	const LIMIT = 30;
	const LAST = 'wc.discover.lastVisit';
	const tabs = [['all', 'All'], ['unread', 'Unread'], ['new', 'New since last visit'], ['saved', 'Saved']] as const;

	let items = $state<Item[]>([]);
	let total = $state(0);
	let loading = $state(true);
	let more = $state(false);
	let error = $state('');
	let view = $state<'grid' | 'list'>('grid');
	let lastVisit = $state<number | null>(null);
	let ready = $state(false);
	let seq = 0;

	const tab = $derived(page.url.searchParams.get('tab') ?? 'all');
	const query = $derived.by(() => {
		const p = page.url.searchParams;
		const q: Record<string, string | number | boolean | undefined> = {
			section: 'discover', limit: LIMIT,
			type: p.get('type') ?? undefined, provider: p.get('provider') ?? undefined, curator: p.get('curator') ?? undefined,
			min_score: p.get('min_score') ?? undefined, since: sinceTs(p.get('since')), sort: p.get('sort') ?? undefined,
			unread: tab === 'unread' || p.get('unread') === '1', saved: tab === 'saved' || p.get('saved') === '1'
		};
		if (tab === 'new' && lastVisit) q.since = Math.max(Number(q.since ?? 0), lastVisit);
		return q;
	});

	onMount(() => {
		const v = Number(lsGet(LAST));
		lastVisit = v > 0 ? v : null;
		view = lsGet('wc.discover.view') === 'list' ? 'list' : 'grid';
		ready = true;
		// Record this visit only when leaving, so "new since last visit" stays stable while browsing.
		const stamp = () => lsSet(LAST, String(Math.floor(Date.now() / 1000)));
		window.addEventListener('pagehide', stamp);
		return () => { window.removeEventListener('pagehide', stamp); stamp(); };
	});

	async function load(append = false) {
		const id = ++seq;
		if (append) more = true; else { loading = true; }
		error = '';
		try {
			const r = await hub.items({ ...query, offset: append ? items.length : 0 });
			if (id !== seq) return;
			items = append ? [...items, ...r.items.filter((n) => !items.some((o) => o.id === n.id))] : r.items;
			total = r.total;
		} catch (e) { if (id === seq) error = errMsg(e); }
		finally { if (id === seq) { loading = false; more = false; } }
	}
	$effect(() => { if (ready) { void JSON.stringify(query); load(false); } });

	function setTab(t: string) {
		const u = new URL(page.url);
		if (t === 'all') u.searchParams.delete('tab'); else u.searchParams.set('tab', t);
		goto(u, { replaceState: true, keepFocus: true, noScroll: true });
	}
	function changed(it: Item) {
		if (it.state.dismissed) { items = items.filter((x) => x.id !== it.id); total = Math.max(0, total - 1); }
	}
	function setView(v: 'grid' | 'list') { view = v; lsSet('wc.discover.view', v); }
</script>

<svelte:head><title>Discover · WatchCraft</title></svelte:head>

<div class="page">
	<header class="head">
		<div>
			<h1>Discover</h1>
			<p class="muted">What have my curators found?</p>
		</div>
		<div class="hstack">
			<a class="btn ghost" href="/curators" use:focusable>Manage curators</a>
			<div class="seg" role="group" aria-label="Layout">
				<button class="btn" aria-pressed={view === 'grid'} use:focusable onclick={() => setView('grid')}>Grid</button>
				<button class="btn" aria-pressed={view === 'list'} use:focusable onclick={() => setView('list')}>List</button>
			</div>
		</div>
	</header>

	<div class="tabs" role="tablist" aria-label="Discover views">
		{#each tabs as [k, name]}
			<button role="tab" aria-selected={tab === k} class="chip tab" class:on={tab === k} use:focusable onclick={() => setTab(k)}>{name}</button>
		{/each}
		{#if !loading && !error}<span class="faint small count">{total} item{total === 1 ? '' : 's'}</span>{/if}
	</div>
	{#if tab === 'new' && !lastVisit && !loading}<p class="muted small">No earlier visit recorded, so everything counts as new once you leave and return.</p>{/if}

	<ItemFilters hide={tab === 'unread' ? ['unread'] : tab === 'saved' ? ['saved'] : []} />

	{#if loading}
		<div class={view === 'grid' ? 'grid-cards' : 'stack'} aria-busy="true" aria-label="Loading">
			{#each Array(view === 'grid' ? 6 : 4) as _}<div class="skeleton sk"></div>{/each}
		</div>
	{:else if error}
		<div class="empty" role="alert">
			<p>Couldn't load discoveries: {error}</p>
			<button class="btn primary" use:focusable onclick={() => load(false)}>Retry</button>
		</div>
	{:else if !items.length}
		<div class="empty">
			<span class="big-emoji">✧</span>
			{#if tab === 'all' && !page.url.search}
				<h2>Nothing found yet</h2>
				<p>Curators watch the internet for you and drop what matches here.</p>
				<a class="btn primary big" href="/curators/new" use:focusable>Create your first curator</a>
			{:else}
				<h2>Nothing matches</h2>
				<p>{tab === 'unread' ? 'You are all caught up.' : tab === 'saved' ? 'Save something and it shows up here.' : 'Try loosening the filters.'}</p>
				<a class="btn" href="/discover" use:focusable>Reset</a>
			{/if}
		</div>
	{:else}
		<div class={view === 'grid' ? 'grid-cards' : 'stack'}>
			{#each items as it, i (it.id)}
				<ContentCard bind:item={items[i]} layout={view === 'grid' ? 'card' : 'row'} onchange={changed} />
			{/each}
		</div>
		{#if items.length < total}
			<div class="more"><button class="btn big" disabled={more} use:focusable onclick={() => load(true)}>{more ? 'Loading…' : `Load more (${total - items.length} left)`}</button></div>
		{/if}
	{/if}
</div>

<style>
	.page { display: grid; gap: 1rem; align-content: start; }
	.head { display: flex; justify-content: space-between; align-items: end; gap: 1rem; flex-wrap: wrap; }
	.seg { display: inline-flex; }
	.seg .btn { border-radius: 0; min-height: 2.75rem; }
	.seg .btn:first-child { border-radius: var(--r-pill) 0 0 var(--r-pill); }
	.seg .btn:last-child { border-radius: 0 var(--r-pill) var(--r-pill) 0; }
	.seg .btn[aria-pressed='true'] { background: var(--accent); color: var(--accent-ink); }
	.tabs { display: flex; gap: .5rem; flex-wrap: wrap; align-items: center; }
	.tab { min-height: 2.75rem; padding: .4rem 1rem; }
	.count { margin-left: auto; }
	.sk { height: 18rem; }
	.more { display: grid; place-items: center; padding: 1rem; }
	@media (max-width: 760px) { .head { align-items: start; } .tabs { overflow-x: auto; flex-wrap: nowrap; } }
</style>
