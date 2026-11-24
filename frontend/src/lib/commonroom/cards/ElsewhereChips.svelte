<script lang="ts">
	import type { Elsewhere } from '$lib/hub/commonroom';
	import { compact, platformOf } from '../util';
	// The same link discussed in other places: hop to the livelier thread.
	let { list }: { list: Elsewhere[] } = $props();
</script>
{#if list.length}
	<div class="also">
		<span class="muted">Also on</span>
		{#each list as e (e.url)}
			{@const p = platformOf(e.platform)}
			<a class="chip" href={e.url} target="_blank" rel="noopener noreferrer" title={e.community || p.label}>{`${p.icon} ${e.community || p.label}${e.comments ? ` · 💬 ${compact(e.comments)}` : ''}`}</a>
		{/each}
	</div>
{/if}
<style>
	.also{display:flex;flex-wrap:wrap;gap:.35rem;align-items:center;font-size:.76rem}
	.muted{color:var(--muted)}
	.chip{padding:.15rem .55rem;border-radius:var(--r-pill);border:1px solid var(--line);color:var(--text);text-decoration:none;white-space:nowrap;max-width:100%;overflow:hidden;text-overflow:ellipsis}
	.chip:hover{border-color:var(--ai)}
</style>
