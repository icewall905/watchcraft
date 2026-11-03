<script lang="ts">
	import { onDestroy } from 'svelte';
	import { hub } from '$lib/hub/api';
	import type { Item } from '$lib/hub/types';
	import { session } from '$lib/stores/session.svelte';
	import { focusable } from '$lib/nav';
	import LocalImage from '$lib/hub/LocalImage.svelte';
	import SectionHeader from '$lib/hub/calm/SectionHeader.svelte';
	import WhyPopover from '$lib/hub/calm/WhyPopover.svelte';
	import EmptyCalm from '$lib/hub/calm/EmptyCalm.svelte';

	const pageSize = 30;
	let items = $state<Item[]>([]);
	let total = $state(0);
	let offset = $state(0);
	let loading = $state(true);
	let moreBusy = $state(false);
	let error = $state('');
	let notice = $state('');
	let retry = $state(0);
	let busy = $state<number | null>(null);
	let controller: AbortController | null = null;
	let generation = 0;

	const label = (type: string) => ({ meme: 'Memes', news: 'News', article: 'Articles', web: 'Web pages', project: 'Projects', document: 'Documents', advisory: 'Advisories' }[type] ?? (type ? `${type[0].toUpperCase()}${type.slice(1)}` : 'Other'));
	const groups = $derived.by(() => {
		const map = new Map<string, Item[]>();
		for (const item of items) { const group = map.get(item.type) ?? []; group.push(item); map.set(item.type, group); }
		return [...map.entries()].sort((a, b) => label(a[0]).localeCompare(label(b[0])));
	});

	async function loadFirst() {
		controller?.abort();
		controller = new AbortController();
		const signal = controller.signal;
		const token = ++generation;
		const profileVersion = session.version;
		loading = true; error = ''; items = []; offset = 0; total = 0; busy = null; moreBusy = false; notice = '';
		try {
			const result = await hub.setAside({ offset: 0, limit: pageSize });
			if (signal.aborted || token !== generation || profileVersion !== session.version) return;
			items = result.items; total = result.total; offset = result.items.length;
		} catch (e) {
			if (!signal.aborted && token === generation && profileVersion === session.version) error = e instanceof Error ? e.message : 'The set-aside list could not be be loaded.';
		} finally { if (!signal.aborted && token === generation && profileVersion === session.version) loading = false; }
	}
	$effect(() => { session.version; retry; void loadFirst(); });
	onDestroy(() => controller?.abort());

	async function loadMore() {
		if (busy !== null || moreBusy || offset >= total) return;
		moreBusy = true; error = '';
		const token = generation; const profileVersion = session.version;
		try {
			const result = await hub.setAside({ offset, limit: pageSize });
			if (token !== generation || profileVersion !== session.version) return;
			items = [...items, ...result.items]; total = result.total; offset += result.items.length;
		} catch (e) { if (profileVersion === session.version) error = e instanceof Error ? e.message : 'More items could not be loaded.'; }
		finally { if (profileVersion === session.version) moreBusy = false; }
	}
	async function promote(item: Item) {
		if (busy !== null || moreBusy) return;
		busy = item.id; notice = ''; error = '';
		const profileVersion = session.version;
		try {
			await hub.overrule(item.id, 'promote');
			if (profileVersion !== session.version) return;
			items = items.filter((entry) => entry.id !== item.id);
			offset = Math.max(0, offset - 1); total = Math.max(0, total - 1); notice = `“${item.title}” was added to your edition.`;
		} catch (e) { if (profileVersion === session.version) error = e instanceof Error ? `Could not promote this item: ${e.message}` : 'Could not promote this item.'; }
		finally { if (profileVersion === session.version) busy = null; }
	}
</script>

<svelte:head><title>Set aside · WatchCraft</title></svelte:head>

<div class="set-aside page" data-area="hub">
	<a class="back" href="/today" use:focusable>← Today</a>
	<header><p class="date">A second look</p><h1>Set aside</h1><p class="intro">Items the curator held back. If one belongs in your day, bring it in and the curator will learn from that choice.</p></header>
	{#if notice}<p class="notice" role="status">{notice}</p>{/if}
	{#if error}<div class="error" role="alert"><p>{error}</p>{#if loading || !items.length}<button class="btn" use:focusable onclick={() => retry++}>Try again</button>{/if}</div>{/if}
	{#if loading}<p role="status" class="muted">Gathering set-aside items…</p>
	{:else if !items.length && !error && total === 0}<EmptyCalm title="Nothing set aside" detail="When the curator holds something back, you can review it here." />
	{:else}
		{#each groups as [type, groupItems] (type)}
			<section class="group">
				<SectionHeader title={label(type)} />
				<div class="items" data-nav-row>
					{#each groupItems as item (item.id)}
						<article class="item">
							{#if item.local_image}<a class="image" href={`/item/${item.id}`} aria-label={`Open ${item.title}`} use:focusable><LocalImage id={item.id} alt={item.title} /></a>{/if}
							<div class="body"><h2><a href={`/item/${item.id}`} use:focusable>{item.title}</a></h2>
								{#if item.summary}<p class="summary">{item.summary}</p>{/if}
								<div class="meta"><span>{item.publisher || item.author || item.type}</span><WhyPopover reasons={item.verdict?.reasons ?? []} /></div>
								<button class="btn promote" disabled={busy !== null || moreBusy} use:focusable onclick={() => promote(item)}>{busy === item.id ? 'Adding…' : 'Bring into Today'}</button>
							</div>
						</article>
					{/each}
				</div>
			</section>
		{/each}
		{#if offset < total}<div class="more"><button class="btn" disabled={moreBusy || busy !== null} use:focusable onclick={loadMore}>{moreBusy ? 'Loading…' : `Show more (${total - offset})`}</button></div>{/if}
	{/if}
</div>

<style>
	.set-aside { max-width: 72rem; margin-inline: auto; padding-top: 2.5rem; padding-bottom: 5rem; color: var(--ink); }
	.back { display: inline-block; margin-bottom: 1.5rem; color: var(--ink-soft); font-size: var(--fs-sm); }
	header { padding-bottom: 2rem; border-bottom: 1px solid var(--rule); margin-bottom: 2rem; }
	.date { color: var(--faint); font-size: var(--fs-sm); margin-bottom: .5rem; }
	h1 { font: 520 clamp(2.3rem, 5vw, 3.7rem) / 1.05 var(--font-display); margin-bottom: .7rem; }
	.intro { max-width: 48ch; color: var(--ink-soft); line-height: 1.6; }
	.group { margin-top: 2.5rem; }
	.items { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1.5rem 2.5rem; }
	.item:not(:has(.image)) { grid-template-columns: minmax(0, 1fr); }
	.item { display: grid; grid-template-columns: minmax(5rem, 8rem) minmax(0, 1fr); gap: 1rem; align-items: start; border-bottom: 1px solid var(--rule); padding-bottom: 1.4rem; }
	.image { aspect-ratio: 4 / 3; overflow: hidden; border-radius: var(--r-sm); background: var(--bg-2); }
	.image :global(img) { width: 100%; height: 100%; object-fit: cover; }
	.body { min-width: 0; display: grid; gap: .6rem; }
	h2 { font: 550 var(--fs-lg) / 1.2 var(--font-display); }
	h2 a { color: var(--ink); }
	.summary { color: var(--ink-soft); font-size: var(--fs-sm); line-height: 1.5; }
	.meta { display: flex; align-items: center; gap: .4rem; color: var(--faint); font-size: var(--fs-xs); }
	.promote { justify-self: start; }
	.more { display: flex; justify-content: center; padding: 2rem; }
	.notice { margin-bottom: 1rem; color: var(--ink-soft); }
	.error { padding: 1rem; border-left: 2px solid var(--warn); background: var(--bg-2); }
	@media (max-width: 700px) { .items { grid-template-columns: 1fr; gap: 1rem; } }
	@media (max-width: 430px) { .item { grid-template-columns: minmax(4.5rem, 6rem) minmax(0, 1fr); gap: .75rem; } }
</style>
