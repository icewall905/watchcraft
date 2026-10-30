<script lang="ts">
	import { session } from '$lib/stores/session.svelte';
	import { focusable, navScope } from '$lib/nav';
	import type { Profile } from '$lib/types';
	import Avatar from './Avatar.svelte';
	import ProfileEditor from './ProfileEditor.svelte';

	let { fullscreen = false, onClose }: { fullscreen?: boolean; onClose: () => void } = $props();
	let editing = $state<Profile | null | undefined>(undefined); // undefined = closed, null = new
	let manage = $state(false);

	async function pick(p: Profile) {
		if (manage) {
			if (!session.isAdmin && p.id !== session.profileId) return;
			if (await session.ensureUnlocked(p.id)) editing = p;
			return;
		}
		if (await session.switchProfile(p.id)) onClose();
	}
</script>

<div class="picker" class:fullscreen role="dialog" aria-label="Who's watching?" use:navScope={{ onBack: fullscreen ? undefined : onClose }}>
	<div class="inner">
		<h1>{manage ? 'Manage profiles' : "Who's watching?"}</h1>
		<div class="people">
			{#each session.profiles as p (p.id)}
				<button
					class="person"
					class:current={p.id === session.profileId}
					use:focusable={{ group: 'people', id: `person-${p.id}`, autofocus: p.id === session.profileId, onSelect: () => pick(p) }}
				>
					<Avatar emoji={p.emoji} color={p.color} size="clamp(5rem, 8vw, 9rem)" />
					<span class="name">{p.name}{#if p.pin_required}<span class="pin-badge" aria-label="PIN protected"> 🔒</span>{/if}</span>
					{#if manage}<span class="edit">✎ Edit</span>{/if}
				</button>
			{/each}
			{#if session.isAdmin}<button class="person add" use:focusable={{ group: 'people', id: 'person-add', onSelect: () => (editing = null) }}>
				<span class="plus">+</span>
				<span class="name">Add person</span>
			</button>{/if}
		</div>
		<div class="hstack actions">
			{#if session.profile?.pin_required && !session.profile?.pin_locked}<button class="btn" use:focusable={{ onSelect: () => session.lockProfile() }}>Lock profile</button>{/if}
			{#if session.isAdmin}<button class="btn" use:focusable={{ id: 'manage', onSelect: () => (manage = !manage) }}>{manage ? 'Done' : 'Manage profiles'}</button>{/if}
			{#if !fullscreen}<button class="btn ghost" use:focusable={{ onSelect: onClose }}>Close</button>{/if}
		</div>
	</div>
</div>

{#if editing !== undefined}
	<ProfileEditor profile={editing} onClose={() => (editing = undefined)} />
{/if}

<style>
	.picker {
		position: fixed;
		inset: 0;
		z-index: 400;
		display: grid;
		place-items: center;
		padding: 2rem var(--gutter);
		background: rgba(8, 6, 20, 0.86);
		backdrop-filter: blur(14px);
		-webkit-backdrop-filter: blur(14px);
		animation: rise 0.3s var(--ease);
		overflow: auto;
	}
	.picker.fullscreen {
		background:
			radial-gradient(900px 500px at 50% 20%, rgba(124, 92, 255, 0.28), transparent 70%),
			var(--bg-0);
	}
	.inner {
		display: grid;
		justify-items: center;
		gap: clamp(1.5rem, 3vw, 3rem);
		text-align: center;
	}
	h1 {
		font-size: clamp(2rem, 3vw + 1rem, 4rem);
	}
	.people {
		display: flex;
		flex-wrap: wrap;
		justify-content: center;
		gap: clamp(1rem, 2.5vw, 3rem);
	}
	.person {
		--focus-scale: 1.1;
		display: grid;
		justify-items: center;
		gap: 0.8rem;
		padding: 1rem;
		border-radius: var(--r-lg);
		background: transparent;
		border: 0;
		cursor: pointer;
		min-width: 8rem;
	}
	.person:hover {
		background: rgba(255, 255, 255, 0.05);
	}
	.person.current .name::after {
		content: ' ✓';
		color: var(--accent);
	}
	.name {
		font-size: 1.15rem;
		font-weight: 650;
	}
	.edit {
		font-size: 0.8rem;
		color: var(--accent);
	}
	.plus {
		display: grid;
		place-items: center;
		width: clamp(5rem, 8vw, 9rem);
		height: clamp(5rem, 8vw, 9rem);
		border-radius: 50%;
		border: 2px dashed var(--line-strong);
		font-size: 2.5rem;
		color: var(--muted);
	}
	.actions {
		justify-content: center;
	}
</style>
