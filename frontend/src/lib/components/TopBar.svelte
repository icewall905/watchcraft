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

	let picker = $state(false);

</script>

<header class="topbar glass" data-nav-noscroll data-nav-skip-initial>
	{#if deviceMode.mode === 'tv'}
	<a class="logo" href="/" aria-label="WatchCraft home" tabindex="-1" data-nav-off>
		<img src="/favicon.svg" alt="" width="38" height="38" /><span class="wordmark">WatchCraft</span>
	</a>
	{/if}

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
 .topbar { position: sticky; top: 0; z-index: 100; height: var(--topbar-h); display: flex; align-items: center; justify-content: space-between; gap: .75rem; padding: 0 var(--gutter); border-width: 0 0 1px; background: var(--bg-1); }
 .logo, .right, .who { display: flex; align-items: center; gap: .6rem; }
 .right { margin-left: auto; }
 .logo img { width: 2.4rem; height: 2.4rem; }
 .wordmark { font-family: Georgia, serif; font-weight: 800; color: #edcd86; letter-spacing: -.04em; }
 .pause { height: 2.6rem; min-height: 2.6rem; padding: 0 .95rem; font-size: .88rem; }
 .pause.paused { background: var(--accent); color: var(--accent-ink); }
 .who { padding: .2rem .85rem .2rem .2rem; border-radius: var(--r-pill); border: 1px solid transparent; background: transparent; cursor: pointer; }
 .who:hover { background: var(--surface-2); }
 .wn { font-weight: 650; font-size: .9rem; }
 @media (max-width: 760px) { .wn, .pl, .wordmark { display: none; } .pause { width: 2.6rem; padding: 0; } .who { padding: .2rem; } }
</style>
