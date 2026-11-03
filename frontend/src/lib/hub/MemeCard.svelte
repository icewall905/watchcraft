<script lang="ts">
	import { focusable } from '$lib/nav';
	import { ago } from '$lib/hub/api';
	import type { Item } from '$lib/hub/types';
	import LocalImage from '$lib/hub/LocalImage.svelte';
	import Icon from '$lib/components/Icon.svelte';

	let { item, onopen, ontune, verdict = null, busy = false }: {
		item: Item; onopen: (item: Item) => void;
		ontune?: (item: Item, verdict: 'more' | 'less') => void;
		verdict?: 'more' | 'less' | null; busy?: boolean;
	} = $props();

	const meme = $derived(item.meme);
	// Feedback tunes the curators that actually found this meme; a manually added
	// item has none, so the thumbs stay out of the way.
	const tunable = $derived(!!ontune && (item.curator_ids?.length ?? 0) > 0);
</script>

<div class="meme-card">
	<button class="open" use:focusable onclick={() => onopen(item)} aria-label={`Open meme: ${item.title}`}>
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
		</div>
	</button>
	<div class="acts">
		{#if tunable}
			<button class="icon-btn thumb" class:on={verdict === 'more'} aria-pressed={verdict === 'more'} disabled={busy}
				title="More memes like this" aria-label={`More memes like this: ${item.title}`} use:focusable
				onclick={() => ontune?.(item, 'more')}>👍</button>
			<button class="icon-btn thumb" class:on={verdict === 'less'} aria-pressed={verdict === 'less'} disabled={busy}
				title="Fewer memes like this" aria-label={`Fewer memes like this: ${item.title}`} use:focusable
				onclick={() => ontune?.(item, 'less')}>👎</button>
		{/if}
		<a class="icon-btn" href={item.url} target="_blank" rel="noreferrer" use:focusable aria-label="Open original post"><Icon name="external" /></a>
	</div>
</div>

<style>
	.meme-card { display: grid; gap: .5rem; background: var(--surface); border: 1px solid var(--border); border-radius: var(--r-card); overflow: hidden; color: inherit; }
	.meme-card:hover { border-color: var(--accent); }
	/* The card body is the button; the actions sit beside it so the thumbs are real buttons. */
	.open { display: grid; gap: .5rem; text-align: start; background: none; border: 0; padding: 0; color: inherit; cursor: pointer; font: inherit; }
	/* The picture is the content: show all of it, letterboxed rather than cropped. */
	.shot { position: relative; display: grid; place-items: center; min-height: 6rem; background: rgba(255, 255, 255, .04); overflow: hidden; }
	.shot :global(img) { width: 100%; height: auto; max-height: 34rem; object-fit: contain; display: block; }
	.text-meme { padding: 1.2rem 1rem; font-size: .95rem; font-weight: 600; display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical; overflow: hidden; }
	.shot.sensitive :global(img), .text-meme.sensitive { filter: blur(7px); }
	.clip { position: absolute; bottom: .5rem; left: .5rem; padding: .2rem .6rem; border-radius: var(--r-pill); background: rgba(0, 0, 0, .7); }
	.fmt, .dup { position: absolute; top: .5rem; font-size: .72rem; font-weight: 700; border-radius: var(--r-pill); padding: .15rem .55rem; background: rgba(0, 0, 0, .55); color: #f5f5f5; }
	.fmt { left: .5rem; }
	.dup { right: .5rem; color: #ffcf5c; }
	.meta { display: grid; gap: .15rem; padding: 0 .7rem .5rem; }
	/* Caption is a label, not the point of the page: one quiet line. */
	.title { margin: 0; font-size: .82rem; font-weight: 500; color: var(--muted); display: -webkit-box; -webkit-line-clamp: 1; -webkit-box-orient: vertical; overflow: hidden; }
	.title.sensitive { filter: blur(7px); }
	.warn { margin: 0; color: var(--warning); }
	.acts { display: flex; gap: .4rem; justify-content: end; padding: 0 .5rem .5rem; }
	.icon-btn { display: inline-grid; place-items: center; width: 2rem; height: 2rem; border-radius: 50%; color: var(--muted); background: none; border: 0; font-size: 1rem; cursor: pointer; }
	.icon-btn:hover { color: var(--fg); background: var(--surface); }
	.thumb { filter: grayscale(1); opacity: .55; }
	.thumb:hover:not(:disabled) { filter: none; opacity: 1; }
	.thumb.on { filter: none; opacity: 1; background: var(--surface); box-shadow: inset 0 0 0 1px var(--accent); }
	.thumb:disabled { cursor: progress; }
</style>
