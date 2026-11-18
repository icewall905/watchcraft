<script lang="ts">
	// One Hub page on The Front Page: a cover picture, a strip of what is waiting, and its sub-categories.
	import { focusable } from '$lib/nav';
	import Icon from '$lib/components/Icon.svelte';
	import LocalImage from '$lib/hub/LocalImage.svelte';
	import BoardImage from '$lib/hub/BoardImage.svelte';
	import type { Chip, Panel, Pic } from './loaders';

	let { panel, label, icon, size = 'medium' }: { panel: Panel; label: string; icon: string; size?: 'wide' | 'medium' | 'compact' } = $props();
	const withPic = $derived(panel.pics.filter((p) => p.img || p.itemId || p.boardUrl));
	const cover = $derived(withPic[0]);
	const strip = $derived(withPic.slice(1, size === 'wide' ? 7 : 5));
	const lines = $derived(size === 'compact' ? (panel.lines?.length ? panel.lines : panel.pics).slice(0, 3) : []);
	const unit = $derived(panel.waiting === 1 ? panel.unit[0] : panel.unit[1]);
</script>

{#snippet pic(p: Pic | Chip, alt = '')}
	{#if p.img}<img src={p.img} {alt} loading="lazy" />{:else if p.itemId}<LocalImage id={p.itemId} {alt} />{:else if p.boardUrl}<BoardImage url={p.boardUrl} {alt} fit />{/if}
{/snippet}

<article class="panel {size}" class:urgent={panel.urgent} aria-label={label} data-panel={panel.key}>
	{#if size !== 'compact' && cover}
		<a class="cover" href={cover.href} use:focusable aria-label={`${label}: ${cover.title}`}>
			{@render pic(cover)}
			<span class="shade"></span>
			<span class="badge"><Icon name={icon} size="1.05em" /> {label}</span>
			<span class="cover-text">
				<span class="count"><strong>{panel.more ? `${panel.waiting}+` : panel.waiting}</strong> {unit}</span>
				{#if panel.today > 0}<span class="today">+{panel.today} today</span>{/if}
				<span class="cover-title">{cover.title}</span>
			</span>
		</a>
		{#if strip.length}
			<div class="strip" data-nav-row>
				{#each strip as p, i (i)}
					<a href={p.href} use:focusable title={p.title} aria-label={p.title}>{@render pic(p)}</a>
				{/each}
			</div>
		{/if}
	{:else}
		<a class="head" href={panel.href} use:focusable>
			<span class="icon"><Icon name={icon} /></span>
			<span class="name">{label}</span>
			<span class="count"><strong>{panel.more ? `${panel.waiting}+` : panel.waiting}</strong> {unit}{#if panel.today > 0} <span class="today">+{panel.today} today</span>{/if}</span>
		</a>
		{#if lines.length}
			<ul>
				{#each lines as p, i (i)}
					<li><a href={p.href} use:focusable><span class="t">{p.title}</span>{#if p.meta}<span class="m">{p.meta}</span>{/if}</a></li>
				{/each}
			</ul>
		{/if}
	{/if}
	{#if panel.chips.length}
		<div class="chips" data-nav-row aria-label={`${label} by category`}>
			{#each panel.chips as c, i (i)}
				<a class="chip" href={c.href} use:focusable>
					{#if c.img || c.itemId || c.boardUrl}<span class="dot">{@render pic(c)}</span>{/if}
					<span class="chip-label">{c.title}</span>
					{#if c.count}<span class="chip-count">{c.count}</span>{/if}
				</a>
			{/each}
		</div>
	{/if}
	<a class="open" href={panel.href} use:focusable tabindex="-1">Open {label} →</a>
</article>

<style>
	.panel { display: flex; flex-direction: column; gap: .75rem; min-width: 0; padding: .7rem; border: 1px solid var(--rule); border-radius: var(--r-lg); background: var(--surface); }
	.panel.compact { padding: 1rem 1.05rem .85rem; }
	.panel.urgent { border-color: color-mix(in srgb, var(--bad) 55%, var(--rule)); }
	.cover { position: relative; display: block; aspect-ratio: 16 / 9; min-height: 12.5rem; overflow: hidden; border-radius: calc(var(--r-lg) - .3rem); background: var(--bg-2); color: #fff; text-decoration: none; }
	.wide .cover { aspect-ratio: 2 / 1; min-height: 15rem; }
	.cover :global(img) { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; display: block; transition: transform .4s ease; }
	.cover:hover :global(img), .cover:focus-visible :global(img) { transform: scale(1.03); }
	.shade { position: absolute; inset: 0; background: linear-gradient(to top, rgb(0 0 0 / .82), rgb(0 0 0 / .25) 55%, rgb(0 0 0 / .35)); }
	.badge { position: absolute; top: .7rem; left: .7rem; display: inline-flex; align-items: center; gap: .4rem; padding: .3rem .65rem; border-radius: 999px; background: rgb(0 0 0 / .62); font: 600 var(--fs-sm) / 1.2 var(--font-display); }
	.cover-text { position: absolute; inset: auto 0 0 0; display: grid; gap: .25rem; padding: .9rem 1rem; }
	.cover .count { font-size: var(--fs-sm); color: rgb(255 255 255 / .85); }
	.cover .count strong { font: 600 clamp(1.6rem, 3vw, 2.3rem) / 1 var(--font-display); color: #fff; margin-right: .2rem; font-variant-numeric: tabular-nums; }
	.cover .today { justify-self: start; }
	.today { display: inline-block; padding: .1rem .5rem; border-radius: 999px; background: var(--accent); color: var(--paper, #111); font-size: .72rem; font-weight: 700; }
	.cover-title { overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; font: 500 var(--fs-md) / 1.3 var(--font-display); overflow-wrap: anywhere; }
	.wide .cover-title { font-size: clamp(1.05rem, 1.8vw, 1.4rem); }
	.strip { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .4rem; }
	.wide .strip { grid-template-columns: repeat(6, minmax(0, 1fr)); }
	.strip a { display: block; aspect-ratio: 1; border-radius: var(--r-sm); overflow: hidden; background: var(--bg-2); outline-offset: 2px; }
	.strip a :global(img), .dot :global(img) { width: 100%; height: 100%; object-fit: cover; display: block; }
	.strip a:hover, .strip a:focus-visible { outline: 2px solid var(--accent); }
	.head { display: grid; grid-template-columns: auto 1fr; column-gap: .65rem; align-items: center; color: var(--ink); text-decoration: none; }
	.icon { grid-row: span 2; display: grid; place-items: center; width: 2.3rem; height: 2.3rem; border-radius: var(--r-md); background: var(--surface-2); color: var(--accent); }
	.urgent .icon { color: var(--bad); }
	.name { font: 560 var(--fs-md) / 1.25 var(--font-display); }
	.head:hover .name, .head:focus-visible .name { color: var(--accent); }
	.head .count { color: var(--muted); font-size: var(--fs-sm); }
	.head .count strong { color: var(--ink); font-variant-numeric: tabular-nums; }
	.urgent .head .count strong { color: var(--bad); }
	ul { list-style: none; margin: 0; padding: 0; display: grid; gap: .45rem; }
	li a { display: grid; gap: .05rem; min-width: 0; color: var(--ink); text-decoration: none; }
	.t { overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; font-size: var(--fs-sm); line-height: 1.35; overflow-wrap: anywhere; }
	.m { overflow: hidden; color: var(--faint); font-size: .75rem; text-overflow: ellipsis; white-space: nowrap; }
	li a:hover .t, li a:focus-visible .t { color: var(--accent); }
	.chips { display: flex; gap: .4rem; overflow-x: auto; padding-bottom: .15rem; scrollbar-width: thin; }
	.compact .chips { flex-wrap: wrap; overflow: visible; }
	.chip { flex: none; display: inline-flex; align-items: center; gap: .4rem; max-width: 15rem; padding: .25rem .6rem .25rem .3rem; border: 1px solid var(--rule); border-radius: 999px; background: var(--bg-2); color: var(--ink); font-size: .8rem; text-decoration: none; }
	.chip:not(:has(.dot)) { padding-left: .6rem; }
	.chip:hover, .chip:focus-visible { border-color: var(--accent); }
	.dot { flex: none; width: 1.45rem; height: 1.45rem; border-radius: 50%; overflow: hidden; background: var(--surface-2); }
	.chip-label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
	.chip-count { color: var(--muted); font-variant-numeric: tabular-nums; }
	.open { margin-top: auto; padding-inline: .3rem; color: var(--accent); font-size: var(--fs-sm); font-weight: 600; text-decoration: none; }
	.compact .open { padding-inline: 0; }
	.open:hover { text-decoration: underline; }
</style>
