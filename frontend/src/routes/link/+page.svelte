<script lang="ts">
	import { page } from '$app/state';
	import { api, ApiError } from '$lib/api';
	import { session } from '$lib/stores/session.svelte';
	import { focusable } from '$lib/nav';
	import TextField from '$lib/components/form/TextField.svelte';

	// svelte-ignore state_referenced_locally
	let code = $state(page.url.searchParams.get('code') ?? '');
	let busy = $state(false);
	let error = $state('');
	let approvedFor = $state('');

	async function approve() {
		const c = code.replace(/[^A-Za-z0-9]/g, '').toUpperCase();
		if (busy || !c) return;
		busy = true;
		error = '';
		try {
			const r = await api.deviceApprove(c);
			approvedFor = r.username;
		} catch (e) {
			error = e instanceof ApiError && e.status === 404 ? 'That code is not valid or has expired.' : e instanceof ApiError && e.message ? e.message : 'Could not approve the code.';
		} finally {
			busy = false;
		}
	}
</script>

<div class="page narrow">
	<h1>Sign in a device</h1>
	{#if session.mode !== 'accounts'}
		<p class="muted">Device codes are only used when accounts are turned on.</p>
	{:else if approvedFor}
		<p data-testid="link-approved">Approved. The device is now signed in as <strong>{approvedFor}</strong>.</p>
		<a class="btn" href="/" use:focusable={{ autofocus: true }}>Done</a>
	{:else}
		<p class="muted">Enter the code shown on the TV or other device. It will be signed in as <strong>{session.account?.username}</strong>.</p>
		<form class="stack" onsubmit={(e) => (e.preventDefault(), approve())}>
			<TextField label="Code" bind:value={code} placeholder="K7QF-2M" autofocus autocomplete="off" onsubmit={approve} />
			{#if error}<p class="err small" role="alert">{error}</p>{/if}
			<button class="btn primary" type="submit" disabled={busy} use:focusable>Approve</button>
		</form>
	{/if}
</div>

<style>
	.narrow { max-width: 30rem; }
	.stack { display: grid; gap: 1rem; }
	.err { color: var(--bad); }
</style>
