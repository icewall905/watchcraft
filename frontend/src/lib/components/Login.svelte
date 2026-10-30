<script lang="ts">
	import { api } from '$lib/api';
	import { session } from '$lib/stores/session.svelte';
	import { focusable, navScope } from '$lib/nav';
	import TextField from './form/TextField.svelte';

	let password = $state('');
	let error = $state('');
	let busy = $state(false);

	async function submit() {
		if (!password) return;
		busy = true;
		error = '';
		try {
			await api.login(password);
			await session.afterLogin();
		} catch {
			error = 'That passphrase is not right. Try again.';
		} finally {
			busy = false;
		}
	}
</script>

<div class="login" use:navScope>
	<form class="card panel" onsubmit={(e) => (e.preventDefault(), submit())}>
		<img src="/favicon.svg" alt="" width="72" height="72" />
		<h1>WatchCraft</h1>
		<p class="muted">Enter the household passphrase to continue.</p>
		<TextField type="password" label="Passphrase" bind:value={password} autofocus onsubmit={submit} />
		{#if error}<p class="err small">{error}</p>{/if}
		<button class="btn primary big" type="submit" disabled={busy} use:focusable>Unlock</button>
	</form>
</div>

<style>
	.login {
		position: fixed;
		inset: 0;
		z-index: 1000;
		display: grid;
		place-items: center;
		padding: 1rem;
		background:
			radial-gradient(800px 500px at 50% 10%, rgba(124, 92, 255, 0.3), transparent 70%),
			var(--bg-0);
	}
	.card {
		width: min(100%, 26rem);
		display: grid;
		gap: 1rem;
		justify-items: stretch;
		text-align: center;
	}
	.card img {
		justify-self: center;
	}
	.err {
		color: var(--bad);
	}
</style>
