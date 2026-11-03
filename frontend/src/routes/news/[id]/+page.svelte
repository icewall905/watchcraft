<script lang="ts">
	import { page } from '$app/state';
	import { session } from '$lib/stores/session.svelte';
	import { focusable } from '$lib/nav';
	import { hub } from '$lib/hub/api';
	import { errMsg } from '$lib/hub/util';
	import type { Item } from '$lib/hub/types';
	import { EmptyCalm } from '$lib/hub/calm';
	import ArchivedSource from '$lib/hub/ArchivedSource.svelte';
	let title = $state('');
	let items = $state<Item[]>([]);
	let loading = $state(true);
	let error = $state('');
	$effect(() => {
		const id = Number(page.params.id);
		void session.version;
		let disposed = false;
		title = ''; items = []; error = ''; loading = true;
		void hub.story(id).then(story => { if (!disposed) { title = story.title; items = story.items; } })
			.catch(e => { if (!disposed) error = errMsg(e); })
			.finally(() => { if (!disposed) loading = false; });
		return () => { disposed = true; };
	});
</script>

<svelte:head><title>{title || 'Story'} · WatchCraft</title></svelte:head>
<div class="page reading-page">
	<a class="back" href="/news" use:focusable>← News</a>
	{#if loading}<div class="skeleton" aria-busy="true"></div>
	{:else if error}<EmptyCalm title="This story isn't available" detail={error} />
	{:else}
		<header><p class="kicker">A story from your sources</p><h1>{title}</h1><p class="meta">{items.length} source{items.length === 1 ? '' : 's'}</p></header>
		<div class="sources">
			{#each items as item (item.id)}
				<ArchivedSource source={item} />
			{/each}
		</div>
	{/if}
</div>

<style>
	.page { display: grid; gap: var(--sp-5); max-width: 58rem; margin-inline: auto; }
	.back { color: var(--ink-soft); }
	header { display: grid; gap: var(--sp-2); }
	h1 { max-width: 20ch; font: 560 var(--fs-3xl)/var(--lh-tight) var(--font-display); }
	.kicker { color: var(--accent); font-size: var(--fs-xs); font-weight: 650; letter-spacing: .1em; text-transform: uppercase; }
	.meta { color: var(--faint); font-size: var(--fs-sm); }
	.sources { display: grid; }
</style>
