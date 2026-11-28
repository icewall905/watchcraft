<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { api, ApiError, setCurrentAccountId } from '$lib/api';
	import { deviceLabel } from '$lib/deviceLabel';
	import { focusable, navScope } from '$lib/nav';
	import type { InviteInfo } from '$lib/types';
	import TextField from '$lib/components/form/TextField.svelte';

	const token = $derived(page.params.token ?? '');
	let info = $state<InviteInfo | null>(null);
	let missing = $state(false);
	let username = $state('');
	let password = $state('');
	let confirm = $state('');
	let error = $state('');
	let busy = $state(false);

	onMount(async () => {
		try {
			info = await api.invite(token);
			username = info.username ?? '';
		} catch {
			missing = true;
		}
	});

	const reset = $derived(info?.purpose === 'reset');

	async function submit() {
		if (busy || !info) return;
		error = '';
		if (!info.username && !username.trim()) return void (error = 'Choose a username.');
		if (password.length < 8) return void (error = 'Use at least 8 characters.');
		if (password !== confirm) return void (error = 'The passwords do not match.');
		busy = true;
		try {
			const r = await api.register({ token, password, ...(info.username ? {} : { username: username.trim() }), device_label: deviceLabel() });
			setCurrentAccountId(r.public_id);
			// Full load so the app boots with the new session.
			location.assign('/');
		} catch (e) {
			error = e instanceof ApiError && e.message ? e.message : 'Cannot reach the server. Try again.';
			busy = false;
		}
	}
</script>

<svelte:head><title>Join The Hub</title></svelte:head>
<div class="join" use:navScope>
	{#if missing}
		<div class="card panel">
			<img src="/logo.png" alt="" width="72" height="72" />
			<h1>Link not valid</h1>
			<p class="muted">This invite link is unknown, already used or expired. Ask the administrator for a new one.</p>
			<a class="btn" href="/" use:focusable>Go to The Hub</a>
		</div>
	{:else if info}
		<form class="card panel" onsubmit={(e) => (e.preventDefault(), submit())}>
			<img src="/logo.png" alt="" width="72" height="72" />
			<h1>{reset ? 'Choose a new password' : 'Join The Hub'}</h1>
			<p class="muted">{reset ? `Account ${info.username}` : info.username ? `Create the password for ${info.username}.` : 'Choose a username and password.'}</p>
			{#if !info.username}<TextField label="Username" bind:value={username} autofocus autocomplete="username" name="username" />{/if}
			<TextField type="password" label="Password" bind:value={password} autofocus={!!info.username} autocomplete="new-password" name="password" hint="At least 8 characters." />
			<TextField type="password" label="Repeat password" bind:value={confirm} autocomplete="new-password" onsubmit={submit} />
			{#if error}<p class="err small" role="alert">{error}</p>{/if}
			<button class="btn primary big" type="submit" disabled={busy} use:focusable>{reset ? 'Set password' : 'Create account'}</button>
		</form>
	{:else}
		<div class="skeleton" style="height:12rem;width:min(100%,26rem)"></div>
	{/if}
</div>

<style>
	.join {
		position: fixed;
		inset: 0;
		display: grid;
		place-items: center;
		padding: 1rem;
		overflow: auto;
		background:
			radial-gradient(800px 500px at 50% 10%, rgba(124, 92, 255, 0.3), transparent 70%),
			var(--bg-0);
	}
	.card {
		width: min(100%, 26rem);
		display: grid;
		gap: 1rem;
		text-align: center;
	}
	.card img { justify-self: center; }
	.err { color: var(--bad); }
</style>
