<script lang="ts">
	import { focusable } from '$lib/nav';
	import { href, img, subtitleLine, type Item } from './api';

	let { item, wide = false, group }: { item: Item; wide?: boolean; group?: string } = $props();
	const src = $derived(wide ? img(item.thumb ?? item.backdrop ?? item.image, 480) : img(item.image ?? item.thumb, 300));
</script>

<a class="card" class:wide href={href(item)} use:focusable={{ group }} aria-label={item.type === 'Episode' ? `${item.series_name}: ${item.name}` : item.name}>
	<span class="art">
		{#if src}<img {src} alt="" loading="lazy" />{:else}<span class="ph">{item.name.slice(0, 1)}</span>{/if}
		{#if item.played}<span class="badge done" aria-label="Watched">✓</span>
		{:else if item.unplayed}<span class="badge">{item.unplayed}</span>{/if}
		{#if item.progress > 0 && !item.played}<span class="bar"><i style:width="{Math.round(item.progress * 100)}%"></i></span>{/if}
	</span>
	<span class="t">{item.type === 'Episode' && wide ? item.series_name : item.name}</span>
	<span class="s">{item.type === 'Episode' && wide ? `S${item.season ?? '?'}E${item.episode ?? '?'} · ${item.name}` : subtitleLine(item)}</span>
</a>

<style>
	.card {
		display: grid;
		gap: 0.3rem;
		width: clamp(8.5rem, 13vw, 12rem);
		flex: 0 0 auto;
		color: inherit;
		text-decoration: none;
		border-radius: var(--r-md);
	}
	.card.wide { width: clamp(14rem, 22vw, 20rem); }
	.art {
		position: relative;
		display: block;
		aspect-ratio: 2 / 3;
		border-radius: var(--r-md);
		overflow: hidden;
		background: var(--surface-solid, #222);
		box-shadow: var(--shadow-calm);
	}
	.wide .art { aspect-ratio: 16 / 9; }
	img { width: 100%; height: 100%; object-fit: cover; display: block; }
	.ph { display: grid; place-items: center; height: 100%; font-size: 2.5rem; color: var(--muted); }
	.badge {
		position: absolute; top: 0.4rem; right: 0.4rem; min-width: 1.5rem; padding: 0.1rem 0.4rem;
		border-radius: 999px; background: var(--accent); color: var(--accent-ink); font-size: var(--fs-xs); text-align: center;
	}
	.badge.done { background: var(--ok); }
	.bar { position: absolute; left: 0; right: 0; bottom: 0; height: 4px; background: rgba(0, 0, 0, 0.5); }
	.bar i { display: block; height: 100%; background: var(--accent); }
	.t { font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
	.s { font-size: var(--fs-sm); color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
	.card:focus-visible .art, .card:hover .art { outline: 3px solid var(--accent); outline-offset: 2px; }
</style>
