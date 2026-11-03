<script lang="ts">
	import type { Snippet } from 'svelte';
	// One story = a lead item plus folded duplicates from other sources ("+N more sources").
	import { focusable } from '$lib/nav';
	let { href, title, source, summary, imageSrc, imageAlt = '', image, mediaAvailable = true, more = 0, moreSources = [], why }: {
		href: string; title: string; source?: string; summary?: string; imageSrc?: string; imageAlt?: string;
		image?: Snippet; mediaAvailable?: boolean; more?: number; moreSources?: string[]; why?: Snippet;
	} = $props();
</script>

<article class="story-card" class:has-media={!!((image || imageSrc) && mediaAvailable)}>
	{#if (image || imageSrc) && mediaAvailable}
		<div class="media">{#if image}{@render image()}{:else}<img src={imageSrc} alt={imageAlt} loading="lazy" />{/if}</div>
	{/if}
	<div class="body">
		<h3 class="title"><a {href} use:focusable>{title}</a></h3>
		{#if summary}<p class="summary">{summary}</p>{/if}
		<div class="meta">
			{#if source}<span class="source">{source}</span>{/if}
			{#if more > 0}<span class="more" title={moreSources.length ? moreSources.join(', ') : undefined}>+{more} {more === 1 ? 'source' : 'sources'}</span>{/if}
			{#if why}<span class="why">{@render why()}</span>{/if}
		</div>
	</div>
</article>

<style>
	.story-card { position: relative; display: grid; gap: var(--sp-4); padding-block: var(--sp-4); border-top: 1px solid var(--rule); color: var(--ink); }
	.story-card.has-media { grid-template-columns: minmax(0, 1fr) 7.5rem; }
	.media { order: 2; aspect-ratio: 4 / 3; overflow: hidden; border-radius: var(--r-sm); background: var(--bg-2); }
	.media :global(img) { width: 100%; height: 100%; object-fit: cover; }
	.body { display: grid; gap: var(--sp-2); align-content: start; min-width: 0; }
	.title { font-family: var(--font-display); font-weight: 560; font-size: var(--fs-lg); line-height: var(--lh-snug); }
	.title a { border-radius: var(--r-sm); }
	.title a::after { content: ''; position: absolute; inset: 0; z-index: 0; }
	.title a:hover { text-decoration: underline; text-decoration-color: var(--rule-strong); text-underline-offset: .18em; }
	.summary { color: var(--ink-soft); display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
	.meta { display: flex; flex-wrap: wrap; align-items: center; gap: var(--sp-1) var(--sp-3); font-size: var(--fs-xs); color: var(--faint); }
	.source { font-weight: 600; color: var(--ink-soft); }
	.more { padding: .05rem .5rem; border: 1px solid var(--rule); border-radius: var(--r-pill); }
	.why { position: relative; z-index: 1; }
	@media (max-width: 520px) { .story-card.has-media { grid-template-columns: minmax(0, 1fr) 5.5rem; } }
</style>
