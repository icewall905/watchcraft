<script lang="ts">
	// One Hub page on The Front Page. `shelf`: a header, its sub-categories and a row of readable cards
	// (picture, title, source). `compact`: a small card for pages that are mostly text.
	import { focusable } from '$lib/nav';
	import Icon from '$lib/components/Icon.svelte';
	import LocalImage from '$lib/hub/LocalImage.svelte';
	import BoardImage from '$lib/hub/BoardImage.svelte';
	import type { Chip, Panel, Pic } from './loaders';

	let { panel, label, icon, size = 'shelf', square = false }: { panel: Panel; label: string; icon: string; size?: 'shelf' | 'compact'; square?: boolean } = $props();
	const lines = $derived((panel.lines?.length ? panel.lines : panel.pics).slice(0, 3));
	const unit = $derived(panel.waiting === 1 ? panel.unit[0] : panel.unit[1]);
	const count = $derived(panel.more ? `${panel.waiting}+` : String(panel.waiting));
	const has = (p: Pic | Chip) => !!(p.img || p.itemId || p.boardUrl);
	// Cards that carry a summary get room to read it: three per row instead of six.
	const reading = $derived(panel.pics.some((p) => p.summary));
</script>

{#snippet pic(p: Pic | Chip, w: number)}
	{#if p.img}<img src={p.img} alt="" loading="lazy" />{:else if p.itemId}<LocalImage id={p.itemId} width={w} alt="" />{:else if p.boardUrl}<BoardImage url={p.boardUrl} alt="" fit />{/if}
{/snippet}

{#snippet chips()}
	{#if panel.chips.length}
		<div class="chips" data-nav-row aria-label={`${label} by category`}>
			{#each panel.chips as c, i (i)}
				<a class="chip" href={c.href} use:focusable>
					{#if has(c)}<span class="dot">{@render pic(c, 240)}</span>{/if}
					<span class="chip-label">{c.title}</span>
					{#if c.count}<span class="chip-count">{c.count}</span>{/if}
				</a>
			{/each}
		</div>
	{/if}
{/snippet}

{#if size === 'shelf'}
	<article class="shelf" class:urgent={panel.urgent} aria-label={label} data-panel={panel.key}>
		<header class="shelf-head">
			<a class="name" href={panel.href} use:focusable><span class="icon"><Icon name={icon} /></span><h3>{label}</h3></a>
			<p class="stats"><strong>{count}</strong> {unit}{#if panel.today > 0}<span class="today">+{panel.today} today</span>{/if}</p>
			<a class="open" href={panel.href} use:focusable tabindex="-1">Open {label} →</a>
		</header>
		{@render chips()}
		<div class="cards" class:square class:reading data-nav-row>
			{#each panel.pics.slice(0, 6) as p, i (i)}
				<a class="card" href={p.href} use:focusable>
					<span class="frame" class:empty={!has(p)}>{#if has(p)}{@render pic(p, 480)}{:else}<Icon name={icon} size="1.6em" />{/if}</span>
					<span class="title">{p.title}</span>
					{#if p.summary}<span class="summary">{p.summary}</span>{/if}
					{#if p.meta}<span class="meta">{p.meta}</span>{/if}
				</a>
			{/each}
		</div>
	</article>
{:else}
	<article class="compact" class:urgent={panel.urgent} aria-label={label} data-panel={panel.key}>
		<a class="head" href={panel.href} use:focusable>
			<span class="icon"><Icon name={icon} /></span>
			<span class="cname">{label}</span>
			<span class="stats"><strong>{count}</strong> {unit}{#if panel.today > 0}<span class="today">+{panel.today} today</span>{/if}</span>
		</a>
		{#if lines.length}
			<ul>
				{#each lines as p, i (i)}
					<li><a href={p.href} use:focusable><span class="t">{p.title}</span>{#if p.meta}<span class="m">{p.meta}</span>{/if}</a></li>
				{/each}
			</ul>
		{/if}
		{@render chips()}
		<a class="open" href={panel.href} use:focusable tabindex="-1">Open {label} →</a>
	</article>
{/if}

<style>
	.shelf { display: grid; gap: .85rem; min-width: 0; padding-block: clamp(1.2rem, 2.5vw, 1.8rem); border-top: 1px solid var(--rule); }
	.shelf-head { display: flex; align-items: center; flex-wrap: wrap; gap: .4rem 1rem; min-width: 0; }
	.name { display: inline-flex; align-items: center; gap: .6rem; color: var(--ink); text-decoration: none; }
	.name h3 { margin: 0; font: 540 clamp(1.25rem, 2.4vw, 1.6rem) / 1.2 var(--font-display); }
	.name:hover h3, .name:focus-visible h3 { color: var(--accent); }
	.icon { display: grid; place-items: center; flex: none; width: 2.2rem; height: 2.2rem; border-radius: var(--r-md); background: var(--surface-2); color: var(--accent); }
	.urgent .icon { color: var(--bad); }
	.stats { display: inline-flex; align-items: center; flex-wrap: wrap; gap: .45rem; margin: 0; color: var(--muted); font-size: var(--fs-sm); }
	.stats strong { color: var(--ink); font-variant-numeric: tabular-nums; font-size: var(--fs-md); }
	.urgent .stats strong { color: var(--bad); }
	.today { display: inline-block; padding: .1rem .5rem; border-radius: 999px; background: var(--accent); color: var(--paper, #111); font-size: .72rem; font-weight: 700; }
	.shelf .open { margin-left: auto; }
	.open { color: var(--accent); font-size: var(--fs-sm); font-weight: 600; text-decoration: none; white-space: nowrap; }
	.open:hover { text-decoration: underline; }

	.chips { display: flex; flex-wrap: wrap; gap: .4rem; }
	.chip { display: inline-flex; align-items: center; gap: .4rem; max-width: 100%; padding: .28rem .65rem .28rem .3rem; border: 1px solid var(--rule); border-radius: 999px; background: var(--bg-2); color: var(--ink); font-size: .82rem; text-decoration: none; }
	.chip:not(:has(.dot)) { padding-left: .65rem; }
	.chip:hover, .chip:focus-visible { border-color: var(--accent); }
	.dot { flex: none; width: 1.45rem; height: 1.45rem; border-radius: 50%; overflow: hidden; background: var(--surface-2); }
	.dot :global(img), .frame :global(img) { width: 100%; height: 100%; object-fit: cover; display: block; }
	.chip-label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
	.chip-count { color: var(--muted); font-variant-numeric: tabular-nums; }

	.cards { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: clamp(.7rem, 1.4vw, 1.1rem); }
	.card { display: grid; align-content: start; gap: .45rem; min-width: 0; color: var(--ink); text-decoration: none; }
	.frame { display: grid; place-items: center; aspect-ratio: 16 / 10; overflow: hidden; border-radius: var(--r-md); background: var(--bg-2); color: var(--faint); }
	.square .frame { aspect-ratio: 1; }
	.frame.empty { background: linear-gradient(135deg, var(--surface-2), var(--bg-2)); }
	.card :global(img) { transition: transform .35s ease; }
	.card:hover :global(img), .card:focus-visible :global(img) { transform: scale(1.04); }
	.card:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; border-radius: var(--r-md); }
	.title { overflow: hidden; display: -webkit-box; -webkit-line-clamp: 3; line-clamp: 3; -webkit-box-orient: vertical; font: 500 .95rem / 1.35 var(--font-display); overflow-wrap: anywhere; }
	.card:hover .title, .card:focus-visible .title { color: var(--accent); }
	.summary { overflow: hidden; display: -webkit-box; -webkit-line-clamp: 4; line-clamp: 4; -webkit-box-orient: vertical; color: var(--ink-soft); font-size: .86rem; line-height: 1.5; overflow-wrap: anywhere; }
	.reading { grid-template-columns: repeat(3, minmax(0, 1fr)); row-gap: clamp(1.1rem, 2vw, 1.6rem); }
	.reading .title { font-size: 1.05rem; -webkit-line-clamp: 2; line-clamp: 2; }
	.meta { overflow: hidden; color: var(--faint); font-size: .75rem; text-overflow: ellipsis; white-space: nowrap; }
	@media (max-width: 1199px) {
		.cards { grid-template-columns: repeat(4, minmax(0, 1fr)); } .card:nth-child(n + 5) { display: none; }
		.reading { grid-template-columns: repeat(2, minmax(0, 1fr)); } .reading .card:nth-child(n + 5) { display: grid; }
	}
	@media (max-width: 760px) {
		.cards { display: flex; overflow-x: auto; scroll-snap-type: x mandatory; padding-bottom: .3rem; scrollbar-width: thin; }
		.card, .card:nth-child(n + 5) { display: grid; flex: 0 0 min(15rem, 68%); scroll-snap-align: start; }
		.reading .card { flex-basis: min(19rem, 82%); }
		.shelf .open { margin-left: 0; }
	}

	.compact { display: flex; flex-direction: column; gap: .7rem; min-width: 0; padding: 1rem 1.05rem .85rem; border: 1px solid var(--rule); border-radius: var(--r-lg); background: var(--surface); }
	.compact.urgent { border-color: color-mix(in srgb, var(--bad) 55%, var(--rule)); }
	.head { display: grid; grid-template-columns: auto 1fr; column-gap: .65rem; align-items: center; color: var(--ink); text-decoration: none; }
	.head .icon { grid-row: span 2; }
	.cname { font: 560 var(--fs-md) / 1.25 var(--font-display); }
	.head:hover .cname, .head:focus-visible .cname { color: var(--accent); }
	ul { list-style: none; margin: 0; padding: 0; display: grid; gap: .5rem; }
	li a { display: grid; gap: .05rem; min-width: 0; color: var(--ink); text-decoration: none; }
	.t { overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; font-size: var(--fs-sm); line-height: 1.35; overflow-wrap: anywhere; }
	.m { overflow: hidden; color: var(--faint); font-size: .75rem; text-overflow: ellipsis; white-space: nowrap; }
	li a:hover .t, li a:focus-visible .t { color: var(--accent); }
	.compact .open { margin-top: auto; }
</style>
