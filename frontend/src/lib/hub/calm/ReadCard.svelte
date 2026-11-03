<script lang="ts">
	import type { Snippet } from 'svelte';
	// Editorial card: optional large image, kicker, serif title, short summary, source + read time, why slot.
	// The title link covers the card; the why slot sits above it so its button stays clickable.
	import { focusable } from '$lib/nav';
	let { href, title, kicker, summary, source, summaryLabel, readMinutes, imageSrc, imageAlt = '', image, mediaAvailable = true, why, external = false, size = 'normal', onopen }: {
		href: string; title: string; kicker?: string; summary?: string; source?: string; summaryLabel?: string; readMinutes?: number;
		imageSrc?: string; imageAlt?: string; image?: Snippet; mediaAvailable?: boolean; why?: Snippet; external?: boolean; size?: 'normal' | 'lead'; onopen?: () => void;
	} = $props();
</script>

<article class="read-card" class:lead={size === 'lead'} class:has-media={!!((image || imageSrc) && mediaAvailable)}>
	{#if (image || imageSrc) && mediaAvailable}
		<div class="media">{#if image}{@render image()}{:else}<img src={imageSrc} alt={imageAlt} loading="lazy" />{/if}</div>
	{/if}
	<div class="body">
		{#if kicker}<p class="kicker">{kicker}</p>{/if}
		<h3 class="title"><a {href} onclick={onopen} use:focusable target={external ? '_blank' : undefined} rel={external ? 'noreferrer' : undefined}>{title}</a></h3>
		{#if summary}<p class="summary">{#if summaryLabel}<span class="summary-label">{summaryLabel} · </span>{/if}{summary}</p>{/if}
		{#if source || readMinutes || why}
			<div class="meta">
				{#if source}<span>{source}</span>{/if}
				{#if readMinutes}<span>{readMinutes} min read</span>{/if}
				{#if why}<span class="why">{@render why()}</span>{/if}
			</div>
		{/if}
	</div>
</article>

<style>
	.read-card { position: relative; display: grid; gap: var(--sp-3); align-content: start; color: var(--ink); }
	.media { aspect-ratio: 16 / 9; overflow: hidden; border-radius: var(--r-md); background: var(--bg-2); }
	.media :global(img) { width: 100%; height: 100%; object-fit: cover; transition: transform var(--dur-calm-slow) var(--ease-calm); }
	.read-card:hover .media :global(img) { transform: scale(1.02); }
	.body { display: grid; gap: var(--sp-2); }
	.kicker { font: 650 var(--fs-xs) / 1.2 var(--font-body); letter-spacing: .1em; text-transform: uppercase; color: var(--accent); }
	.title { font-family: var(--font-display); font-weight: 560; font-size: var(--fs-lg); line-height: var(--lh-snug); letter-spacing: -0.01em; }
	.lead .title { font-size: var(--fs-2xl); line-height: var(--lh-tight); }
	.title a { border-radius: var(--r-sm); }
	.title a::after { content: ''; position: absolute; inset: 0; z-index: 0; }
	.title a:hover { text-decoration: underline; text-decoration-color: var(--rule-strong); text-underline-offset: .18em; }
	.summary { color: var(--ink-soft); line-height: 1.55; display: -webkit-box; -webkit-line-clamp: 3; line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
	.lead .summary { font-size: var(--fs-md); }
	.meta { display: flex; flex-wrap: wrap; align-items: center; gap: var(--sp-1) var(--sp-3); font-size: var(--fs-xs); color: var(--faint); }
	.meta > span:not(.why) + span:not(.why)::before { content: '·'; margin-right: var(--sp-3); }
	.why { position: relative; z-index: 1; }
	@media (min-width: 900px) { .lead.has-media { grid-template-columns: 3fr 2fr; gap: var(--sp-6); align-items: center; } }
</style>
