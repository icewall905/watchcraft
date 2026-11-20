<script lang="ts">
	import KidSafeBadge from '$lib/hub/KidSafeBadge.svelte';
	import '@fontsource-variable/inter';
	import '@fontsource-variable/newsreader';
	import '../app.css';
	import '$lib/design/tokens.css';
	import { onMount, type Snippet } from 'svelte';
	import { afterNavigate, beforeNavigate, goto } from '$app/navigation';
	import { page } from '$app/state';
	import { nav, focusable } from '$lib/nav';
	import { session } from '$lib/stores/session.svelte';
	import { agent } from '$lib/stores/agent.svelte';
	import { browserNotify } from '$lib/stores/browserNotify.svelte';
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
	import { inAndroidApp, isHubPath, landingPath } from '$lib/navigation';

	let { children }: { children: Snippet } = $props();
	// SvelteKit keeps a history index in history.state; back only pops entries this app created.
	const histIndex = () => Number((history.state ?? {})['sveltekit:history'] ?? 0);
	let baseIndex = histIndex();
	let watchReturnIndex: number | null = null;
	let navigationStartIndex = baseIndex;

	nav.install();
	// Remote-style keys (arrows move focus, Backspace = Back) only in TV mode unless changed under Appearance.
	$effect(() => nav.setRemote(deviceMode.remote));
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

	// Back/forward scroll memory per history entry. SvelteKit restores scroll right after the page mounts, but feeds
	// such as The Screen load their rows afterwards, so the page is still short and the position clamps to the top.
	// Keep re-applying it while the content grows, until reached, the user scrolls, or a few seconds pass.
	// Keyed by the entry being left; on popstate history.state already names the target, so track it ourselves.
	const scrollMemory = new Map<number, number>();
	let scrollEntry = histIndex();
	let stopScrollRestore: (() => void) | null = null;
	function restoreScroll(target: number) {
		stopScrollRestore?.();
		if (target <= 0) return;
		const until = Date.now() + 10000;
		let frame = 0;
		const stop = () => { cancelAnimationFrame(frame); for (const e of ['wheel', 'touchstart', 'keydown', 'mousedown']) removeEventListener(e, stop, true); stopScrollRestore = null; };
		for (const e of ['wheel', 'touchstart', 'keydown', 'mousedown']) addEventListener(e, stop, { capture: true, passive: true });
		const step = () => {
			if (Math.abs(window.scrollY - target) < 2 || Date.now() > until) return stop();
			if (document.documentElement.scrollHeight - window.innerHeight >= target) window.scrollTo({ top: target, behavior: 'instant' });
			frame = requestAnimationFrame(step);
		};
		stopScrollRestore = stop;
		frame = requestAnimationFrame(step);
	}

	beforeNavigate((n) => {
		navigationStartIndex = histIndex();
		scrollMemory.set(scrollEntry, window.scrollY);
		stopScrollRestore?.();
		if (!n.willUnload) nav.beginNavigation();
	});

	afterNavigate((n) => {
		if (n.type === 'enter') baseIndex = histIndex();
		const fromPath = n.from?.url?.pathname;
		const toPath = n.to?.url?.pathname;
		if (toPath?.startsWith('/watch/') && !fromPath?.startsWith('/watch/')) {
			watchReturnIndex = n.from && histIndex() > navigationStartIndex ? navigationStartIndex : null;
		} else if (!toPath?.startsWith('/watch/')) watchReturnIndex = null;
		scrollEntry = histIndex();
		if (n.type === 'popstate') restoreScroll(scrollMemory.get(scrollEntry) ?? 0);
		const key = page.url.pathname + page.url.search;
		if (n.type === 'goto' && fromPath === toPath) nav.setRouteKey(key);
		else nav.setRoute(key);
	});

	// Public share links (/s/<token>) and invite links (/join/<token>) render alone: no sign-in, profile, agents or app chrome.
	const publicShare = page.url.pathname.startsWith('/s/') || page.url.pathname.startsWith('/join/');

	onMount(() => {
		if (publicShare) return;
		deviceMode.init();
		session.init();
		agent.start();
		const stopNotify = browserNotify.start();
		const stopPlayback = playback.start();
		return () => { stopNotify(); stopPlayback(); };
	});
	$effect(() => {
		const selected = session.settings?.appearance?.theme;
		// Paper is a reading-room theme. Existing video chrome keeps its original palette.
		applyTheme(selected === 'paper' && !hubArea ? 'projector' : selected);
	});
	$effect(() => {
		if (session.ready && !session.needsLogin && session.settings?.setup?.completed === false && page.url.pathname === '/') {
			goto('/welcome', { replaceState: true });
		}
	});
	// Landing page: only when this browser opens at plain `/`; later visits to The Screen stay put.
	let landingPending = !publicShare && page.url.pathname === '/' && !page.url.search;
	$effect(() => {
		if (!landingPending || !session.ready) return;
		if (session.needsLogin || session.choosing) return;
		landingPending = false;
		const target = landingPath(deviceMode.landing);
		if (target === '/' || inAndroidApp() || page.url.pathname !== '/' || session.settings?.setup?.completed === false) return;
		if (!session.canOpen(target)) return;
		goto(target, { replaceState: true });
	});

	// Calm editorial styling (tokens.css [data-area='hub']) applies only to non-video routes.
	const hubArea = $derived(isHubPath(page.url.pathname) || ['/settings', '/stats', '/agent', '/welcome'].some((p) => page.url.pathname === p || page.url.pathname.startsWith(p + '/')));
	const fullscreen = $derived(page.url.pathname.startsWith('/watch/') || page.url.pathname.startsWith('/jellyfin/play/') || page.url.pathname === '/shorts');
</script>

{#if publicShare}
	{@render children()}
{:else if !session.ready}
	<div class="splash"><img src="/logo.png" alt="" width="96" height="96" /></div>
{:else if session.needsLogin}
	<Login />
{:else}
	{#if !session.profile?.pin_locked}
		{#if !fullscreen}<Sidebar /><MobileNav />{/if}
		<div class:desktop={!fullscreen && deviceMode.mode === 'desktop'}>
		{#if !fullscreen}<TopBar />{:else if page.url.pathname.startsWith('/watch/')}<PlaybackPicker floating />{:else if page.url.pathname.startsWith('/jellyfin/play/')}<PlaybackPicker trigger={false} />{/if}
		<main class:fullscreen data-area={hubArea ? 'hub' : undefined}>
			{#if !session.canOpen(page.url.pathname)}
				{#if session.isChild && !isHubPath(page.url.pathname)}
				<div class="page"><h1>Switched off</h1><p>A grown-up has switched this off for your profile. Ask them if you would like to use it.</p><a class="btn primary" href="/tv" use:focusable>Back to The Screen</a></div>
				{:else if session.isChild}
				<div class="page"><h1>Not available on kid profiles</h1><p>This part of The Hub is for grown-ups. Ask a grown-up if you need it.</p><a class="btn primary" href="/today" use:focusable>Back to Today</a></div>
				{:else}
				<div class="page"><h1>Discovery is unavailable for this profile</h1><p>Discovery, external sources and research are disabled. Switch to an adult profile to use the Hub.</p><a class="btn primary" href="/tv" use:focusable>Back to TV</a></div>
				{/if}
			{:else}{#key session.version}
				{#if session.isChild && isHubPath(page.url.pathname)}<div class="kid-safe-bar"><KidSafeBadge /></div>{/if}
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
