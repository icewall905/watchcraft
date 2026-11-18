<script lang="ts">
	// "Where to next": every Hub page this profile may open, ordered by what waits there.
	import { api } from '$lib/api';
	import { hub } from '$lib/hub/api';
	import { ALL_ENTRIES } from '$lib/navigation';
	import { session } from '$lib/stores/session.svelte';
	import type { HomeRow } from '$lib/types';
	import type { TodayOverview } from '$lib/hub/types';
	import PagePanel from './PagePanel.svelte';
	import { LOADERS, pictured, rank, type Panel, type Shared } from './loaders';

	let { refresh = 0 }: { refresh?: number } = $props();
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

	const ranked = $derived(rank(panels));
	const urgent = $derived(ranked.filter((p) => p.urgent));
	const visual = $derived(ranked.filter((p) => !p.urgent && pictured(p) >= 2));
	const rail = $derived(ranked.filter((p) => !p.urgent && pictured(p) < 2));
	const sizeOf = (p: Panel, i: number) => (i < 2 && pictured(p) >= 4 ? 'wide' : 'medium');

	$effect(() => {
		const version = session.version;
		refresh;
		if (!session.ready || session.needsLogin || session.choosing || !session.hubAllowed) { panels = []; quiet = []; pending = 0; return; }
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
			});
		}
		return () => controller.abort();
	});
</script>

<section class="overview" aria-labelledby="where-next">
	<header class="head">
		<h2 id="where-next">Where to next</h2>
		<p>What is waiting on each page, newest first.</p>
	</header>
	{#if urgent.length}
		<div class="urgent-row" data-nav-row>
			{#each urgent as p (p.key)}<PagePanel panel={p} size="compact" {...meta(p.href)} />{/each}
		</div>
	{/if}
	{#if visual.length || pending}
		<div class="grid" data-nav-row>
			{#each visual as p, i (p.key)}<div class="cell {sizeOf(p, i)}"><PagePanel panel={p} size={sizeOf(p, i)} {...meta(p.href)} /></div>{/each}
			{#if pending && visual.length < 3}
				{#each Array(3 - visual.length) as _, i (i)}<div class="cell medium"><div class="skeleton" aria-hidden="true"></div></div>{/each}
			{/if}
		</div>
	{/if}
	{#if rail.length}
		<h3 class="rail-head">Also waiting</h3>
		<div class="rail" data-nav-row>
			{#each rail as p (p.key)}<PagePanel panel={p} size="compact" {...meta(p.href)} />{/each}
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
	.urgent-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 22rem), 1fr)); gap: .9rem; margin-bottom: .9rem; }
	.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 18rem), 1fr)); grid-auto-flow: row dense; gap: clamp(.75rem, 1.6vw, 1.15rem); }
	.cell { display: grid; min-width: 0; }
	.cell.wide { grid-column: span 2; }
	@media (max-width: 680px) { .cell.wide { grid-column: auto; } }
	.skeleton { min-height: 16rem; border-radius: var(--r-lg); background: linear-gradient(100deg, var(--bg-2) 30%, var(--surface-2) 50%, var(--bg-2) 70%) 0 0 / 300% 100%; animation: shimmer 1.4s linear infinite; }
	@keyframes shimmer { to { background-position: -150% 0; } }
	@media (prefers-reduced-motion: reduce) { .skeleton { animation: none; } }
	.rail-head { margin: clamp(1.4rem, 3vw, 2rem) 0 .8rem; color: var(--ink-soft); font: 500 var(--fs-md) / 1.3 var(--font-display); }
	.rail { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 17rem), 1fr)); gap: clamp(.7rem, 1.6vw, 1.1rem); }
	.quiet { margin: 1rem 0 0; color: var(--faint); font-size: var(--fs-sm); }
	.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
</style>
