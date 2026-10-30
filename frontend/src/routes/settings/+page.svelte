<script lang="ts">
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { session } from '$lib/stores/session.svelte';
	import { focusable } from '$lib/nav';
	import CategoriesTab from '$lib/settings/CategoriesTab.svelte';
	import MemoryTab from '$lib/settings/MemoryTab.svelte';
	import PlayerTab from '$lib/settings/PlayerTab.svelte';
	import SponsorBlockTab from '$lib/settings/SponsorBlockTab.svelte';
	import AiTab from '$lib/settings/AiTab.svelte';
	import CookiesTab from '$lib/settings/CookiesTab.svelte';
	import ExtrasTab from '$lib/settings/ExtrasTab.svelte';
	import ProfilesTab from '$lib/settings/ProfilesTab.svelte';
	import KeysTab from '$lib/settings/KeysTab.svelte';
	import StorageTab from '$lib/settings/StorageTab.svelte';
	import AppearanceTab from '$lib/settings/AppearanceTab.svelte';

	const TABS = [
		{ key: 'categories', label: 'Playlists', icon: '🗂️', hint: 'What the AI looks for' },
		{ key: 'memory', label: 'AI memory', icon: '🧠', hint: 'Rules learned from feedback' },
		{ key: 'ai', label: 'AI and limits', icon: '✦', hint: 'Model, curator, pace' },
		{ key: 'player', label: 'Player', icon: '▶', hint: 'Quality, speed, captions' },
		{ key: 'appearance', label: 'Appearance', icon: '🎨', hint: 'Device mode, themes and colours' },
		{ key: 'storage', label: 'Storage', icon: '💾', hint: 'Videos saved on the server' },
		{ key: 'sponsorblock', label: 'SponsorBlock', icon: '⏭', hint: 'Skip sponsors and intros' },
		{ key: 'extras', label: 'Extras', icon: '🧩', hint: 'Shorts, dislikes, titles' },
		{ key: 'cookies', label: 'YouTube cookies', icon: '🍪', hint: 'Optional sign-in' },
		{ key: 'profiles', label: 'Profiles', icon: '👪', hint: 'Family members' },
		{ key: 'keys', label: 'Remote test', icon: '🎮', hint: 'See what buttons send' }
	] as const;
	type TabKey = (typeof TABS)[number]['key'];
	const tab = $derived((TABS.find((t) => t.key === (page.url.searchParams.get('tab') === 'playlists' ? 'categories' : page.url.searchParams.get('tab')))?.key ?? 'categories') as TabKey);

	function go(k: TabKey) {
		if (k !== tab) goto(`/settings?tab=${k}`, { replaceState: true, keepFocus: true, noScroll: true });
	}
</script>


<div class="page settings">
	<nav class="side" aria-label="Settings sections" data-nav-zone="side">
		<h1>Settings</h1>
		{#each TABS as t}
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
		<a class="tab" href="/welcome" use:focusable={{ group: 'set-tabs', id: 'set-welcome' }}>Setup guide</a>
	</nav>
	<div class="content" data-nav-zone="content">
		{#if !session.settings}
			<div class="skeleton" style="height:20rem"></div>
		{:else}
			{#key tab}
				<div class="pane">
					{#if tab === 'categories'}<CategoriesTab />
					{:else if tab === 'memory'}<MemoryTab />
					{:else if tab === 'ai'}<AiTab />
					{:else if tab === 'player'}<PlayerTab />
					{:else if tab === 'appearance'}<AppearanceTab />
					{:else if tab === 'storage'}<StorageTab />
					{:else if tab === 'sponsorblock'}<SponsorBlockTab />
					{:else if tab === 'extras'}<ExtrasTab />
					{:else if tab === 'cookies'}<CookiesTab />
					{:else if tab === 'profiles'}<ProfilesTab />
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
	}
</style>
