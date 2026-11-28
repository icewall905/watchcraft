<script lang="ts">
	import { onDestroy } from 'svelte';
	import { api, ApiError } from '$lib/api';
	import { session } from '$lib/stores/session.svelte';
	import { deviceLabel } from '$lib/deviceLabel';
	import { focusable, navScope } from '$lib/nav';
	import type { DeviceStart } from '$lib/types';
	import TextField from './form/TextField.svelte';

	let username = $state('');
	let password = $state('');
	let repeat = $state('');
	let error = $state('');
	let busy = $state(false);
	let view = $state<'password' | 'code'>('password');
	let device = $state<DeviceStart | null>(null);
	let timer: ReturnType<typeof setInterval> | null = null;

	const bootstrap = $derived(session.bootstrap);
	const canCancel = $derived(session.accounts.length > 0);
	const fullLink = $derived(device ? location.origin + device.link : '');

	async function submit() {
		if (busy || !username.trim() || !password) return;
		if (bootstrap) {
			if (password.length < 8) return void (error = 'Use at least 8 characters.');
			if (password !== repeat) return void (error = 'The passwords do not match.');
		}
		busy = true;
		error = '';
		try {
			const r = bootstrap ? await api.bootstrap(username.trim(), password) : await api.login(username.trim(), password, deviceLabel());
			if (bootstrap) session.bootstrap = false;
			await session.afterLogin(r.public_id);
			password = repeat = '';
		} catch (e) {
			error = e instanceof ApiError && e.status === 429 ? 'Too many attempts. Wait a few minutes and try again.'
				: e instanceof ApiError && e.status === 401 ? 'That username or password is not right.'
				: e instanceof ApiError && e.message && e.status !== 0 ? e.message
				: 'Cannot reach the server. Try again.';
		} finally {
			busy = false;
		}
	}

	function stopPolling() {
		if (timer) clearInterval(timer);
		timer = null;
	}

	async function startCode() {
		view = 'code';
		error = '';
		stopPolling();
		try {
			device = await api.deviceStart(deviceLabel());
		} catch {
			error = 'Could not get a code. Try again.';
			view = 'password';
			return;
		}
		const poll = device.poll;
		timer = setInterval(async () => {
			try {
				const r = await api.devicePoll(poll);
				if (r.status === 'approved') {
					stopPolling();
					await session.afterLogin(r.public_id);
				}
			} catch (e) {
				if (e instanceof ApiError && e.status === 410) {
					stopPolling();
					device = null;
					error = 'That code expired.';
				}
			}
		}, 3000);
	}

	function back() {
		stopPolling();
		device = null;
		view = 'password';
	}

	onDestroy(stopPolling);
</script>

{#snippet hero(title: string, subtitle?: string)}
	<div class="hero">
		<img src="/logo.png" alt="" width="512" height="512" />
		<div class="hero-text">
			<h1>{title}</h1>
			{#if subtitle}<p>{subtitle}</p>{/if}
		</div>
	</div>
{/snippet}

<div class="login" use:navScope={{ onBack: canCancel ? () => (session.adding = false) : undefined }}>
	{#if view === 'code'}
		<div class="card panel">
			{@render hero('Sign in with a code')}
			{#if device}
				<p class="muted">On a phone or computer that is signed in, open this address and enter the code.</p>
				<p class="code" data-testid="device-code">{device.code}</p>
				<p class="link small">{fullLink}</p>
				<p class="muted small">Waiting for approval…</p>
			{:else}
				<p class="muted">Getting a code…</p>
			{/if}
			{#if error}<p class="err small">{error}</p>{/if}
			{#if !device && error}<button class="btn" use:focusable={{ autofocus: true, onSelect: startCode }}>New code</button>{/if}
			<button class="btn ghost" use:focusable={{ autofocus: !!device, onSelect: back }}>Back</button>
		</div>
	{:else}
		<form class="card panel" onsubmit={(e) => (e.preventDefault(), submit())}>
			{@render hero('The Hub', bootstrap ? 'Create the first admin to get started.' : canCancel ? 'Sign in to add another account.' : 'Welcome back. Sign in to continue.')}
			<TextField label="Username" bind:value={username} autofocus autocomplete="username" name="username" />
			<TextField type="password" label="Password" bind:value={password} autocomplete={bootstrap ? 'new-password' : 'current-password'} name="password" onsubmit={bootstrap ? undefined : submit} />
			{#if bootstrap}<TextField type="password" label="Repeat password" bind:value={repeat} autocomplete="new-password" onsubmit={submit} />{/if}
			{#if error}<p class="err small" role="alert">{error}</p>{/if}
			<button class="btn primary big" type="submit" disabled={busy} use:focusable>{bootstrap ? 'Create admin' : 'Sign in'}</button>
			{#if !bootstrap}<button class="btn" type="button" use:focusable={{ onSelect: startCode }}>Sign in with code</button>{/if}
			{#if canCancel}<button class="btn ghost" type="button" use:focusable={{ onSelect: () => (session.adding = false) }}>Cancel</button>{/if}
		</form>
	{/if}
</div>

<style>
	.login {
		position: fixed;
		inset: 0;
		z-index: 1000;
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
		justify-items: stretch;
		text-align: center;
	}
	/* Big logo fading into the card; title and subtitle sit over its lower part. */
	.hero {
		display: grid;
		justify-items: center;
		margin-top: -0.5rem;
		background: radial-gradient(closest-side, rgba(124, 92, 255, 0.28), transparent) top center / clamp(14rem, 42vh, 21rem) clamp(14rem, 42vh, 21rem) no-repeat;
	}
	.hero img {
		width: clamp(11rem, 34vh, 17rem);
		height: auto;
		mask-image: linear-gradient(to bottom, #000 50%, transparent 96%);
		-webkit-mask-image: linear-gradient(to bottom, #000 50%, transparent 96%);
	}
	.hero-text {
		position: relative;
		margin-top: clamp(-5.5rem, -11vh, -3.5rem);
		padding: 0.6rem 1.2rem 0;
		text-shadow: 0 2px 14px var(--bg-0), 0 0 4px var(--bg-0);
	}
	.hero h1 {
		margin: 0;
		font: 600 clamp(2.1rem, 5vh, 2.8rem) / 1.05 var(--font-display, var(--font));
		letter-spacing: -0.02em;
	}
	.hero p {
		margin: 0.45rem 0 0;
		color: var(--text);
		opacity: 0.85;
	}
	.err {
		color: var(--bad);
	}
	.code {
		font-size: clamp(2.5rem, 6vw, 4rem);
		font-weight: 800;
		letter-spacing: 0.15em;
		font-variant-numeric: tabular-nums;
		margin: 0;
	}
	.link {
		word-break: break-all;
		color: var(--accent);
	}
</style>
