<script lang="ts">
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { session } from '$lib/stores/session.svelte';
	import { focusable } from '$lib/nav';
	import CategoriesTab from '$lib/settings/CategoriesTab.svelte';
	import FeedsTab from '$lib/settings/FeedsTab.svelte';
	import MemoryTab from '$lib/settings/MemoryTab.svelte';
	import PlayerTab from '$lib/settings/PlayerTab.svelte';
	import SponsorBlockTab from '$lib/settings/SponsorBlockTab.svelte';
	import AiTab from '$lib/settings/AiTab.svelte';
	import CookiesTab from '$lib/settings/CookiesTab.svelte';
	import ExtrasTab from '$lib/settings/ExtrasTab.svelte';
	import ProfilesTab from '$lib/settings/ProfilesTab.svelte';
	import ScreensTab from '$lib/settings/ScreensTab.svelte';
	import KeysTab from '$lib/settings/KeysTab.svelte';
	import StorageTab from '$lib/settings/StorageTab.svelte';
	import AppearanceTab from '$lib/settings/AppearanceTab.svelte';
	import SearchTab from '$lib/settings/SearchTab.svelte';
	import CommonRoomTab from '$lib/settings/CommonRoomTab.svelte';
	import MoodSourcesTab from '$lib/settings/MoodSourcesTab.svelte';
	import NotificationsTab from '$lib/settings/NotificationsTab.svelte';
	import CuratorTab from '$lib/settings/CuratorTab.svelte';
	import MemeLanguagesTab from '$lib/settings/MemeLanguagesTab.svelte';
	import JellyfinTab from '$lib/settings/JellyfinTab.svelte';

	// Grouped by what you are doing: what gets collected, how the AI learns, where it may look,
	// how things play, the devices around the house, and who lives here.
	type Tab = { key: string; label: string; icon: string; hint: string };
	const GROUPS: { label: string; tabs: Tab[] }[] = [
		{ label: 'Collections', tabs: [
			{ key: 'categories', label: 'Playlists', icon: '🗂️', hint: 'What the AI looks for' },
			{ key: 'feeds', label: 'Feeds', icon: '▶', hint: 'Local short-video collections' }
		] },
		{ label: 'AI and learning', tabs: [
			{ key: 'ai', label: 'AI model and limits', icon: '✦', hint: 'Model, agent team, pace' },
			{ key: 'curator', label: 'Curator', icon: '✧', hint: 'Learned preferences and nightly selection' },
			{ key: 'memory', label: 'AI memory', icon: '🧠', hint: 'Rules learned from feedback' },
			{ key: 'memes', label: 'Meme Radar', icon: '🌐', hint: 'Languages to show' }
		] },
		{ label: 'Sources and keys', tabs: [
			{ key: 'search', label: 'Web search', icon: '🔎', hint: 'SearXNG or Brave for curators' },
			{ key: 'commonroom', label: 'Common Room', icon: '💬', hint: 'Reddit and Stack Exchange keys' },
			{ key: 'moodsources', label: 'Mood Board sources', icon: '🖼️', hint: 'Image sites, Flickr and Tumblr keys' },
			{ key: 'cookies', label: 'YouTube sign-in', icon: '🍪', hint: 'Optional cookies' }
		] },
		{ label: 'Watching', tabs: [
			{ key: 'player', label: 'Player', icon: '▶', hint: 'Quality, speed, captions' },
			{ key: 'sponsorblock', label: 'SponsorBlock', icon: '⏭', hint: 'Skip sponsors and intros' },
			{ key: 'jellyfin', label: 'Jellyfin', icon: '🎬', hint: 'Your media server in The Cinema' },
			{ key: 'extras', label: 'YouTube extras', icon: '🧩', hint: 'Shorts, dislikes, better titles' },
			{ key: 'storage', label: 'Storage', icon: '💾', hint: 'Videos saved on the server' }
		] },
		{ label: 'Home and devices', tabs: [
			{ key: 'appearance', label: 'Appearance', icon: '🎨', hint: 'Device mode, themes and colours' },
			{ key: 'screens', label: 'Screens', icon: '📺', hint: 'Play on receivers and pairing' },
			{ key: 'notify', label: 'Notifications', icon: '🔔', hint: 'Webhook, Home Assistant, email' },
			{ key: 'keys', label: 'Remote test', icon: '🎮', hint: 'See what buttons send' }
		] },
		{ label: 'Household', tabs: [
			{ key: 'profiles', label: 'Profiles', icon: '👪', hint: 'Family members' }
		] }
	];
	const TABS = GROUPS.flatMap((g) => g.tabs);
	type TabKey = string;
	const tab = $derived((TABS.find((t) => t.key === (page.url.searchParams.get('tab') === 'playlists' ? 'categories' : page.url.searchParams.get('tab')))?.key ?? 'categories') as TabKey);

	function go(k: TabKey) {
		if (k !== tab) goto(`/settings?tab=${k}`, { replaceState: true, keepFocus: true, noScroll: true });
	}
</script>


<div class="page settings workshop-page">
	<nav class="side" aria-label="Settings sections" data-nav-zone="side">
		<h1>Settings</h1>
		{#each GROUPS as g}
			<p class="group">{g.label}</p>
			{#each g.tabs as t}
			<button
				class="tab"
				class:on={tab === t.key}
				aria-current={tab === t.key ? 'page' : undefined}
				use:focusable={{ group: 'set-tabs', id: `set-${t.key}`, autofocus: tab === t.key, scroll: 'nearest', onSelect: () => go(t.key) }}
			>
				<span class="ic">{t.icon}</span>
				<span class="tx"><span class="l">{t.label}</span><span class="h">{t.hint}</span></span>
			</button>
			{/each}
		{/each}
		<a class="tab" href="/welcome" use:focusable={{ group: 'set-tabs', id: 'set-welcome' }}>Setup guide</a>
	</nav>
	<div class="content" data-nav-zone="content">
		{#if !session.settings}
			<div class="skeleton" style="height:20rem"></div>
		{:else}
			{#key tab}
				<div class="pane">
					{#if !session.isAdmin && !['categories', 'feeds', 'profiles', 'keys', 'appearance', ...(session.grownUp ? ['curator'] : [])].includes(tab)}
						<h2>Administrator access required</h2><p class="muted">Switch to an administrator profile to change shared server settings.</p>
					{:else if tab === 'categories'}<CategoriesTab />
					{:else if tab === 'feeds'}<FeedsTab />
					{:else if tab === 'jellyfin'}<JellyfinTab />
					{:else if tab === 'curator'}<CuratorTab />
					{:else if tab === 'memory'}<MemoryTab />
					{:else if tab === 'ai'}<AiTab />
					{:else if tab === 'search'}<SearchTab />
					{:else if tab === 'commonroom'}<CommonRoomTab />
					{:else if tab === 'moodsources'}<MoodSourcesTab />
					{:else if tab === 'memes'}<MemeLanguagesTab />
					{:else if tab === 'notify'}<NotificationsTab />
					{:else if tab === 'player'}<PlayerTab />
					{:else if tab === 'appearance'}<AppearanceTab />
					{:else if tab === 'storage'}<StorageTab />
					{:else if tab === 'sponsorblock'}<SponsorBlockTab />
					{:else if tab === 'extras'}<ExtrasTab />
					{:else if tab === 'cookies'}<CookiesTab />
					{:else if tab === 'profiles'}<ProfilesTab />
					{:else if tab === 'screens'}<ScreensTab />
					{:else}<KeysTab />{/if}
				</div>
			{/key}
		{/if}
	</div>
</div>

<style>
	.settings {
		display: grid;
		grid-template-columns: clamp(15rem, 18vw, 21rem) minmax(0, 1fr);
		gap: clamp(1.5rem, 3vw, 4rem);
		align-items: start;
	}
	.side {
		position: sticky;
		top: calc(var(--topbar-h) + 1.25rem);
		display: grid;
		gap: 0.3rem;
	}
	.side h1 {
		font-size: clamp(1.5rem, 1.5vw + 0.8rem, 2.4rem);
		margin: 0.5rem 0 1rem 0.4rem;
	}
	.group {
		margin: 0.9rem 0 0.15rem 0.8rem;
		font-size: 0.7rem;
		font-weight: 700;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--faint);
	}
	.side h1 + .group {
		margin-top: 0;
	}
	.tab {
		--focus-scale: 1.04;
		display: flex;
		align-items: center;
		gap: 0.8rem;
		padding: 0.65rem 0.8rem;
		border-radius: var(--r-md);
		border: 1px solid transparent;
		background: transparent;
		text-align: left;
		cursor: pointer;
		color: var(--muted);
	}
	.tab:hover {
		background: rgba(255, 255, 255, 0.05);
	}
	.tab.on {
		background: var(--surface-2);
		border-color: var(--line);
		color: var(--text);
	}
	.ic {
		width: 2rem;
		height: 2rem;
		display: grid;
		place-items: center;
		flex: none;
		border-radius: 0.6rem;
		background: rgba(255, 255, 255, 0.06);
		font-size: 1rem;
	}
	.tab.on .ic {
		background: var(--accent);
		color: var(--accent-ink);
	}
	.tx {
		display: grid;
	}
	.l {
		font-weight: 650;
	}
	.h {
		font-size: 0.75rem;
		color: var(--faint);
	}
	.content {
		min-width: 0;
		max-width: 64rem;
		padding-top: 0.6rem;
	}
	.pane {
		animation: rise 0.25s var(--ease);
	}
	@media (max-width: 900px) {
		.settings {
			grid-template-columns: 1fr;
		}
		.side {
			position: static;
			display: flex;
			overflow-x: auto;
			gap: 0.4rem;
			padding-bottom: 0.4rem;
			scrollbar-width: none;
		}
		.side h1 {
			display: none;
		}
		.tab {
			flex: none;
		}
		.h {
			display: none;
		}
		.group {
			flex: none;
			align-self: center;
			margin: 0 0 0 0.6rem;
		}
	}
</style>
