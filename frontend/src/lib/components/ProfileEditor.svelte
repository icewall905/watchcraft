<script lang="ts">
	import { api } from '$lib/api';
	import { session } from '$lib/stores/session.svelte';
	import { toast } from '$lib/stores/toast.svelte';
	import { focusable } from '$lib/nav';
	import type { Profile } from '$lib/types';
	import Modal from './Modal.svelte';
	import TextField from './form/TextField.svelte';
	import EmojiPicker from './EmojiPicker.svelte';
	import ColorPicker from './ColorPicker.svelte';
	import Avatar from './Avatar.svelte';
	import Select from './form/Select.svelte';

	let { profile = null, onClose }: { profile?: Profile | null; onClose: () => void } = $props();
	// svelte-ignore state_referenced_locally
	let name = $state(profile?.name ?? '');
	// svelte-ignore state_referenced_locally
	let emoji = $state(profile?.emoji ?? '😺');
	// svelte-ignore state_referenced_locally
	let color = $state(profile?.color ?? '#ff6b9a');
	// svelte-ignore state_referenced_locally
	let role = $state<Profile['role']>(profile?.role ?? 'adult');
	let confirmDelete = $state(false);
	let busy = $state(false);
	let pin = $state('');
	let repeatPin = $state('');
	let removePin = $state(false);

	async function save() {
		if (!name.trim()) return toast.warn('Give the profile a name first.');
		if (pin && !/^[0-9]{4,8}$/.test(pin)) return toast.warn('Use a PIN with 4 to 8 digits.');
		if (pin && pin !== repeatPin) return toast.warn('The PINs do not match.');
		const pinChange = removePin ? { pin: '' } : pin ? { pin } : {};
		const accessChange = session.isAdmin ? { role } : {};
		busy = true;
		try {
			if (profile) await api.updateProfile(profile.id, { name: name.trim(), emoji, color, ...pinChange, ...accessChange });
			else {
				const p = await api.createProfile({ name: name.trim(), emoji, color, ...pinChange, ...accessChange });
				toast.success(`Added ${p.name}`);
			}
			await session.loadProfiles();
			session.version++;
			onClose();
		} catch {
			// The API client shows validation and authorization errors; keep the editor open.
		} finally {
			busy = false;
		}
	}

	async function remove() {
		if (!profile) return;
		if (!confirmDelete) return (confirmDelete = true);
		await api.deleteProfile(profile.id);
		toast.success(`Removed ${profile.name}`);
		await session.loadProfiles();
		if (session.profileId === profile.id && session.profiles[0]) await session.switchProfile(session.profiles[0].id);
		onClose();
	}
</script>

<Modal title={profile ? `Edit ${profile.name}` : 'New profile'} subtitle="Each person gets their own playlists, history and AI memory." size="md" {onClose}>
	<div class="stack">
		<div class="preview">
			<Avatar {emoji} {color} size="4.5rem" />
			<div class="pname">{name || 'Name'}</div>
		</div>
		<TextField label="Name" bind:value={name} placeholder="e.g. Maja" autofocus id="profile-name" />
		{#if session.isAdmin}
			<Select label="Profile access" bind:value={role} options={[
				{ value: 'admin', label: 'Administrator', hint: 'Manage shared settings and profile access.' },
				{ value: 'adult', label: 'Adult', hint: 'Use Hub content and manage personal playlists and curators.' },
				{ value: 'child', label: 'Child', hint: 'Hub discovery, external sources and research are disabled.' }
			]} />
			{#if role === 'child'}<p class="small muted">Every adult and administrator profile needs a PIN first. Video playlists keep their existing kid-safe checks; this setting does not filter all video playback.</p>{/if}
		{/if}
		<div class="field"><span class="label">Emoji</span><EmojiPicker bind:value={emoji} /></div>
		<div class="field"><span class="label">Colour</span><ColorPicker bind:value={color} /></div>
		{#if !removePin}
			<TextField label={profile?.pin_required ? 'New PIN (optional)' : 'PIN (optional)'} type="password" inputmode="numeric" bind:value={pin} hint={profile?.pin_required ? '4–8 digits. Leave empty to keep the current PIN.' : '4–8 digits. Leave empty for a profile without a PIN.'} group="profile-pin-edit" />
			{#if pin}<TextField label="Repeat PIN" type="password" inputmode="numeric" bind:value={repeatPin} group="profile-pin-edit" />{/if}
		{/if}
		{#if profile?.pin_required}
			<button class="btn" use:focusable={{ group: 'profile-pin-edit', onSelect: () => { removePin = !removePin; pin = ''; repeatPin = ''; } }}>{removePin ? 'Keep current PIN' : 'Remove PIN'}</button>
			{#if removePin}<p class="small muted">This profile will open without a PIN after you save.</p>{/if}
		{/if}
	</div>
	{#snippet footer()}
		{#if profile && session.profiles.length > 1 && session.isAdmin}
			<button class="btn danger" use:focusable={{ onSelect: remove, scroll: 'nearest' }}>{confirmDelete ? 'Press again to delete everything' : 'Delete profile'}</button>
		{/if}
		<span class="spacer"></span>
		<button class="btn" use:focusable={{ onSelect: onClose, scroll: 'nearest' }}>Cancel</button>
		<button class="btn primary" disabled={busy} use:focusable={{ id: 'profile-save', onSelect: save, scroll: 'nearest' }}>{profile ? 'Save' : 'Add profile'}</button>
	{/snippet}
</Modal>

<style>
	.preview {
		display: flex;
		align-items: center;
		gap: 1rem;
	}
	.pname {
		font-size: 1.4rem;
		font-weight: 700;
	}
</style>
