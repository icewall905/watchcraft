<script lang="ts">
	import type { Snippet } from 'svelte';
	// Calm end state instead of an endless "load more".
	let { title = "That's everything for today", detail, children }: { title?: string; detail?: string; children?: Snippet } = $props();
</script>

<div class="empty-calm" role="status">
	<span class="mark" aria-hidden="true"></span>
	<p class="title">{title}</p>
	{#if detail}<p class="detail">{detail}</p>{/if}
	{#if children}<div class="extra">{@render children()}</div>{/if}
</div>

<style>
	.empty-calm { display: grid; justify-items: center; gap: var(--sp-2); text-align: center; padding: var(--sp-8) var(--sp-4); color: var(--ink-soft); }
	.mark { width: 2.5rem; height: 1px; background: var(--rule-strong); margin-bottom: var(--sp-3); }
	.title { font-family: var(--font-display); font-size: var(--fs-lg); font-style: italic; color: var(--ink); }
	.detail { font-size: var(--fs-sm); max-width: 44ch; }
	.extra { margin-top: var(--sp-3); }
</style>
