<script lang="ts">
	// "The best of everything": the top pick from each Hub page in one magazine-style mosaic.
	import { focusable } from '$lib/nav';
	import Icon from '$lib/components/Icon.svelte';
	import LocalImage from '$lib/hub/LocalImage.svelte';
	import BoardImage from '$lib/hub/BoardImage.svelte';
	import type { Pic } from './loaders';

	export type Tile = Pic & { page: string; label: string; icon: string };
	let { tiles }: { tiles: Tile[] } = $props();
	const has = (p: Pic) => !!(p.img || p.itemId || p.boardUrl);
</script>

{#if tiles.length >= 3}
	<section class="best" aria-labelledby="best-head">
		<h2 id="best-head">The best of everything</h2>
		<div class="mosaic" data-nav-row>
			{#each tiles as t, i (t.href)}
				<a class="tile tone-{t.page}" class:hero={i === 0} class:textual={!has(t)} href={t.href} use:focusable aria-label={`${t.label}: ${t.title}`}>
					{#if has(t)}
						<span class="pic">{#if t.img}<img src={t.img} alt="" loading={i < 3 ? 'eager' : 'lazy'} />{:else if t.itemId}<LocalImage id={t.itemId} width={i === 0 ? 800 : 480} alt="" />{:else if t.boardUrl}<BoardImage url={t.boardUrl} alt="" fit />{/if}</span>
						<span class="shade"></span>
					{/if}
					<span class="page"><Icon name={t.icon} size="1em" /> {t.label}</span>
					<span class="text">
						<span class="title">{t.title}</span>
						{#if t.summary && (i === 0 || !has(t))}<span class="summary">{t.summary}</span>{/if}
						{#if t.meta}<span class="meta">{t.meta}</span>{/if}
					</span>
				</a>
			{/each}
		</div>
	</section>
{/if}

<style>
	.best { margin-bottom: clamp(1.4rem, 3vw, 2.2rem); }
	h2 { margin: 0 0 .9rem; font: 520 clamp(1.4rem, 2.8vw, 1.9rem) / 1.15 var(--font-display); letter-spacing: -.01em; }
	.mosaic { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); grid-auto-rows: minmax(12.5rem, auto); gap: clamp(.7rem, 1.4vw, 1rem); }
	.tile { --tone: var(--accent); position: relative; display: flex; flex-direction: column; justify-content: flex-end; min-width: 0; overflow: hidden; padding: 1rem; border-radius: var(--r-lg); background: var(--bg-2); color: #fff; text-decoration: none; isolation: isolate; }
	.tone-wire { --tone: oklch(68% 0.13 250); } .tone-finds { --tone: oklch(72% 0.12 185); } .tone-commons { --tone: oklch(72% 0.15 55); }
	.tone-memes { --tone: oklch(70% 0.16 350); } .tone-screen, .tone-subscriptions { --tone: oklch(66% 0.17 25); } .tone-scroll { --tone: oklch(68% 0.15 305); }
	.tone-boards { --tone: oklch(78% 0.13 85); } .tone-jukebox { --tone: oklch(72% 0.15 150); } .tone-stacks { --tone: oklch(70% 0.06 230); }
	.tone-almanac { --tone: oklch(74% 0.11 115); } .tone-dispatch { --tone: oklch(72% 0.09 210); } .tone-study, .tone-scout { --tone: oklch(70% 0.12 280); }
	.hero { grid-column: span 2; grid-row: span 2; padding: clamp(1.1rem, 2vw, 1.6rem); }
	.pic { position: absolute; inset: 0; z-index: -2; }
	.pic :global(img) { width: 100%; height: 100%; object-fit: cover; display: block; transition: transform .5s ease; }
	.tile:hover .pic :global(img), .tile:focus-visible .pic :global(img) { transform: scale(1.04); }
	.shade { position: absolute; inset: 0; z-index: -1; background: linear-gradient(to top, rgb(0 0 0 / .88) 0%, rgb(0 0 0 / .45) 45%, rgb(0 0 0 / .05) 75%); }
	.textual { color: var(--ink); background: linear-gradient(160deg, color-mix(in srgb, var(--tone) 22%, var(--surface)), var(--surface)); border: 1px solid color-mix(in srgb, var(--tone) 35%, var(--rule)); justify-content: flex-start; }
	.page { position: absolute; top: .75rem; left: .75rem; display: inline-flex; align-items: center; gap: .35rem; padding: .25rem .6rem; border-radius: 999px; background: color-mix(in srgb, var(--tone) 85%, #000); color: #111; font-size: .75rem; font-weight: 700; }
	.textual .page { position: static; align-self: flex-start; margin-bottom: .8rem; }
	.text { display: grid; gap: .35rem; }
	.title { overflow: hidden; display: -webkit-box; -webkit-line-clamp: 3; line-clamp: 3; -webkit-box-orient: vertical; font: 540 1.05rem / 1.3 var(--font-display); overflow-wrap: anywhere; text-shadow: 0 1px 2px rgb(0 0 0 / .4); }
	.textual .title { text-shadow: none; }
	.hero .title { font-size: clamp(1.5rem, 2.8vw, 2.2rem); line-height: 1.18; }
	.summary { overflow: hidden; display: -webkit-box; -webkit-line-clamp: 4; line-clamp: 4; -webkit-box-orient: vertical; font-size: .9rem; line-height: 1.5; opacity: .9; }
	.hero .summary { font-size: 1rem; -webkit-line-clamp: 3; line-clamp: 3; max-width: 46rem; }
	.meta { font-size: .75rem; opacity: .75; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
	.tile:focus-visible { outline: 3px solid var(--tone); outline-offset: 3px; }
	@media (max-width: 1099px) { .mosaic { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
	@media (max-width: 599px) {
		.mosaic { grid-auto-rows: minmax(10.5rem, auto); gap: .6rem; } .hero { grid-row: span 1; min-height: 16rem; }
		.tile:not(.hero) { padding: .7rem; } .tile:not(.hero) .title { font-size: .9rem; -webkit-line-clamp: 4; line-clamp: 4; }
		.tile:not(.hero) .summary { display: none; } .page { top: .5rem; left: .5rem; font-size: .68rem; }
	}
</style>
