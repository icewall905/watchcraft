<script lang="ts">
	import { session } from '$lib/stores/session.svelte';
	import { focusable } from '$lib/nav';
	import type { Profile } from '$lib/types';
	import Section from './Section.svelte';
	import Avatar from '$lib/components/Avatar.svelte';
	import ProfileEditor from '$lib/components/ProfileEditor.svelte';

	let editing = $state<Profile | null | undefined>(undefined);
	async function edit(profile: Profile) {
		if (await session.ensureUnlocked(profile.id)) editing = profile;
	}
</script>

<Section title="Profiles" intro="Each profile has its own playlists, subscriptions, history and AI memory. Administrators manage profile access. Child profiles disable Hub discovery and research; every adult profile must have a PIN when child profiles are enabled.">
	{#snippet actions()}
		{#if session.isAdmin}<button class="btn primary" use:focusable={{ group: 'pf-top', onSelect: () => (editing = null) }}>＋ Add person</button>{/if}
	{/snippet}
	{#if session.profiles.some(p => p.role === 'admin' && !p.pin_required)}<p class="small muted">An administrator profile without a PIN is open to anyone. Set its PIN to protect shared settings.</p>{/if}
	<div class="list">
		{#each session.profiles as p (p.id)}
			<div class="item">
				<Avatar emoji={p.emoji} color={p.color} size="3rem" />
				<div class="grow">
					<strong>{p.name}{p.pin_required ? ' 🔒' : ''}</strong>
					<span class="small muted">{p.role === 'admin' ? 'Administrator' : p.role === 'child' ? 'Child · Hub disabled' : 'Adult'}</span>
					{#if p.id === session.profileId}<span class="small" style="color:var(--accent)">Watching now</span>{/if}
				</div>
				{#if p.id !== session.profileId}
					<button class="btn mini" use:focusable={{ group: `pf-${p.id}`, onSelect: () => session.switchProfile(p.id) }}>Switch to</button>
				{/if}
				{#if session.isAdmin || p.id === session.profileId}<button class="btn mini" use:focusable={{ group: `pf-${p.id}`, onSelect: () => edit(p) }}>Edit</button>{/if}
			</div>
		{/each}
	</div>
</Section>

{#if editing !== undefined}
	<ProfileEditor profile={editing} onClose={() => (editing = undefined)} />
{/if}

<style>
	.list {
		display: grid;
		gap: 0.5rem;
	}
	.item {
		display: flex;
		align-items: center;
		gap: 1rem;
		padding: 0.7rem 0.9rem;
		border-radius: var(--r-md);
		background: rgba(255, 255, 255, 0.035);
		border: 1px solid var(--line);
	}
	.grow {
		flex: 1;
		display: grid;
	}
	.btn.mini {
		min-height: 2.2rem;
		padding: 0.2rem 0.85rem;
		font-size: 0.85rem;
	}
</style>
