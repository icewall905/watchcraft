<script lang="ts">
	import '@fontsource-variable/inter';
	import '../app.css';
	import { onMount, type Snippet } from 'svelte';
	import { afterNavigate, beforeNavigate, goto } from '$app/navigation';
	import { page } from '$app/state';
	import { nav, focusable } from '$lib/nav';
	import { session } from '$lib/stores/session.svelte';
	import { agent } from '$lib/stores/agent.svelte';
	import { dialogs } from '$lib/stores/dialogs.svelte';
	import Sidebar from '$lib/components/Sidebar.svelte';
	import { deviceMode } from '$lib/stores/deviceMode.svelte';
	import MobileNav from '$lib/components/MobileNav.svelte';
	import CommandPalette from '$lib/components/CommandPalette.svelte';
	import TopBar from '$lib/components/TopBar.svelte';
	import Toasts from '$lib/components/Toasts.svelte';
	import OskOverlay from '$lib/components/OskOverlay.svelte';
	import FeedbackDialog from '$lib/components/FeedbackDialog.svelte';
	import AddToCategory from '$lib/components/AddToCategory.svelte';
	import CandidateDrawer from '$lib/components/CandidateDrawer.svelte';
	import ProfilePicker from '$lib/components/ProfilePicker.svelte';
	import ProfilePin from '$lib/components/ProfilePin.svelte';
	import Login from '$lib/components/Login.svelte';
	import { applyTheme } from '$lib/themes';
	import PlaybackPicker from '$lib/components/PlaybackPicker.svelte';
	import { playback } from '$lib/stores/playback.svelte';
	import { isHubPath } from '$lib/navigation';

	let { children }: { children: Snippet } = $props();
	// SvelteKit keeps a history index in history.state; back only pops entries this app created.
	const histIndex = () => Number((history.state ?? {})['sveltekit:history'] ?? 0);
	let baseIndex = histIndex();
	let watchReturnIndex: number | null = null;
	let navigationStartIndex = baseIndex;

	nav.install();
	nav.backFallback = () => {
		if (page.url.pathname === '/') return;
		if (page.url.pathname.startsWith('/watch/')) {
			if (watchReturnIndex !== null && watchReturnIndex < histIndex()) history.go(watchReturnIndex - histIndex());
			else {
				const category = page.url.searchParams.get('c');
				goto(category && /^\d+$/.test(category) ? `/category/${category}` : '/', { replaceState: true });
			}
			return;
		}
		if (histIndex() > baseIndex) history.back();
		else goto('/', { replaceState: true });
	};

	beforeNavigate((n) => {
		navigationStartIndex = histIndex();
		if (!n.willUnload) nav.beginNavigation();
	});

	afterNavigate((n) => {
		if (n.type === 'enter') baseIndex = histIndex();
		const fromPath = n.from?.url?.pathname;
		const toPath = n.to?.url?.pathname;
		if (toPath?.startsWith('/watch/') && !fromPath?.startsWith('/watch/')) {
			watchReturnIndex = n.from && histIndex() > navigationStartIndex ? navigationStartIndex : null;
		} else if (!toPath?.startsWith('/watch/')) watchReturnIndex = null;
		const key = page.url.pathname + page.url.search;
		if (n.type === 'goto' && fromPath === toPath) nav.setRouteKey(key);
		else nav.setRoute(key);
	});

	onMount(() => {
		deviceMode.init();
		session.init();
		agent.start();
		return playback.start();
	});
	$effect(() => { applyTheme(session.settings?.appearance?.theme); });
	$effect(() => {
		if (session.ready && !session.needsLogin && session.settings?.setup?.completed === false && page.url.pathname === '/') {
			goto('/welcome', { replaceState: true });
		}
	});

	const fullscreen = $derived(page.url.pathname.startsWith('/watch/'));
</script>

{#if !session.ready}
	<div class="splash"><img src="/logo.png" alt="" width="96" height="96" /></div>
{:else if session.needsLogin}
	<Login />
{:else}
	{#if !session.profile?.pin_locked}
		{#if !fullscreen}<Sidebar /><MobileNav />{/if}
		<div class:desktop={!fullscreen && deviceMode.mode === 'desktop'}>
		{#if !fullscreen}<TopBar />{:else}<PlaybackPicker floating />{/if}
		<main class:fullscreen>
			{#if !session.hubAllowed && isHubPath(page.url.pathname)}
				<div class="page"><h1>Hub is unavailable for this profile</h1><p>Discovery, external sources and research are disabled. Switch to an adult profile to use the Hub.</p><a class="btn primary" href="/tv" use:focusable>Back to TV</a></div>
			{:else}{#key session.version}
				{@render children()}
			{/key}{/if}
		</main>
		</div>
	{/if}
	{#if !session.profile?.pin_locked && !fullscreen}<CommandPalette />{/if}
	{#if session.choosing || session.profile?.pin_locked}
		<ProfilePicker fullscreen onClose={() => (session.choosing = false)} />
	{/if}
	{#if !session.profile?.pin_locked}
		{#if dialogs.candidate != null}<CandidateDrawer />{/if}
		{#if dialogs.feedback}<FeedbackDialog />{/if}
		{#if dialogs.addTo}<AddToCategory />{/if}
	{/if}
{/if}
{#if session.unlocking}<ProfilePin profile={session.unlocking} />{/if}
<OskOverlay />
<Toasts />

<style>
	main {
		position: relative;
		z-index: 1;
		padding-bottom: env(safe-area-inset-bottom);
	}
	.desktop { margin-left: var(--sidebar-width, 14rem); }
	@media (max-width: 760px) { .desktop { margin-left: 0; padding-bottom: calc(3.75rem + env(safe-area-inset-bottom)); } }
	.splash {
		position: fixed;
		inset: 0;
		display: grid;
		place-items: center;
	}
	.splash img {
		animation: pulse 1.4s ease-in-out infinite;
	}
</style>
