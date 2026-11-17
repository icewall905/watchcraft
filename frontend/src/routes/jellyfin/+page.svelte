<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { focusable } from '$lib/nav';
	import Row from '$lib/components/Row.svelte';
	import { session } from '$lib/stores/session.svelte';
	import Card from '$lib/jellyfin/Card.svelte';
	import LinkForm from '$lib/jellyfin/LinkForm.svelte';
	import { jellyfin, type Home, type Item, type Status } from '$lib/jellyfin/api';

	let status = $state<Status | null>(null);
	let home = $state<Home | null>(null);
	let failed = $state('');
	let results = $state<Item[] | null>(null);
	let searching = $state(false);
	let text = $state(page.url.searchParams.get('q') ?? '');
	const q = $derived(page.url.searchParams.get('q') ?? '');

	async function load() {
		failed = '';
		try {
			status = await jellyfin.status();
			if (status.configured && status.allowed && status.linked) home = await jellyfin.home();
		} catch (e) {
			failed = (e as Error).message;
		}
	}

	function search(e: Event) {
		e.preventDefault();
		const u = new URL(page.url);
		if (text.trim()) u.searchParams.set('q', text.trim()); else u.searchParams.delete('q');
		goto(u, { keepFocus: true, noScroll: true });
	}

	$effect(() => {
		const query = q;
		if (!query || !status?.linked) { results = null; return; }
		searching = true;
		jellyfin.search(query).then((r) => { if (query === q) results = r.items; }).catch(() => {}).finally(() => { if (query === q) searching = false; });
	});

	onMount(load);
</script>

<svelte:head><title>The Cinema · The Hub</title></svelte:head>

<div class="page jf">
	<header class="top">
		<h1>The Cinema</h1>
		{#if status?.linked}
			<form class="search" onsubmit={search} role="search">
				<input type="search" placeholder="Search movies and shows" aria-label="Search Jellyfin" bind:value={text} use:focusable={{ group: 'jf-top' }} />
			</form>
			<span class="who small muted">{status.server_name || 'Jellyfin'} · {status.user_name}</span>
		{/if}
	</header>

	{#if failed}
		<p class="err">{failed}</p>
		<button class="btn" use:focusable={{ autofocus: true, onSelect: load }}>Try again</button>
	{:else if !status}
		<p class="muted">Loading…</p>
	{:else if !status.configured}
		<div class="empty">
			<p>Watch your Jellyfin movies and shows here: browse, resume and play with Jellyfin's own transcoding.</p>
			{#if session.grownUp}
				<a class="btn primary" href="/settings?tab=jellyfin" use:focusable={{ autofocus: true }}>Add your Jellyfin server</a>
			{:else}
				<p class="muted">Ask an administrator to add the Jellyfin server in Settings.</p>
			{/if}
		</div>
	{:else if !status.allowed}
		<p class="muted empty">Jellyfin is not available for this profile.</p>
	{:else if !status.linked}
		<div class="empty">
			<p>Sign in with your Jellyfin account to see your library on {status.server_name || 'the server'}.</p>
			<LinkForm onLinked={(s) => { status = s; void load(); }} />
		</div>
	{:else if q}
		<Row title={searching ? `Searching “${q}”…` : `Results for “${q}”`}>
			{#each results ?? [] as it (it.id)}<Card item={it} />{/each}
		</Row>
		{#if results && !results.length && !searching}<p class="muted pad">Nothing on Jellyfin matches “{q}”.</p>{/if}
	{:else if home}
		{#if home.resume.length}
			<Row title="Continue watching">{#each home.resume as it (it.id)}<Card item={it} wide />{/each}</Row>
		{/if}
		{#if home.next_up.length}
			<Row title="Next up">{#each home.next_up as it (it.id)}<Card item={it} wide />{/each}</Row>
		{/if}
		<Row title="Libraries">{#each home.views as v (v.id)}<Card item={v} wide />{/each}</Row>
		{#each home.latest as row (row.view.id)}
			<Row title={`New in ${row.view.name}`} href={`/jellyfin/library/${row.view.id}`}>
				{#each row.items as it (it.id)}<Card item={it} />{/each}
			</Row>
		{/each}
	{:else}
		<p class="muted">Loading your library…</p>
	{/if}
</div>

<style>
	.jf { padding-bottom: 3rem; }
	.top { display: flex; flex-wrap: wrap; gap: 1rem; align-items: center; padding: 1rem var(--gutter) 0; }
	.top h1 { margin: 0; font-family: var(--font-display); }
	.search { flex: 1 1 16rem; max-width: 28rem; }
	.search input { width: 100%; }
	.empty { display: grid; gap: 1rem; padding: 2rem var(--gutter); max-width: 40rem; }
	.err { color: var(--bad); padding: 0 var(--gutter); }
	.pad { padding: 0 var(--gutter); }
	.muted:only-child { padding: 0 var(--gutter); }
</style>
