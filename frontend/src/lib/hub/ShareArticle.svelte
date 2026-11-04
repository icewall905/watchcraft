<script lang="ts">
	import { hub } from './api';
	import { errMsg } from './util';
	import { session } from '$lib/stores/session.svelte';
	import { focusable } from '$lib/nav';

	let { itemId }: { itemId: number } = $props();
	const scope = $derived(`${session.profileId}:${session.version}:${itemId}`);
	let loadedScope = $state('');
	let path = $state<string | null>(null);
	let busy = $state(true);
	let error = $state('');
	let copied = $state(false);
	let generation = 0;
	const current = $derived(loadedScope === scope);
	const url = $derived(current && path && typeof window !== 'undefined' ? new URL(path, window.location.origin).href : '');

	$effect(() => {
		const key = scope;
		const id = itemId;
		const my = ++generation;
		let disposed = false;
		loadedScope = key; path = null; busy = true; error = ''; copied = false;
		hub.articleShare(id).then((r) => {
			if (!disposed && my === generation && key === scope) path = r.path;
		}).catch((e) => {
			if (!disposed && my === generation && key === scope) error = errMsg(e);
		}).finally(() => {
			if (!disposed && my === generation && key === scope) busy = false;
		});
		return () => { disposed = true; generation++; };
	});

	async function change(revoke = false) {
		if (busy || !current) return;
		const key = scope, my = generation, id = itemId;
		busy = true; error = ''; copied = false;
		try {
			const r = await (revoke ? hub.revokeArticleShare(id) : hub.shareArticle(id));
			if (my === generation && key === scope) path = r.path;
		} catch (e) { if (my === generation && key === scope) error = errMsg(e); }
		finally { if (my === generation && key === scope) busy = false; }
	}

	async function copy() {
		const key = scope, my = generation;
		try {
			await navigator.clipboard.writeText(url);
			if (my === generation && key === scope) { copied = true; error = ''; }
		} catch { if (my === generation && key === scope) error = 'Could not copy. Select and copy the public link below.'; }
	}
</script>

<div class="share" aria-busy={busy || !current}>
	{#if url}
		<label class="small muted">Public article link
			<input class="input" aria-label="Public article link" readonly value={url} onclick={(e) => e.currentTarget.select()} />
		</label>
		<div class="hstack">
			<button class="btn" disabled={busy} use:focusable onclick={copy}>{copied ? 'Copied' : 'Copy link'}</button>
			<a class="btn" href={url} target="_blank" rel="noopener noreferrer" use:focusable>Open public link ↗</a>
			<button class="btn ghost" disabled={busy} use:focusable onclick={() => change(true)}>Revoke link</button>
		</div>
	{:else}
		<button class="btn" disabled={busy || !current} use:focusable onclick={() => change()}>{busy && current ? 'Loading share link…' : 'Share article'}</button>
	{/if}
	<p class="small muted">Anyone with the link can view the archived article and AI summary. Your profile, notes and reading history stay private.</p>
	{#if current && error}<p class="small error" role="alert">{error}</p>{/if}
</div>

<style>
	.share { display: grid; gap: var(--sp-2); min-width: 0; }
	label { display: grid; gap: var(--sp-2); }
	input { width: 100%; min-width: 0; }
	.error { color: var(--bad); }
</style>
