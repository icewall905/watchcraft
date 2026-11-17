<script lang="ts">
	import { page } from '$app/state';
	import { focusable } from '$lib/nav';
	import Card from '$lib/jellyfin/Card.svelte';
	import { jellyfin, type Item } from '$lib/jellyfin/api';

	const SORTS = [['SortName', 'A–Z', false], ['DateCreated', 'Recently added', true], ['PremiereDate', 'Release date', true], ['CommunityRating', 'Rating', true], ['Random', 'Shuffle', false]] as const;
	const TYPES: Record<string, string> = { movies: 'Movie', tvshows: 'Series', boxsets: 'BoxSet', musicvideos: 'MusicVideo', homevideos: 'Video' };
	const PAGE = 60;

	const id = $derived(page.params.id ?? '');
	let view = $state<Item | null>(null);
	let items = $state<Item[]>([]);
	let total = $state(0);
	let sort = $state(0);
	let unplayed = $state(false);
	let favorite = $state(false);
	let loading = $state(false);
	let seq = 0;

	async function fetchPage(reset: boolean) {
		if (!view) return;
		const mine = reset ? ++seq : seq;
		loading = true;
		const [key, , desc] = SORTS[sort];
		try {
			const types = view.collection_type ? TYPES[view.collection_type] : undefined;
			const r = await jellyfin.items({ parent_id: view.id, types, sort: key, descending: desc, unplayed, favorite,
				start: reset ? 0 : items.length, limit: PAGE });
			if (mine !== seq) return;
			items = reset ? r.items : [...items, ...r.items];
			total = r.total;
		} finally {
			if (mine === seq) loading = false;
		}
	}

	$effect(() => {
		const v = id;
		view = null; items = []; total = 0;
		jellyfin.item(v).then((it) => { if (v === id) { view = it; void fetchPage(true); } }).catch(() => {});
	});

	function setSort(i: number) { sort = i; void fetchPage(true); }
	function toggle(k: 'unplayed' | 'favorite') { if (k === 'unplayed') unplayed = !unplayed; else favorite = !favorite; void fetchPage(true); }
</script>

<svelte:head><title>{view?.name ?? 'Library'} · The Marquee</title></svelte:head>

<div class="page lib">
	<header>
		<a class="btn" href="/jellyfin" use:focusable={{ group: 'lib-top' }}>← The Marquee</a>
		<h1>{view?.name ?? '…'}</h1>
		{#if total}<span class="muted small">{total} titles</span>{/if}
	</header>
	<div class="filters" role="toolbar" aria-label="Sort and filter">
		{#each SORTS as [, label], i}
			<button class="btn mini" class:primary={sort === i} aria-pressed={sort === i} use:focusable={{ group: 'lib-f', onSelect: () => setSort(i) }}>{label}</button>
		{/each}
		<button class="btn mini" class:primary={unplayed} aria-pressed={unplayed} use:focusable={{ group: 'lib-f', onSelect: () => toggle('unplayed') }}>Unwatched</button>
		<button class="btn mini" class:primary={favorite} aria-pressed={favorite} use:focusable={{ group: 'lib-f', onSelect: () => toggle('favorite') }}>Favourites</button>
	</div>
	<div class="grid">
		{#each items as it (it.id)}<Card item={it} group="lib-grid" />{/each}
	</div>
	{#if loading}<p class="muted pad">Loading…</p>
	{:else if !items.length && view}<p class="muted pad">Nothing here with these filters.</p>
	{:else if items.length < total}
		<div class="pad"><button class="btn" use:focusable={{ group: 'lib-more', onSelect: () => fetchPage(false) }}>Show more ({total - items.length} left)</button></div>
	{/if}
</div>

<style>
	.lib { padding-bottom: 3rem; }
	header { display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; padding: 1rem var(--gutter) 0; }
	h1 { margin: 0; font-family: var(--font-display); }
	.filters { display: flex; flex-wrap: wrap; gap: 0.5rem; padding: 1rem var(--gutter); }
	.grid {
		display: grid; gap: 1.2rem 1rem; padding: 0 var(--gutter);
		grid-template-columns: repeat(auto-fill, minmax(clamp(8.5rem, 13vw, 12rem), 1fr));
	}
	.grid :global(.card) { width: auto; }
	.pad { padding: 1rem var(--gutter); }
</style>
