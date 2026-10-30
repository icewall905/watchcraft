<script lang="ts">
	import { page } from '$app/state';
	import { agent } from '$lib/stores/agent.svelte';
	import { session } from '$lib/stores/session.svelte';
	import { focusable } from '$lib/nav';
	import StatusPill from './StatusPill.svelte';
	import PlaybackPicker from './PlaybackPicker.svelte';
	import Avatar from './Avatar.svelte';
	import Icon from './Icon.svelte';
	import ProfilePicker from './ProfilePicker.svelte';

	let picker = $state(false);
	const TABS = [
		{ href: '/', label: 'Home', icon: 'home' },
		{ href: '/search', label: 'Search', icon: 'search' },
		{ href: '/subscriptions', label: 'Subscriptions', icon: 'subs' },
		{ href: '/agent', label: 'Video AI', icon: 'agent' },
		{ href: '/discover', label: 'Discover', icon: 'search' },
		{ href: '/stats', label: 'Stats', icon: 'chart' },
		{ href: '/settings', label: 'Settings', icon: 'settings' }
	];
	const path = $derived(page.url.pathname);
	const isActive = (href: string) => (href === '/' ? path === '/' || path.startsWith('/category') : path.startsWith(href));
</script>

<header class="topbar glass" data-nav-noscroll data-nav-skip-initial>
	<a class="logo" href="/" aria-label="WatchCraft home" tabindex="-1" data-nav-off>
		<img src="/favicon.svg" alt="" width="38" height="38" /><span class="wordmark">WatchCraft</span>
	</a>

	<nav class="tabs" aria-label="Main">
		{#each TABS as t}
			<a href={t.href} class="tab" class:active={isActive(t.href)} aria-current={isActive(t.href) ? 'page' : undefined} use:focusable={{ group: 'topbar', id: `tab-${t.icon}`, scroll: 'none' }}>
				<Icon name={t.icon} />
				<span>{t.label}</span>
			</a>
		{/each}
	</nav>

	<div class="right">
		<PlaybackPicker />
		<StatusPill />
		<button
			class="btn pause"
			class:paused={agent.control.paused}
			aria-label={agent.control.paused ? 'Resume AI' : 'Pause AI'}
			use:focusable={{ group: 'topbar', id: 'pause-ai', scroll: 'none', onSelect: () => agent.toggle() }}
		>
			<Icon name={agent.control.paused ? 'play' : 'pause'} size="1.05em" />
			<span class="pl">{agent.control.paused ? 'Resume AI' : 'Pause AI'}</span>
		</button>
		{#if session.profile}
			<button class="who" aria-label="Switch profile ({session.profile.name})" use:focusable={{ group: 'topbar', id: 'profile', scroll: 'none', onSelect: () => (picker = true) }}>
				<Avatar emoji={session.profile.emoji} color={session.profile.color} size="2.5rem" />
				<span class="wn">{session.profile.name}</span>
			</button>
		{/if}
	</div>
</header>

{#if picker}
	<ProfilePicker onClose={() => (picker = false)} />
{/if}

<style>
 .wordmark { font-family: Georgia, serif; font-weight: 800; color: #edcd86; letter-spacing: -.04em; text-shadow: 0 2px #503719; }
 @media(max-width:1500px) { .wordmark { display:none; } }
	.topbar {
		position: sticky;
		top: 0;
		z-index: 100;
		height: var(--topbar-h);
		display: flex;
		align-items: center;
		gap: clamp(0.75rem, 2vw, 2.5rem);
		padding: 0 var(--gutter);
		border-width: 0 0 1px 0;
		background: rgba(12, 10, 28, 0.62);
	}
	.logo {
		display: flex;
		align-items: center;
		gap: 0.6rem;
		flex: none;
	}
	.logo img {
		width: 2.4rem;
		height: 2.4rem;
	}
	.tabs {
		display: flex;
		gap: 0.3rem;
		flex: 1;
		min-width: 0;
	}
	.tab {
		--focus-scale: 1.08;
		display: flex;
		align-items: center;
		gap: 0.5rem;
		padding: 0.55rem 0.95rem;
		border-radius: var(--r-pill);
		color: var(--muted);
		font-weight: 600;
		font-size: 0.95rem;
		white-space: nowrap;
	}
	.tab:hover {
		color: var(--text);
		background: rgba(255, 255, 255, 0.06);
	}
	.tab.active {
		color: var(--text);
		background: rgba(255, 255, 255, 0.09);
	}
	.tab.active :global(.icon) {
		color: var(--accent);
	}
	.right {
		display: flex;
		align-items: center;
		gap: 0.6rem;
		min-width: 0;
	}
	.pause {
		height: 2.6rem;
		min-height: 2.6rem;
		padding: 0 0.95rem;
		font-size: 0.88rem;
	}
	.pause.paused {
		background: var(--accent);
		color: var(--accent-ink);
		border-color: transparent;
	}
	.who {
		--focus-scale: 1.1;
		display: flex;
		align-items: center;
		gap: 0.55rem;
		padding: 0.2rem 0.85rem 0.2rem 0.2rem;
		border-radius: var(--r-pill);
		border: 1px solid transparent;
		background: transparent;
		cursor: pointer;
	}
	.who:hover {
		background: rgba(255, 255, 255, 0.06);
	}
	.wn {
		font-weight: 650;
		font-size: 0.9rem;
	}
	@media (max-width: 1300px) {
		.wn,
		.pl {
			display: none;
		}
		.pause {
			width: 2.6rem;
			padding: 0;
		}
		.who {
			padding: 0.2rem;
		}
	}
	@media (max-width: 1100px) {
		.tab span {
			display: none;
		}
	}
	/* Phones: tabs become a bottom bar. */
	@media (max-width: 760px) {
		/* backdrop-filter would make the fixed bottom bar position relative to the header */
		.topbar {
			backdrop-filter: none;
			-webkit-backdrop-filter: none;
			background: rgba(12, 10, 28, 0.94);
			gap: 0.5rem;
		}
		.tabs {
			position: fixed;
			left: 0;
			right: 0;
			bottom: 0;
			justify-content: space-around;
			padding: 0.4rem 0.5rem calc(0.4rem + env(safe-area-inset-bottom));
			background: rgba(12, 10, 28, 0.88);
			backdrop-filter: var(--blur);
			-webkit-backdrop-filter: var(--blur);
			border-top: 1px solid var(--line);
		}
		.tab {
			flex-direction: column;
			gap: 0.15rem;
			font-size: 0.68rem;
			padding: 0.35rem 0.5rem;
		}
		.tab span {
			display: inline;
		}
		.tab.active {
			background: transparent;
		}
		.right {
			margin-left: auto;
		}
	}
</style>
