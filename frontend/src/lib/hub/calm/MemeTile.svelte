<script lang="ts">
	import type { Snippet } from 'svelte';
	// Calm meme tile: thumbnail (image or video still), caption. Sensitive media stays blurred at 7px.
	import { focusable } from '$lib/nav';
	let { caption, meta, href, onopen, label, imageSrc, imageAlt = '', media, mediaAvailable = true, why, sensitive = false, video = false }: {
		caption: string; meta?: string; href?: string; onopen?: () => void;
		label?: string;
		imageSrc?: string; imageAlt?: string; media?: Snippet; mediaAvailable?: boolean; why?: Snippet; sensitive?: boolean; video?: boolean;
	} = $props();
</script>

{#snippet inner()}
	<span class="thumb" class:sensitive>
		{#if media && mediaAvailable}{@render media()}{:else if imageSrc}<img src={imageSrc} alt={imageAlt} loading="lazy" />{:else}<span class="text">{caption}</span>{/if}
		{#if video}<span class="badge" aria-label="Video">▶</span>{/if}
	</span>
	<span class="caption" class:sensitive>{caption}</span>
	{#if meta || why}<span class="meta">{#if meta}{meta}{/if}{#if why}<span class="why">{@render why()}</span>{/if}</span>{/if}
{/snippet}

{#if href}
	<a class="meme-tile" {href} use:focusable>{@render inner()}</a>
{:else}
	<button class="meme-tile" type="button" aria-label={label} use:focusable={{ onSelect: () => onopen?.() }}>{@render inner()}</button>
{/if}

<style>
	.meme-tile { display: grid; gap: var(--sp-2); align-content: start; width: 100%; padding: 0; background: none; border: 0; border-radius: var(--r-md); text-align: left; color: var(--ink); cursor: pointer; }
	.thumb { position: relative; display: grid; place-items: center; aspect-ratio: 1; overflow: hidden; border-radius: var(--r-md); background: var(--bg-2); }
	.thumb :global(img), .thumb :global(video) { width: 100%; height: 100%; object-fit: cover; transition: transform var(--dur-calm-slow) var(--ease-calm); }
	.meme-tile:hover .thumb :global(img) { transform: scale(1.02); }
	.text { padding: var(--sp-4); font-family: var(--font-display); font-size: var(--fs-md); text-align: center; }
	.thumb.sensitive :global(img), .thumb.sensitive :global(video), .thumb.sensitive .text, .caption.sensitive { filter: blur(7px); }
	.badge { position: absolute; right: var(--sp-2); bottom: var(--sp-2); padding: .1rem .45rem; border-radius: var(--r-pill); background: rgba(0, 0, 0, .55); color: #f5f5f5; font-size: var(--fs-xs); }
	.caption { font-size: var(--fs-sm); line-height: var(--lh-snug); display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
	.meta { font-size: var(--fs-xs); color: var(--faint); }
	.why { position: relative; z-index: 1; margin-left: var(--sp-2); }
</style>
