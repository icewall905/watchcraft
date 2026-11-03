<script lang="ts">
	import { hub } from './api';
	import type { Item, ItemDetail } from './types';
	import { safeUrl, errMsg } from './util';
	import { focusable } from '$lib/nav';
	import LocalImage from './LocalImage.svelte';
	let { source }: { source: Item } = $props();
	let detail = $state<ItemDetail | null>(null);
	let error = $state('');
	$effect(() => {
		const id = source.id;
		let disposed = false;
		let timer: ReturnType<typeof setTimeout>;
		detail = null; error = '';
		async function load() {
			try {
				const result = await hub.item(id);
				if (disposed) return;
				detail = result;
				if (result.capture && ['queued', 'running'].includes(result.capture.status)) timer = setTimeout(load, 10000);
			} catch (e) { if (!disposed) error = errMsg(e); }
		}
		void load();
		return () => { disposed = true; clearTimeout(timer); };
	});
	const summary = $derived(typeof detail?.enrichment?.summary === 'string' ? detail.enrichment.summary : '');
</script>

<article>
	<h2><a href={`/item/${source.id}`} use:focusable>{source.title}</a></h2>
	<p class="muted small">{source.publisher || source.provider}</p>
	{#if summary}<section><h3>AI summary <span class="tag ai">AI-generated</span></h3><p class="prose">{summary}</p></section>{/if}
	{#if detail?.local_assets?.includes('image')}<LocalImage id={source.id} alt={source.title} loading="eager" expandable />{/if}
	<section><h3>Archived text</h3>
		{#if detail?.body}<p class="prose captured-text">{detail.body}</p>
		{:else if error}<p role="alert">{error}</p>
		{:else if !detail}<p class="muted">Loading local archive…</p>
		{:else}<p class="muted">Readable text is not available locally yet.</p>{/if}
	</section>
	{#if detail?.capture}<p class="small muted">Local capture: {detail.capture.status} · Summary: {detail.capture.summary_status}{detail.capture_truncated ? ' · Text is partial' : ''}{detail.capture.error ? ` · ${detail.capture.error}` : ''}</p>{/if}
	<div class="hstack">
		<a class="btn" href={`/item/${source.id}`} use:focusable>Archive details</a>
		{#if safeUrl(source.url)}<a class="btn" href={safeUrl(source.url)} target="_blank" rel="noopener noreferrer" use:focusable>Open original ↗</a>{/if}
	</div>
</article>

<style>
	article { display: grid; gap: var(--sp-4); border-top: 1px solid var(--rule); padding-block: var(--sp-5); min-width: 0; }
	h2 { font-family: var(--font-display); font-size: var(--fs-xl); }
	section { display: grid; gap: var(--sp-2); }
	.captured-text { white-space: pre-wrap; overflow-wrap: anywhere; }
	article :global(img) { display: block; max-width: 100%; height: auto; }
</style>
