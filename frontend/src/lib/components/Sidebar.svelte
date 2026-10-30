<script lang="ts">
	import { onMount, tick } from 'svelte';
	import { page } from '$app/state';
	import { nav, focusable, navScope } from '$lib/nav';
	import { deviceMode } from '$lib/stores/deviceMode.svelte';
	import { hub } from '$lib/hub/api';
	import { session } from '$lib/stores/session.svelte';
	import type { Counts, Collection, Topic } from '$lib/hub/types';
	import { SECTIONS, SETTINGS, TV_DRAWER, isHubPath } from '$lib/navigation';
	import Icon from './Icon.svelte';

	const store = (k: string) => { try { return localStorage.getItem(k); } catch { return null; } };
	const save = (k: string, v: string) => { try { localStorage.setItem(k, v); } catch { /* optional */ } };

	let open = $state(false);
	let panel = $state<HTMLDivElement>();
	let compact = $state(false);
	let collapsed = $state<string[]>([]);
	let counts = $state<Partial<Counts>>({});
	let pinned = $state<{ href: string; label: string; icon: string }[]>([]);

	const path = $derived(page.url.pathname);
	const search = $derived(page.url.search);
	const isTv = $derived(deviceMode.mode === 'tv');
	const sections = $derived(SECTIONS.map(s => ({ ...s, items: s.items.filter(it => session.hubAllowed || !isHubPath(it.href)) })).filter(s => s.items.length));
	const drawer = $derived(TV_DRAWER.filter(it => session.hubAllowed || !isHubPath(it.href)));

	$effect(() => {
		document.documentElement.style.setProperty('--sidebar-width', isTv ? '0rem' : compact ? '4.5rem' : '15rem');
	});

	function toggleCompact() { compact = !compact; save('wc.nav.compact', compact ? '1' : '0'); }
	function toggleGroup(key: string) {
		collapsed = collapsed.includes(key) ? collapsed.filter((k) => k !== key) : [...collapsed, key];
		save('wc.nav.collapsed', JSON.stringify(collapsed));
	}
	function close() { open = false; }
	async function reveal() {
		open = true;
		await tick();
		const link = panel?.querySelector<HTMLElement>('[aria-current="page"]') ?? panel?.querySelector<HTMLElement>('nav a');
		if (link) nav.focus(link, { scroll: false });
	}

	async function loadCounts() {
		if (!session.hubAllowed) { counts = {}; return; }
		const pid = session.profileId;
		try { const result = await hub.counts(); if (session.hubAllowed && session.profileId === pid) counts = result; } catch { /* optional */ }
	}
	async function loadPinned() {
		if (!session.hubAllowed) { pinned = []; return; }
		const pid = session.profileId;
		const out: typeof pinned = [];
		try { (await hub.collections() as Collection[]).filter((c) => c.pinned).forEach((c) => out.push({ href: `/library?collection=${c.id}`, label: c.name, icon: 'collection' })); } catch { /* optional */ }
		try { (await hub.topics() as Topic[]).filter((t) => t.pinned).forEach((t) => out.push({ href: `/knowledge/${t.id}`, label: t.name, icon: 'pin' })); } catch { /* optional */ }
		if (session.hubAllowed && session.profileId === pid) pinned = out;
	}

	$effect(() => { void session.version; void session.profile?.id; void loadCounts(); void loadPinned(); });

	onMount(() => {
		compact = store('wc.nav.compact') === '1';
		try { const c = JSON.parse(store('wc.nav.collapsed') ?? '[]'); if (Array.isArray(c)) collapsed = c.filter((x) => typeof x === 'string'); } catch { /* ignore */ }
		const timer = setInterval(loadCounts, 60_000);
		const off = nav.handleKeys((key) => {
			if (deviceMode.mode !== 'tv') return;
			if (open) {
				if (nav.scopeRoot === panel && key === 'right') { close(); return true; }
				return;
			}
			if (key !== 'left' || nav.scopeRoot) return;
			// Only reveal at the left edge: carousel navigation keeps working normally.
			if (!nav.move('left')) void reveal();
			return true;
		});
		return () => { clearInterval(timer); off?.(); };
	});
</script>

{#snippet badge(n: number | undefined)}
	{#if n}<span class="count" aria-label="{n} new">{n > 99 ? '99+' : n}</span>{/if}
{/snippet}

{#if !isTv}
	<aside class="sidebar desktop" class:compact data-nav-noscroll data-nav-zone="main-navigation" data-nav-side data-nav-skip-initial>
		<a class="brand" href="/tv" aria-label="WatchCraft">
			<img src="/logo.png" alt="" width="36" height="36" />
			{#if !compact}<span class="bt"><b>WatchCraft</b><small>Watching the Internet for you.</small></span>{/if}
		</a>
		<nav aria-label="Main" data-nav-skip-initial>
			{#each sections as sec}
				<section>
					{#if !compact}
						<button class="sec-head" aria-expanded={!collapsed.includes(sec.key)} onclick={() => toggleGroup(sec.key)}>
							<span>{sec.title}</span><Icon name={collapsed.includes(sec.key) ? 'next' : 'down'} size=".8em" />
						</button>
					{:else}<hr />{/if}
					{#if compact || !collapsed.includes(sec.key)}
						{#each sec.items as it}
							{@const on = it.active(path)}
							<a href={it.href} aria-label={it.label} title={it.label} aria-current={on ? 'page' : undefined} class:active={on}
								use:focusable={{ group: 'sidebar', id: `sidebar-${it.label}`, scroll: 'nearest' }}>
								<Icon name={it.icon} />{#if !compact}<span class="lbl">{it.label}</span>{/if}
								{@render badge(it.countKey ? counts[it.countKey] : undefined)}
							</a>
						{/each}
					{/if}
				</section>
			{/each}
			{#if pinned.length && !compact}
				<section>
					<button class="sec-head" aria-expanded={!collapsed.includes('pinned')} onclick={() => toggleGroup('pinned')}>
						<span>Pinned</span><Icon name={collapsed.includes('pinned') ? 'next' : 'down'} size=".8em" />
					</button>
					{#if !collapsed.includes('pinned')}
						{#each pinned as p}
							<a href={p.href} aria-label={p.label} use:focusable={{ group: 'sidebar', id: `sidebar-pin-${p.href}`, scroll: 'nearest' }}>
								<Icon name={p.icon} /><span class="lbl">{p.label}</span>
							</a>
						{/each}
					{/if}
				</section>
			{/if}
		</nav>
		<div class="foot">
			{#each [SETTINGS] as it}
				{@const on = it.active(path)}
				<a href={it.href} aria-label={it.label} title={it.label} aria-current={on ? 'page' : undefined} class:active={on}
					use:focusable={{ group: 'sidebar', id: `sidebar-${it.label}`, scroll: 'nearest' }}>
					<Icon name={it.icon} />{#if !compact}<span class="lbl">{it.label}</span>{/if}
				</a>
			{/each}
			<button class="toggle" aria-label={compact ? 'Expand sidebar' : 'Collapse sidebar'} aria-pressed={compact} title={compact ? 'Expand sidebar' : 'Collapse sidebar'} onclick={toggleCompact}>
				<Icon name="sidebar" />{#if !compact}<span class="lbl">Collapse</span>{/if}
			</button>
		</div>
	</aside>
{:else}
	<button data-nav-skip-initial class="menu-toggle btn" aria-label="Open navigation" aria-expanded={open} aria-controls="tv-navigation" onclick={reveal}
		use:focusable={{ group: 'shell', id: 'open-navigation', scroll: 'none' }}>☰</button>
	{#if open}
		<div class="overlay" bind:this={panel} use:navScope={{ onBack: () => close() }}>
			<button class="backdrop" aria-label="Close navigation" tabindex="-1" onclick={() => close()}></button>
			<aside id="tv-navigation" class="sidebar tv" aria-label="Navigation drawer">
				<div class="brand"><img src="/logo.png" alt="" width="40" height="40" /><span class="bt"><b>WatchCraft</b></span></div>
				<nav aria-label="Main" data-nav-zone="main-navigation" data-nav-skip-initial>
					{#each drawer as it}
						{@const on = it.active(path, search)}
						<a href={it.href} aria-label={it.label} aria-current={on ? 'page' : undefined} class:active={on} onclick={() => close()}
							use:focusable={{ group: 'sidebar', id: `sidebar-${it.label}`, scroll: 'nearest' }}>
							<Icon name={it.icon} /><span class="lbl">{it.label}</span>
						</a>
					{/each}
				</nav>
				<button class="btn close" use:focusable={{ group: 'sidebar', onSelect: () => close() }}>Close menu →</button>
				<p class="small muted">Press Right or Back to close</p>
			</aside>
		</div>
	{/if}
{/if}

<style>
	.sidebar { position: fixed; inset: 0 auto 0 0; width: 15rem; padding: 1.1rem .7rem; background: var(--bg-1); border-right: 1px solid var(--line); z-index: 110; overflow-y: auto; display: flex; flex-direction: column; transition: width .18s var(--ease); }
	.desktop.compact { width: 4.5rem; padding-inline: .45rem; }
	.brand { display: flex; align-items: center; gap: .6rem; margin: 0 .3rem 1.2rem; color: var(--accent); font-family: Georgia, serif; }
	.compact .brand { justify-content: center; margin-inline: 0; }
	.bt { display: grid; line-height: 1.15; }
	.bt b { font-weight: 800; font-size: 1.15rem; }
	.bt small { font: 500 .68rem var(--font); color: var(--muted); letter-spacing: .01em; }
	nav { display: grid; gap: .1rem; flex: 1; align-content: start; }
	section { display: grid; gap: .15rem; margin-bottom: .55rem; }
	hr { border: 0; border-top: 1px solid var(--line); margin: .3rem .4rem; width: calc(100% - .8rem); }
	.sec-head { display: flex; justify-content: space-between; align-items: center; padding: .3rem .7rem; background: none; border: 0; border-radius: var(--r-sm); color: var(--faint); font: 700 .68rem var(--font); letter-spacing: .12em; text-transform: uppercase; cursor: pointer; }
	.sec-head:hover { color: var(--muted); }
	a, .toggle { display: flex; align-items: center; gap: .75rem; padding: .62rem .7rem; border-radius: var(--r-md); color: var(--muted); font-weight: 600; font-size: .92rem; position: relative; }
	.compact a, .compact .toggle { justify-content: center; padding: .75rem .4rem; }
	a:hover, a.active, .toggle:hover { color: var(--text); background: var(--surface-2); }
	a.active { box-shadow: inset 3px 0 var(--accent); }
	a.active :global(.icon) { color: var(--accent); }
	.lbl { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
	.count { min-width: 1.35rem; padding: .05rem .4rem; border-radius: var(--r-pill); background: var(--accent); color: var(--accent-ink); font: 800 .7rem var(--font); text-align: center; }
	.compact .count { position: absolute; top: .2rem; right: .35rem; min-width: 1.1rem; padding: 0 .25rem; font-size: .62rem; }
	.foot { border-top: 1px solid var(--line); padding-top: .5rem; display: grid; gap: .15rem; }
	.toggle { background: none; border: 0; cursor: pointer; font-family: inherit; }
	.toggle:focus-visible, .sec-head:focus-visible, a:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
	.overlay { position: fixed; inset: 0; z-index: 200; }
	.backdrop { position: absolute; inset: 0; width: 100%; border: 0; background: rgba(0,0,0,.6); }
	.tv { width: 18rem; padding: 2rem 1.25rem; }
	.tv nav { flex: none; gap: .55rem; }
	.tv a { padding: .85rem; gap: .85rem; font-size: 1rem; }
	.close { margin-top: 2rem; width: 100%; }
	/* Remote focus styling normally makes controls relative; this edge control must stay fixed. */
	:global(html[data-modality='keys']) .menu-toggle:focus { position: fixed; z-index: 105; }
	.menu-toggle { position: fixed; left: .65rem; bottom: .65rem; z-index: 105; min-width: 3rem; }
	@media (max-width: 760px) { .desktop { display: none; } }
	@media (prefers-reduced-motion: reduce) { .sidebar { transition: none; } }
</style>
