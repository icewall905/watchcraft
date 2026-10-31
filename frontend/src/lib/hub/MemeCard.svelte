<script lang="ts">
	import { focusable } from '$lib/nav';
	import { ago } from '$lib/hub/api';
	import type { Item } from '$lib/hub/types';
	import LocalImage from '$lib/hub/LocalImage.svelte';
	import Icon from '$lib/components/Icon.svelte';

	let { item, onopen }: { item: Item; onopen: (item: Item) => void } = $props();

	const meme = $derived(item.meme);
</script>

<button class="meme-card" use:focusable onclick={() => onopen(item)} aria-label={`Open meme: ${item.title}`}>
	<div class="shot" class:sensitive={meme?.sensitive}>
		{#if item.local_image}
			<LocalImage id={item.id} alt={meme?.spoiler || item.title} />
		{:else}
			<span class="text-meme" class:sensitive={meme?.sensitive}>{item.title || item.summary}</span>
		{/if}
		{#if meme?.format_name}<span class="fmt">{meme.format_name}</span>{/if}
		{#if item.local_video}<span class="clip">▶ Video</span>{/if}
		{#if meme?.dup_of}<span class="dup" title="Exact duplicate of another meme">dup</span>{/if}
	</div>
	<div class="meta">
		<p class="title" class:sensitive={meme?.sensitive}>{meme?.spoiler || item.title || item.summary}</p>
		<p class="faint small">
			{meme?.community || item.publisher || item.provider}{#if meme?.language} · {meme.language}{/if} · {ago(item.first_seen)}
		</p>
		{#if meme?.sensitive}<p class="warn small">Marked sensitive{meme.spoiler ? ` — ${meme.spoiler}` : ''}</p>{/if}
		<div class="acts">
			<a class="icon-btn" href={item.url} target="_blank" rel="noreferrer" use:focusable aria-label="Open original post"><Icon name="external" /></a>
		</div>
	</div>
</button>

<style>
	.meme-card { display: grid; gap: .5rem; text-align: start; background: var(--surface); border: 1px solid var(--border); border-radius: var(--r-card); padding: 0; overflow: hidden; color: inherit; cursor: pointer; }
	.meme-card:hover { border-color: var(--accent); }
	.shot { position: relative; display: grid; place-items: center; min-height: 12rem; max-height: 22rem; background: rgba(255, 255, 255, .04); overflow: hidden; }
	.shot :global(img) { width: 100%; height: 100%; max-height: 22rem; object-fit: cover; display: block; }
	.text-meme { padding: 1.4rem 1rem; font-size: 1.05rem; font-weight: 600; display: -webkit-box; -webkit-line-clamp: 6; -webkit-box-orient: vertical; overflow: hidden; }
	.shot.sensitive :global(img), .text-meme.sensitive { filter: blur(7px); }
	.clip { position: absolute; bottom: .5rem; left: .5rem; padding: .2rem .6rem; border-radius: var(--r-pill); background: rgba(0, 0, 0, .7); }
	.fmt, .dup { position: absolute; top: .5rem; font-size: .72rem; font-weight: 700; border-radius: var(--r-pill); padding: .15rem .55rem; background: rgba(0, 0, 0, .55); color: var(--fg); }
	.fmt { left: .5rem; }
	.dup { right: .5rem; color: var(--warning); }
	.meta { display: grid; gap: .25rem; padding: 0 .8rem .8rem; }
	.title { margin: 0; font-weight: 600; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
	.title.sensitive { filter: blur(7px); }
	.warn { margin: 0; color: var(--warning); }
	.acts { display: flex; gap: .4rem; justify-content: end; }
	.icon-btn { display: inline-grid; place-items: center; width: 2.4rem; height: 2.4rem; border-radius: 50%; color: var(--muted); }
	.icon-btn:hover { color: var(--fg); background: var(--surface); }
</style>
