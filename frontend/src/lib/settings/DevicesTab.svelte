<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import { session } from '$lib/stores/session.svelte';
	import { toast } from '$lib/stores/toast.svelte';
	import { focusable } from '$lib/nav';
	import { timeAgo } from '$lib/util';
	import type { AuthDevice } from '$lib/types';
	import Section from './Section.svelte';
	import TextField from '$lib/components/form/TextField.svelte';

	let devices = $state<AuthDevice[]>([]);
	let current = $state('');
	let next = $state('');
	let repeat = $state('');
	let busy = $state(false);

	async function load() {
		try { devices = await api.devices(); } catch { devices = []; }
	}
	onMount(load);

	async function revoke(d: AuthDevice) {
		if (d.current) return void (await session.signOut());
		try {
			await api.revokeDevice(d.public_id);
			toast.success('Signed out');
			await load();
		} catch { /* toast shown */ }
	}
	async function change() {
		if (busy) return;
		if (next.length < 8) return void toast.warn('Use at least 8 characters.');
		if (next !== repeat) return void toast.warn('The passwords do not match.');
		busy = true;
		try {
			await api.changePassword(current, next);
			toast.success('Password changed');
			current = next = repeat = '';
		} catch (e) {
			toast.error(e instanceof Error && e.message ? e.message : 'Could not change the password.');
		} finally { busy = false; }
	}
</script>

<Section title="Devices" intro="Browsers, TVs and apps signed in to {session.account?.username ?? 'your account'}. Sign out any you do not recognise.">
	<div class="list">
		{#each devices as d (d.public_id)}
			<div class="item">
				<div class="grow">
					<strong>{d.device_label || 'Unknown device'}{d.current ? ' (this device)' : ''}</strong>
					<span class="small muted">Last used {timeAgo(d.last_seen_at)}</span>
				</div>
				<button class="btn mini" use:focusable={{ group: `dev-${d.public_id}`, onSelect: () => revoke(d) }}>{d.current ? 'Sign out' : 'Revoke'}</button>
			</div>
		{/each}
	</div>
</Section>

<Section title="Change password">
	<form class="form" onsubmit={(e) => (e.preventDefault(), change())}>
		<TextField type="password" label="Current password" bind:value={current} autocomplete="current-password" />
		<TextField type="password" label="New password" bind:value={next} autocomplete="new-password" hint="At least 8 characters." />
		<TextField type="password" label="Repeat new password" bind:value={repeat} autocomplete="new-password" onsubmit={change} />
		<button class="btn primary" type="submit" disabled={busy} use:focusable>Change password</button>
	</form>
</Section>

<style>
	.list { display: grid; gap: 0.5rem; }
	.item { display: flex; align-items: center; gap: 1rem; padding: 0.7rem 0.9rem; border-radius: var(--r-md); background: rgba(255, 255, 255, 0.035); border: 1px solid var(--line); }
	.grow { flex: 1; display: grid; }
	.form { display: grid; gap: 0.8rem; max-width: 26rem; }
	.btn.mini { min-height: 2.2rem; padding: 0.2rem 0.85rem; font-size: 0.85rem; }
</style>
