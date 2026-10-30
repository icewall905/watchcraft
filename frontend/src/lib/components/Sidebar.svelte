<script lang="ts">
	import { onMount, tick } from 'svelte';
	import { page } from '$app/state';
	import { nav, focusable, navScope } from '$lib/nav';
	import { deviceMode } from '$lib/stores/deviceMode.svelte';
	import Icon from './Icon.svelte';

	const tabs = [
		{ href: '/', label: 'Home', icon: 'home' },
		{ href: '/search', label: 'Search', icon: 'search' },
		{ href: '/subscriptions', label: 'Subscriptions', icon: 'subs' },
		{ href: '/discover', label: 'Discover', icon: 'search' },
		{ href: '/stats', label: 'Stats', icon: 'chart' },
		{ href: '/settings', label: 'Settings', icon: 'settings' }
	];
	let open = $state(false);
	let panel = $state<HTMLDivElement>();
	const active = (href: string) => href === '/' ? page.url.pathname === '/' || page.url.pathname.startsWith('/category') : page.url.pathname.startsWith(href);
	function close() { open = false; }
	async function reveal() {
		open = true;
		await tick();
		const link = panel?.querySelector<HTMLElement>('[aria-current="page"]') ?? panel?.querySelector<HTMLElement>('nav a');
		if (link) nav.focus(link, { scroll: false });
	}
	onMount(() => nav.handleKeys((key) => {
		if (deviceMode.mode !== 'tv') return;
		if (open) {
			if (nav.scopeRoot === panel && key === 'right') { close(); return true; }
			return;
		}
		if (key !== 'left' || nav.scopeRoot) return;
		// Only reveal at the left edge: carousel navigation keeps working normally.
		if (!nav.move('left')) void reveal();
		return true;
	}));
</script>

{#snippet menu()}
	<div class="brand"><img src="/favicon.svg" alt="" width="40" height="40" /><span>WatchCraft</span></div>
	<nav aria-label="Main" data-nav-zone="main-navigation" data-nav-skip-initial>
		{#each tabs as tab}
			<a href={tab.href} aria-label={tab.label} aria-current={active(tab.href) ? 'page' : undefined}
				class:active={active(tab.href)} onclick={() => close()}
				use:focusable={{ group: 'sidebar', id: `sidebar-${tab.label}`, scroll: 'nearest' }}>
				<Icon name={tab.icon} /><span>{tab.label}</span>
			</a>
		{/each}
	</nav>
{/snippet}

{#if deviceMode.mode === 'desktop'}
	<aside class="sidebar desktop" data-nav-noscroll>{@render menu()}</aside>
{:else}
	<button data-nav-skip-initial class="menu-toggle btn" aria-label="Open navigation" aria-expanded={open} aria-controls="tv-navigation" onclick={reveal}
		use:focusable={{ group: 'shell', id: 'open-navigation', scroll: 'none' }}>☰</button>
	{#if open}
		<div class="overlay" bind:this={panel} use:navScope={{ onBack: () => close() }}>
			<button class="backdrop" aria-label="Close navigation" tabindex="-1" onclick={() => close()}></button>
			<aside id="tv-navigation" class="sidebar tv" aria-label="Navigation drawer">
				{@render menu()}
				<button class="btn close" use:focusable={{ group: 'sidebar', onSelect: () => close() }}>Close menu →</button>
				<p class="small muted">Press Right or Back to close</p>
			</aside>
		</div>
	{/if}
{/if}

<style>
	.sidebar { position: fixed; inset: 0 auto 0 0; width: 14rem; padding: 1.25rem .85rem; background: var(--bg-1); border-right: 1px solid var(--line); z-index: 110; overflow-y: auto; }
	.brand { display: flex; align-items: center; gap: .6rem; margin-bottom: 2rem; color: var(--accent); font-family: Georgia, serif; font-weight: 800; font-size: 1.2rem; }
	nav { display: grid; gap: .55rem; }
	nav a { display: flex; align-items: center; gap: .85rem; padding: .85rem; border-radius: var(--r-md); color: var(--muted); font-weight: 600; }
	nav a:hover, nav a.active { color: var(--text); background: var(--surface-2); }
	nav a.active { box-shadow: inset 3px 0 var(--accent); }
	.overlay { position: fixed; inset: 0; z-index: 200; }
	.backdrop { position: absolute; inset: 0; width: 100%; border: 0; background: rgba(0,0,0,.6); }
	.tv { width: 18rem; padding: 2rem 1.25rem; }
	.close { margin-top: 2rem; width: 100%; }
	/* Remote focus styling normally makes controls relative; this edge control must stay fixed. */
	:global(html[data-modality='keys']) .menu-toggle:focus { position: fixed; z-index: 105; }
	.menu-toggle { position: fixed; left: .65rem; bottom: .65rem; z-index: 105; min-width: 3rem; }
	@media (max-width: 760px) { .desktop { width: 4.5rem; padding: 1rem .45rem; } .desktop .brand { justify-content: center; } .desktop .brand span, .desktop nav a span { display: none; } .desktop nav a { justify-content: center; padding: .9rem .4rem; } }
</style>
