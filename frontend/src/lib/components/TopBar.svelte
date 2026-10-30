<script lang="ts">
	import { deviceMode } from '$lib/stores/deviceMode.svelte';
	import { agent } from '$lib/stores/agent.svelte';
	import { session } from '$lib/stores/session.svelte';
	import { focusable } from '$lib/nav';
	import StatusPill from './StatusPill.svelte';
	import PlaybackPicker from './PlaybackPicker.svelte';
	import Avatar from './Avatar.svelte';
	import Icon from './Icon.svelte';
	import ProfilePicker from './ProfilePicker.svelte';
	import { palette } from '$lib/stores/palette.svelte';

	let picker = $state(false);

</script>

<header class="topbar glass" data-nav-noscroll data-nav-skip-initial>
	{#if deviceMode.mode === 'tv'}
	<a class="logo" href="/" aria-label="WatchCraft home" tabindex="-1" data-nav-off>
		<img src="/logo.png" alt="" width="38" height="38" /><span class="wordmark">WatchCraft</span>
	</a>
	{/if}

	{#if deviceMode.mode === 'tv'}
		<a class="searchicon btn" href="/search" aria-label="Search videos" use:focusable={{ group: 'topbar', id: 'video-search', scroll: 'none' }}><Icon name="search" /></a>
	{:else}
		<button class="searchbox" aria-label="Search everything" aria-haspopup="dialog" onclick={() => palette.show()}>
			<Icon name="search" size="1.1em" /><span class="st">Search everything…</span><kbd>Ctrl K</kbd>
		</button>
	{/if}

	<div class="right">
		<PlaybackPicker />
		<StatusPill />
		{#if session.isAdmin}
		<button
			class="btn pause"
			class:paused={agent.control.paused}
			aria-label={agent.control.paused ? 'Resume AI' : 'Pause AI'}
			use:focusable={{ group: 'topbar', id: 'pause-ai', scroll: 'none', onSelect: () => agent.toggle() }}
		>
			<Icon name={agent.control.paused ? 'play' : 'pause'} size="1.05em" />
			<span class="pl">{agent.control.paused ? 'Resume AI' : 'Pause AI'}</span>
		</button>
		{/if}
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
 .topbar { position: sticky; top: 0; z-index: 100; height: var(--topbar-h); display: flex; align-items: center; justify-content: space-between; gap: .75rem; padding: 0 var(--gutter); border-width: 0 0 1px; background: var(--bg-1); }
 .logo, .right, .who { display: flex; align-items: center; gap: .6rem; }
 .right { margin-left: auto; }
 .searchbox { display: flex; align-items: center; gap: .6rem; flex: 0 1 26rem; min-width: 0; height: 2.6rem; padding: 0 .8rem; border: 1px solid var(--line-strong); border-radius: var(--r-pill); background: var(--surface); color: var(--muted); font: 500 .9rem var(--font); cursor: pointer; }
 .searchbox:hover { background: var(--surface-2); color: var(--text); }
 .searchbox:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
 .st { flex: 1; text-align: left; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
 .searchbox kbd { font: 600 .68rem var(--font); padding: .1rem .4rem; border: 1px solid var(--line-strong); border-radius: var(--r-sm); color: var(--faint); }
 .searchicon { width: 2.6rem; min-height: 2.6rem; padding: 0; display: grid; place-items: center; }
 .logo img { width: 2.4rem; height: 2.4rem; }
 .wordmark { font-family: Georgia, serif; font-weight: 800; color: #edcd86; letter-spacing: -.04em; }
 .pause { height: 2.6rem; min-height: 2.6rem; padding: 0 .95rem; font-size: .88rem; }
 .pause.paused { background: var(--accent); color: var(--accent-ink); }
 .who { padding: .2rem .85rem .2rem .2rem; border-radius: var(--r-pill); border: 1px solid transparent; background: transparent; cursor: pointer; }
 .who:hover { background: var(--surface-2); }
 .wn { font-weight: 650; font-size: .9rem; }
 @media (max-width: 760px) { .searchbox kbd { display: none; } .searchbox { flex: 1; } .wn, .pl, .wordmark { display: none; } .pause { width: 2.6rem; padding: 0; } .who { padding: .2rem; } }
</style>
