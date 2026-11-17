<script lang="ts">
	import { focusable } from '$lib/nav';
	import { jellyfin, type Status } from './api';

	let { onLinked }: { onLinked: (s: Status) => void } = $props();
	let username = $state('');
	let password = $state('');
	let busy = $state(false);
	let error = $state('');

	async function submit(e: Event) {
		e.preventDefault();
		if (busy || !username.trim()) return;
		busy = true;
		error = '';
		try {
			onLinked(await jellyfin.link(username.trim(), password));
			password = '';
		} catch (err) {
			error = (err as Error).message;
		} finally {
			busy = false;
		}
	}
</script>

<form class="link" onsubmit={submit}>
	<label>Jellyfin user name
		<input autocomplete="username" bind:value={username} use:focusable={{ group: 'jf-link' }} />
	</label>
	<label>Password
		<input type="password" autocomplete="current-password" bind:value={password} use:focusable={{ group: 'jf-link-pw' }} />
	</label>
	{#if error}<p class="err" role="alert">{error}</p>{/if}
	<button class="btn primary" type="submit" disabled={busy || !username.trim()} use:focusable={{ group: 'jf-link-go' }}>
		{busy ? 'Signing in…' : 'Sign in to Jellyfin'}
	</button>
	<p class="small muted">WatchCraft keeps only the sign-in token on the server, never your password. Watched state and resume points are your Jellyfin user's.</p>
</form>

<style>
	.link { display: grid; gap: 0.8rem; max-width: 26rem; }
	label { display: grid; gap: 0.3rem; }
	.err { color: var(--bad); margin: 0; }
</style>
