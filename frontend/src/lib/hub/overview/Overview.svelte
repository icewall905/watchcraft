<script lang="ts">
	// "Where to next": every Hub page this profile may open, ordered by what waits there.
	import { api } from '$lib/api';
	import { hub } from '$lib/hub/api';
	import { ALL_ENTRIES } from '$lib/navigation';
	import { session } from '$lib/stores/session.svelte';
	import type { HomeRow } from '$lib/types';
	import type { TodayOverview } from '$lib/hub/types';
	import PagePanel from './PagePanel.svelte';
	import BestOf, { type Tile } from './BestOf.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { focusable } from '$lib/nav';
	import { LOADERS, pictured, rank, type Panel, type Shared } from './loaders';

	// `exclude`: the lead story's link, already shown big above, so the mosaic does not repeat it.
	let { refresh = 0, exclude = '' }: { refresh?: number; exclude?: string } = $props();
	const EXTRA: Record<string, { label: string; icon: string }> = {
		'/memes': { label: 'Meme Radar', icon: 'team' }, '/subscriptions': { label: 'Subscriptions', icon: 'subs' },
		'/alerts': { label: 'Alerts', icon: 'alert' }, '/tv': { label: 'The Screen', icon: 'tv' }
	};
	const meta = (href: string) => {
		const e = ALL_ENTRIES.find((x) => x.href === href);
		return { label: e?.label ?? EXTRA[href]?.label ?? href, icon: e?.icon ?? EXTRA[href]?.icon ?? 'news' };
	};

	let panels = $state<Panel[]>([]);
	let quiet = $state<string[]>([]);
	let pending = $state(0);
	let shownFor = '';

	const ranked = $derived(rank(panels));
	const urgent = $derived(ranked.filter((p) => p.urgent));
	// Pages with a few things to show get a shelf of readable cards; mostly-text pages a small card.
	const shelves = $derived(ranked.filter((p) => !p.urgent && (pictured(p) >= 1 ? p.pics.length >= 2 : p.pics.length >= 4)));
	const rail = $derived(ranked.filter((p) => !p.urgent && !shelves.includes(p)));
	const SQUARE = new Set(['memes', 'boards', 'jukebox']);
	const hasPic = (p: { img?: string; itemId?: number; boardUrl?: string }) => !!(p.img || p.itemId || p.boardUrl);
	// The best of everything: each page's top pick in rank order, then second picks, a pictured one leading.
	const tiles = $derived.by(() => {
		const pages = ranked.filter((p) => !p.urgent);
		const pool: Tile[] = [];
		for (let round = 0; round < 2; round++)
			for (const p of pages) {
				const own = [...p.pics, ...(p.lines ?? [])].sort((a, b) => Number(hasPic(b)) - Number(hasPic(a)));
				const pick = own[round];
				if (pick && pick.href !== exclude && !pool.some((t) => t.href === pick.href)) pool.push({ ...pick, page: p.key, label: meta(p.href).label, icon: meta(p.href).icon });
			}
		const lead = pool.findIndex(hasPic);
		if (lead > 0) pool.unshift(...pool.splice(lead, 1));
		// A hero plus a full grid: 8 small tiles (3 rows) when there are enough, else 4 (2 rows).
		return pool.slice(0, pool.length >= 9 ? 9 : 5);
	});
	// The last overview is kept per profile so Today opens with it at once, then refreshes.
	const cacheKey = (pid: number | null) => `wc-frontpage:${pid}`;
	function readCache(pid: number | null, version: number): { panels: Panel[]; quiet: string[] } | null {
		try {
			const c = JSON.parse(localStorage.getItem(cacheKey(pid)) ?? 'null');
			return c && c.version === version && Array.isArray(c.panels) ? c : null;
		} catch { return null; }
	}
	function writeCache(pid: number | null, version: number, data: { panels: Panel[]; quiet: string[] }) {
		try { localStorage.setItem(cacheKey(pid), JSON.stringify({ version, at: Date.now(), ...data })); } catch { /* storage full or blocked */ }
	}

	$effect(() => {
		const version = session.version;
		refresh;
		if (!session.ready || session.needsLogin || session.choosing || !session.hubAllowed) { panels = []; quiet = []; pending = 0; shownFor = ''; return; }
		const pid = session.profileId;
		// Another profile's panels never stay on screen: start from this profile's cache or nothing.
		if (shownFor !== `${pid}:${version}`) {
			const cached = readCache(pid, version);
			panels = cached?.panels ?? []; quiet = cached?.quiet ?? [];
			shownFor = `${pid}:${version}`;
		}
		const controller = new AbortController();
		const current = () => !controller.signal.aborted && version === session.version;
		let overview: Promise<TodayOverview | null> | null = null;
		let home: Promise<HomeRow[]> | null = null;
		const shared: Shared = {
			overview: () => (overview ??= hub.todayOverview(controller.signal).catch(() => null)),
			home: () => (home ??= api.home(12))
		};
		const visible = LOADERS.filter((l) => session.canOpen(l.href));
		const next: Panel[] = [];
		const empty: string[] = [];
		pending = visible.length;
		for (const l of visible) {
			void l.load(controller.signal, shared).catch(() => null).then((p) => {
				if (!current()) return;
				if (p) next.push(p); else empty.push(meta(l.href).label);
				pending--;
				// Show panels as they arrive; the first full pass replaces what an earlier load showed.
				if (!pending || panels.length === 0 || next.length > panels.length) { panels = [...next]; quiet = [...empty]; }
				if (!pending) writeCache(pid, version, { panels: next, quiet: empty });
			});
		}
		return () => controller.abort();
	});
</script>

<section class="overview" aria-labelledby="where-next">
	{#if urgent.length}
		<div class="urgent-row" data-nav-row>
			{#each urgent as p (p.key)}<PagePanel panel={p} size="compact" {...meta(p.href)} />{/each}
		</div>
	{/if}
	<BestOf {tiles} />
	<header class="head">
		<h2 id="where-next">Where to next</h2>
		<p>What is waiting on each page, busiest first.</p>
	</header>
	{#if shelves.length + rail.length > 1}
		<nav class="jump" aria-label="Jump to a page" data-nav-row>
			{#each [...shelves, ...rail] as p (p.key)}
				<a href={`#page-${p.key}`} class="tone-{p.key}" use:focusable><Icon name={meta(p.href).icon} size="1em" /> {meta(p.href).label} <span>{p.waiting}</span></a>
			{/each}
		</nav>
	{/if}
	<div class="shelves">
		{#each shelves as p (p.key)}<PagePanel panel={p} id={`page-${p.key}`} square={SQUARE.has(p.key)} {...meta(p.href)} />{/each}
	</div>
	{#if pending && !panels.length}
		{#each [0, 1] as i (i)}<div class="skeleton" aria-hidden="true"></div>{/each}
	{/if}
	{#if rail.length}
		<h3 class="rail-head">Also waiting</h3>
		<div class="rail" data-nav-row>
			{#each rail as p (p.key)}<PagePanel panel={p} size="compact" id={`page-${p.key}`} {...meta(p.href)} />{/each}
		</div>
	{/if}
	{#if !pending && !panels.length}
		<p class="quiet">Nothing is waiting anywhere in The Hub right now.</p>
	{:else if !pending && quiet.length}
		<p class="quiet">All caught up in {quiet.join(', ')}.</p>
	{/if}
	{#if pending}<p class="sr-only" role="status">Checking {pending} more pages…</p>{/if}
</section>

<style>
	.overview { margin-block: clamp(1.5rem, 3.5vw, 2.6rem); }
	.head { display: flex; align-items: baseline; gap: .8rem; flex-wrap: wrap; margin-bottom: 1rem; }
	.head h2 { margin: 0; font: 520 clamp(1.5rem, 3vw, 2.1rem) / 1.15 var(--font-display); letter-spacing: -.015em; }
	.head p { margin: 0; color: var(--faint); font-size: var(--fs-sm); }
	.shelves { display: grid; gap: clamp(1rem, 2.2vw, 1.6rem); }
	.jump { position: sticky; top: 0; z-index: 5; display: flex; gap: .4rem; overflow-x: auto; margin: 0 calc(-1 * var(--gutter, 0px)) 1rem; padding: .6rem var(--gutter, 0px); background: color-mix(in srgb, var(--bg, var(--paper)) 88%, transparent); backdrop-filter: blur(8px); scrollbar-width: none; }
	.jump a { --tone: var(--accent); flex: none; display: inline-flex; align-items: center; gap: .4rem; padding: .35rem .75rem; border: 1px solid var(--rule); border-radius: 999px; background: var(--surface); color: var(--ink); font-size: .82rem; font-weight: 600; text-decoration: none; }
	.jump a :global(svg) { color: var(--tone); }
	.jump a span { color: var(--muted); font-weight: 500; font-variant-numeric: tabular-nums; }
	.jump a:hover, .jump a:focus-visible { border-color: var(--tone); }
	.tone-wire { --tone: oklch(68% 0.13 250); } .tone-finds { --tone: oklch(72% 0.12 185); } .tone-commons { --tone: oklch(72% 0.15 55); }
	.tone-memes { --tone: oklch(70% 0.16 350); } .tone-screen, .tone-subscriptions { --tone: oklch(66% 0.17 25); } .tone-scroll { --tone: oklch(68% 0.15 305); }
	.tone-boards { --tone: oklch(78% 0.13 85); } .tone-jukebox { --tone: oklch(72% 0.15 150); } .tone-stacks { --tone: oklch(70% 0.06 230); }
	.tone-almanac { --tone: oklch(74% 0.11 115); } .tone-dispatch { --tone: oklch(72% 0.09 210); } .tone-study, .tone-scout { --tone: oklch(70% 0.12 280); }
	.urgent-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 22rem), 1fr)); gap: .9rem; margin-bottom: .9rem; }
	.skeleton { min-height: 14rem; margin-top: 1rem; border-radius: var(--r-lg); background: linear-gradient(100deg, var(--bg-2) 30%, var(--surface-2) 50%, var(--bg-2) 70%) 0 0 / 300% 100%; animation: shimmer 1.4s linear infinite; }
	@keyframes shimmer { to { background-position: -150% 0; } }
	@media (prefers-reduced-motion: reduce) { .skeleton { animation: none; } }
	.rail-head { margin: clamp(1.4rem, 3vw, 2rem) 0 .8rem; color: var(--ink-soft); font: 500 var(--fs-md) / 1.3 var(--font-display); }
	.rail { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 17rem), 1fr)); gap: clamp(.7rem, 1.6vw, 1.1rem); }
	.quiet { margin: 1rem 0 0; color: var(--faint); font-size: var(--fs-sm); }
	.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
</style>
