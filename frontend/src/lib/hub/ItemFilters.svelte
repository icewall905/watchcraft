<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { focusable } from '$lib/nav';
	import { hub } from './api';
	import { FILTER_KEYS } from './util';
	import type { Curator, Registry } from './types';

	let { hide = [] }: { hide?: string[] } = $props();

	let reg = $state<Registry | null>(null);
	let curators = $state<Curator[]>([]);
	let open = $state(false);

	onMount(() => {
		hub.registry().then((r) => (reg = r)).catch(() => {});
		hub.curators().then((c) => (curators = c)).catch(() => {});
	});

	const p = $derived(page.url.searchParams);
	const active = $derived(FILTER_KEYS.filter((k) => !hide.includes(k) && k !== 'sort' && p.get(k)).length);

	function set(key: string, value: string | boolean) {
		const u = new URL(page.url);
		if (value === '' || value === false) u.searchParams.delete(key);
		else u.searchParams.set(key, value === true ? '1' : String(value));
		goto(u, { replaceState: true, keepFocus: true, noScroll: true });
	}
	function clear() {
		const u = new URL(page.url);
		for (const k of FILTER_KEYS) u.searchParams.delete(k);
		goto(u, { replaceState: true, keepFocus: true, noScroll: true });
	}
	const show = (k: string) => !hide.includes(k);
</script>

<div class="filters" class:open>
	<button class="btn toggle" aria-expanded={open} aria-controls="wc-filter-panel" use:focusable onclick={() => (open = !open)}>
		Filters{#if active} <span class="n">{active}</span>{/if}
	</button>
	<div class="panel" id="wc-filter-panel">
		{#if show('type')}
			<label><span>Type</span>
				<select class="input" value={p.get('type') ?? ''} onchange={(e) => set('type', e.currentTarget.value)}>
					<option value="">All types</option>
					{#each reg?.types ?? [] as t}<option value={t.key}>{t.label}</option>{/each}
				</select></label>
		{/if}
		{#if show('provider')}
			<label><span>Provider</span>
				<select class="input" value={p.get('provider') ?? ''} onchange={(e) => set('provider', e.currentTarget.value)}>
					<option value="">Any provider</option>
					{#each reg?.providers ?? [] as pr}<option value={pr.key}>{pr.label}</option>{/each}
				</select></label>
		{/if}
		{#if show('curator')}
			<label><span>Curator</span>
				<select class="input" value={p.get('curator') ?? ''} onchange={(e) => set('curator', e.currentTarget.value)}>
					<option value="">Any curator</option>
					{#each curators as c}<option value={c.id}>{c.name}</option>{/each}
				</select></label>
		{/if}
		{#if show('since')}
			<label><span>Found</span>
				<select class="input" value={p.get('since') ?? ''} onchange={(e) => set('since', e.currentTarget.value)}>
					<option value="">Any time</option>
					<option value="1">Last 24 hours</option>
					<option value="7">Last 7 days</option>
					<option value="30">Last 30 days</option>
				</select></label>
		{/if}
		{#if show('min_score')}
			<label><span>Min relevance</span>
				<select class="input" value={p.get('min_score') ?? ''} onchange={(e) => set('min_score', e.currentTarget.value)}>
					<option value="">Any</option>
					<option value="0.3">30+</option>
					<option value="0.5">50+</option>
					<option value="0.7">70+</option>
					<option value="0.9">90+</option>
				</select></label>
		{/if}
		{#if show('sort')}
			<label><span>Sort</span>
				<select class="input" value={p.get('sort') ?? 'newest'} onchange={(e) => set('sort', e.currentTarget.value === 'newest' ? '' : e.currentTarget.value)}>
					<option value="newest">Newest found</option>
					<option value="published">Newest published</option>
					<option value="relevance">Most relevant</option>
				</select></label>
		{/if}
		{#if show('unread')}
			<label class="check"><input type="checkbox" checked={p.get('unread') === '1'} onchange={(e) => set('unread', e.currentTarget.checked)} /> Unread only</label>
		{/if}
		{#if show('saved')}
			<label class="check"><input type="checkbox" checked={p.get('saved') === '1'} onchange={(e) => set('saved', e.currentTarget.checked)} /> Saved only</label>
		{/if}
		{#if active}<button class="btn ghost" use:focusable onclick={clear}>Clear filters</button>{/if}
	</div>
</div>

<style>
	.filters { display: grid; gap: .6rem; }
	.toggle { display: none; justify-self: start; min-height: 2.75rem; }
	.n { background: var(--accent); color: var(--accent-ink); border-radius: var(--r-pill); padding: 0 .5rem; font-size: .75rem; }
	.panel { display: flex; flex-wrap: wrap; gap: .6rem .9rem; align-items: end; }
	label { display: grid; gap: .2rem; font-size: .72rem; color: var(--muted); font-weight: 600; min-width: 9rem; }
	label .input { min-height: 2.75rem; padding: .4rem .7rem; font-size: .9rem; }
	label.check { display: flex; align-items: center; gap: .5rem; min-height: 2.75rem; font-size: .88rem; min-width: 0; }
	label.check input { width: 1.2rem; height: 1.2rem; accent-color: var(--accent); }
	select.input { background: rgba(10, 8, 25, .85); }
	@media (max-width: 760px) {
		.toggle { display: inline-flex; }
		.panel { display: none; }
		.open .panel { display: grid; grid-template-columns: 1fr; }
		label { min-width: 0; }
	}
</style>
