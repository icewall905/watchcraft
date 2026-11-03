<script lang="ts">
	// Live filter for item lists. Writes the shared `q` URL parameter as the user
	// types, so every page that already reads `q` narrows without a submit.
	import { untrack } from 'svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { focusable } from '$lib/nav';

	let { placeholder = 'Filter…', label = 'Filter', count = null, delay = 250, hint = '' }: {
		placeholder?: string; label?: string; count?: number | null; delay?: number; hint?: string;
	} = $props();

	let value = $state(page.url.searchParams.get('q') ?? '');
	let timer: ReturnType<typeof setTimeout> | undefined;

	// Follow the URL when something else changes it (a tag chip, Clear, a link).
	$effect(() => {
		const q = page.url.searchParams.get('q') ?? '';
		if (q !== untrack(() => value).trim()) value = q;
	});
	$effect(() => () => clearTimeout(timer));

	function apply(v: string) {
		const u = new URL(page.url);
		u.searchParams.delete('page');
		if (v.trim()) u.searchParams.set('q', v.trim()); else u.searchParams.delete('q');
		goto(u, { replaceState: true, keepFocus: true, noScroll: true });
	}
	function oninput() {
		clearTimeout(timer);
		timer = setTimeout(() => apply(value), delay);
	}
	function submit(e: SubmitEvent) {
		e.preventDefault();
		clearTimeout(timer);
		apply(value);
	}
	function clear() {
		clearTimeout(timer);
		value = '';
		apply('');
	}
</script>

<form class="live" role="search" onsubmit={submit}>
	<input class="input" type="search" bind:value {placeholder} aria-label={label} oninput={oninput} use:focusable />
	{#if value}<button type="button" class="btn ghost" use:focusable onclick={clear}>Clear</button>{/if}
	{#if count !== null}<span class="faint small count" aria-live="polite">{count} match{count === 1 ? '' : 'es'}</span>{/if}
</form>
{#if hint}<p class="faint small hint">{hint}</p>{/if}

<style>
	.live { display: flex; gap: .5rem; align-items: center; max-width: 42rem; }
	.live .input { flex: 1; min-width: 0; min-height: 2.75rem; }
	.count { white-space: nowrap; }
	.hint { margin: -.4rem 0 0; }
</style>
