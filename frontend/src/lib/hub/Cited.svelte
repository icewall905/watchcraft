<script lang="ts">
	// Plain-text paragraphs; "[n]" markers become links to the nth cited item.
	import { focusable } from '$lib/nav';
	let { text, itemIds = [] }: { text: string; itemIds?: (number | null)[] } = $props();
	const paras = $derived(
		text.split(/\n{2,}/).map((p) => p.trim()).filter(Boolean).map((p) => p.split(/(\[\d+\])/).map((part) => {
			const m = /^\[(\d+)\]$/.exec(part);
			const id = m ? itemIds[Number(m[1]) - 1] : undefined;
			return { text: part, id };
		}))
	);
</script>

<div class="cited">
	{#each paras as parts}
		<p>{#each parts as part}{#if part.id}<a class="cite" href={`/item/${part.id}`} use:focusable aria-label={`Source ${part.text}`}>{part.text}</a>{:else}{part.text}{/if}{/each}</p>
	{/each}
</div>

<style>
	.cited { display: grid; gap: var(--sp-5); line-height: var(--lh-body); max-width: var(--measure); font-size: var(--fs-md); white-space: pre-line; }
	.cited p { margin: 0; color: var(--text); }
	.cite { color: var(--accent); font-weight: 700; font-size: .8em; vertical-align: super; padding: .15em .3em; border-radius: var(--r-sm); text-decoration: underline; text-underline-offset: .2em; }
</style>
