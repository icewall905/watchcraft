<script lang="ts">
	import { session } from '$lib/stores/session.svelte';
	import { focusable } from '$lib/nav';
	import { inAndroidApp } from '$lib/navigation';
	import { watcher, type WatchUpdate } from '$lib/hub/watcher';

	let configured = $state<boolean | null>(null);
	let pairingLink = $state('');
	let tests = $state<WatchUpdate[]>([]);
	let busy = $state(false);
	let message = $state('');
	let error = $state('');
	const nativeSupport = () => typeof navigator !== 'undefined' && navigator.userAgent.includes('WatcherNtfy/2');
	const identity = () => session.grownUp && session.profileId && !session.choosing && !session.needsLogin
		? `${session.profileId}:${session.version}` : '';

	$effect(() => {
		const key = identity();
		configured = null;
		pairingLink = ''; tests = []; busy = false; message = ''; error = '';
		if (!key) return;
		const controller = new AbortController();
		void watcher.updates(true, controller.signal, 'test').then((updates) => {
			if (!controller.signal.aborted && key === identity()) tests = updates;
		}).catch(() => {});
		if (nativeSupport()) void watcher.androidStatus().then((status) => {
			if (!controller.signal.aborted && key === identity()) configured = status.configured;
		}).catch((e: Error) => {
			if (!controller.signal.aborted && key === identity()) error = e.message;
		});
		return () => controller.abort();
	});

	async function action(run: (key: string) => Promise<void>) {
		const key = identity();
		if (!key || busy) return;
		busy = true; error = ''; message = '';
		try { await run(key); }
		catch (e) { if (key === identity()) error = (e as Error).message; }
		finally { if (key === identity()) busy = false; }
	}

	const prepare = () => action(async (key) => {
		const config = await watcher.pairAndroid();
		if (key !== identity()) return;
		const query = new URLSearchParams({ endpoint: config.endpoint, since: String(config.since) });
		pairingLink = `watchcraft-watcher://enable?${query}`;
	});

	const revoke = () => action(async (key) => {
		await watcher.revokeAndroid();
		if (key !== identity()) return;
		pairingLink = '';
		message = 'Devices disconnected. Disable the listener on each device to remove its status notification.';
	});

	const sendTest = () => action(async (key) => {
		const update = await watcher.testNotification();
		if (key !== identity()) return;
		tests = [update, ...tests];
		message = 'Test notification sent. Check the Front Page banner and any paired Android device.';
	});

	const markRead = (id: number) => action(async (key) => {
		await watcher.read(id);
		if (key === identity()) tests = tests.filter((test) => test.id !== id);
	});
</script>

<section aria-labelledby="watcher-notifications-heading">
	<h2 id="watcher-notifications-heading">Watcher notifications</h2>
	{#if nativeSupport()}
		{#if configured && location.protocol === 'https:'}
			<div class="actions">
				{#if pairingLink}
					<a class="btn primary" href={pairingLink} data-sveltekit-reload use:focusable>Open connection in Android app</a>
				{:else}
					<button class="btn" disabled={busy} onclick={prepare} use:focusable>Set up background notifications</button>
				{/if}
				<a class="btn" href="watchcraft-watcher://disable" data-sveltekit-reload use:focusable>Disable on this device</a>
				<button class="btn" disabled={busy} onclick={revoke} use:focusable>Disconnect all Android devices</button>
			</div>
		{:else if configured !== null}
			<p>Android background notifications need an ntfy-enabled Hub over HTTPS.</p>
		{/if}
	{:else if inAndroidApp()}
		<p>Update the Android app to manage background pairing here.</p>
	{:else}
		<p>Open these settings in the Android app to pair this device.</p>
	{/if}

	<div class="actions">
		<button class="btn" disabled={busy} onclick={sendTest} use:focusable>Send test notification</button>
	</div>
	{#if message}<p role="status">{message}</p>{/if}
	{#if error}<p role="alert">{error}</p>{/if}
	{#each tests as test (test.id)}
		<div class="test">
			<span>{test.text}</span>
			<button class="btn" disabled={busy} onclick={() => markRead(test.id)} use:focusable>Mark test as read</button>
		</div>
	{/each}
</section>

<style>
	.actions, .test { display: flex; flex-wrap: wrap; align-items: center; gap: .5rem; margin-block: .75rem; }
	.test { justify-content: space-between; }
</style>
