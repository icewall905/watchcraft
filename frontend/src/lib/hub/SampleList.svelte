<script lang="ts">
	import { focusable } from '$lib/nav';
	import type { SourceTest } from './types';

	// A live extraction result: what the rules actually found, or why they found nothing.
	let { result, limit = 5, label = 'Test' }: { result: SourceTest; limit?: number; label?: string } = $props();
	const day = (t: number) => new Date(t * 1000).toLocaleDateString();
</script>

<div class="result" role="status">
	{#if result.ok}
		<b class="okc">{label} passed: {result.count} {result.count === 1 ? 'item' : 'items'} extracted.</b>
	{:else}
		<b class="bad">{label} failed: {result.error || 'nothing could be extracted'}</b>
	{/if}
	{#if result.sample.length}
		<ol class="samples">
			{#each result.sample.slice(0, limit) as it, i (i)}
				<li>
					<b>{it.title || '(untitled)'}</b>
					<span class="small faint">
						{#if it.url}<a href={it.url} target="_blank" rel="noopener noreferrer" use:focusable>{it.url}</a>{/if}
						{#if it.published_at} · {day(it.published_at)}{/if}
					</span>
					{#if it.excerpt}<span class="small muted">{it.excerpt}</span>{/if}
				</li>
			{/each}
		</ol>
	{/if}
</div>

<style>
	.result { display: grid; gap: .3rem; }
	.samples { margin: 0; padding-left: 1.2rem; display: grid; gap: .35rem; }
	.samples li { display: grid; gap: .05rem; overflow-wrap: anywhere; }
	.bad { color: var(--bad); } .okc { color: var(--ok); }
</style>
