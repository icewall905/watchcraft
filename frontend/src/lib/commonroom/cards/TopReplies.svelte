<script lang="ts">
	import type { TopComment } from '$lib/hub/commonroom';
	import { compact } from '../util';
	// Best replies right on the card: the conversation is half the fun.
	let { top, label = '', big = false, max = 1, onOpen }: { top: TopComment[]; label?: string; big?: boolean; max?: number; onOpen: () => void } = $props();
</script>
{#if top.length}
	<button type="button" class="replies" class:big onclick={onOpen} aria-label="Open the discussion">
		{#if label}<span class="label">{label}</span>{/if}
		{#each top.slice(0, max) as c, i (i)}
			<span class="bubble">
				<span class="body">{c.body}</span>
				<span class="who">{`— ${c.author || 'someone'}${c.score ? ` · ▲ ${compact(c.score)}` : ''}`}</span>
			</span>
		{/each}
	</button>
{/if}
<style>
	.replies{display:grid;gap:.45rem;width:100%;padding:0;border:0;background:none;color:inherit;font:inherit;text-align:left;cursor:pointer}
	.label{font-size:.7rem;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);font-weight:700}
	.bubble{position:relative;display:grid;gap:.25rem;padding:.55rem .75rem;border-radius:var(--r-sm) var(--r-sm) var(--r-sm) .2rem;background:var(--surface-hi);border-left:3px solid var(--ai)}
	.body{font-size:.86rem;line-height:1.4;overflow-wrap:anywhere;display:-webkit-box;-webkit-line-clamp:4;line-clamp:4;-webkit-box-orient:vertical;overflow:hidden}
	.big .body{font-size:1rem;-webkit-line-clamp:6;line-clamp:6}
	.who{font-size:.74rem;color:var(--muted)}
	.replies:hover .bubble{border-left-color:var(--accent)}
</style>
