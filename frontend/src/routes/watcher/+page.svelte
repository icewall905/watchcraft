<script lang="ts">
	import { onMount } from 'svelte';
	import { session } from '$lib/stores/session.svelte';
	import { focusable } from '$lib/nav';
	import { intervals, isSearch, modes, watcher, type Mode, type Plan, type Watch, type WatchUpdate } from '$lib/hub/watcher';

	let goal = $state('');
	let plan = $state<Plan>({ mode: 'page_watch', source: '', selector: '', terms: [] });
	let terms = $state('');
	let interval = $state<number | 'custom'>(60);
	let customInterval = $state<number | undefined>(30);
	let question = $state('');
	let error = $state('');
	let busy = $state(false);
	let showPlan = $state(false);
	let watches = $state<Watch[]>([]);
	let updates = $state<WatchUpdate[]>([]);
	let androidConfigured = $state(false);
	let androidMessage = $state('');
	let pendingRemoval = $state<number | null>(null);
	const supportsAndroidPush = () => typeof navigator !== 'undefined' && navigator.userAgent.includes('WatcherNtfy/1');
	let pairingLink = $state('');
	const active = () => session.ready && !session.needsLogin && !session.choosing && !!session.profileId && !session.profile?.pin_locked;
	const identity = () => active() ? `${session.profileId}:${session.version}` : '';

	async function load(key: string) {
		try {
			const [list, events] = await Promise.all([watcher.list(), watcher.updates()]);
			if (key === identity()) { watches = list; updates = events; }
		} catch (e) {
			if (key === identity()) error = (e as Error).message;
		}
	}

	$effect(() => {
		const key = identity();
		watches = []; updates = []; goal = ''; terms = ''; question = ''; error = ''; busy = false;
		showPlan = false; pendingRemoval = null; pairingLink = ''; androidMessage = ''; androidConfigured = false;
		plan = { mode: 'page_watch', source: '', selector: '', terms: [] };
		if (key) {
			void load(key);
			if (supportsAndroidPush()) void watcher.androidStatus().then((status) => {
				if (key === identity()) androidConfigured = status.configured;
			}).catch(() => {});
		}
	});

	onMount(() => {
		const timer = window.setInterval(() => {
			const key = identity();
			if (key && document.visibilityState === 'visible') void load(key);
		}, 5000);
		return () => window.clearInterval(timer);
	});

	async function action(fn: () => Promise<void>) {
		const key = identity();
		if (busy || !key) return;
		busy = true; error = '';
		try { await fn(); }
		catch (e) { if (key === identity()) error = (e as Error).message; }
		finally { if (key === identity()) busy = false; }
	}

	function suggest() {
		const key = identity();
		const description = goal.trim();
		showPlan = true;
		question = '';
		void action(async () => {
			const result = await watcher.draft(description);
			if (key !== identity() || description !== goal.trim()) return;
			question = result.question ?? '';
			if (result.plan) {
				plan = result.plan;
				terms = result.plan.terms.join('\n');
			} else {
				plan = { mode: 'page_watch', source: '', selector: '', terms: [] };
				terms = '';
			}
		});
	}

	function save(event: SubmitEvent) {
		event.preventDefault();
		const key = identity();
		const minutes = interval === 'custom' ? customInterval : interval;
		if (minutes === undefined) return;
		void action(async () => {
			await watcher.create(goal.trim(), { ...plan, terms: plan.mode === 'page_watch' ? [] : terms.split('\n').map((term) => term.trim()).filter(Boolean) }, minutes);
			if (key !== identity()) return;
			goal = ''; terms = ''; question = ''; showPlan = false;
			plan = { mode: 'page_watch', source: '', selector: '', terms: [] };
			await load(key);
		});
	}

	function preparePairing() {
		const key = identity();
		void action(async () => {
			const config = await watcher.pairAndroid();
			if (key !== identity()) return;
			const query = new URLSearchParams({ endpoint: config.endpoint, since: String(config.since) });
			pairingLink = `watchcraft-watcher://enable?${query}`;
			androidMessage = '';
		});
	}

	function revokePairing() {
		const key = identity();
		void action(async () => {
			await watcher.revokeAndroid();
			if (key === identity()) {
				pairingLink = '';
				androidMessage = 'This profile’s background connection was revoked. Disable the listener on each device to remove its status notification.';
			}
		});
	}

	function change(fn: () => Promise<unknown>) {
		const key = identity();
		void action(async () => { await fn(); if (key === identity()) { pendingRemoval = null; await load(key); } });
	}

	const formatTime = (value: number) => new Date(value * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
</script>

<svelte:head><title>The Watcher · The Hub</title></svelte:head>

<div class="page watcher-page" data-area="hub">
	<header>
		<h1>The Watcher</h1>
		<p>Set up a recurring check and get an update when it finds what you’re looking for.</p>
	</header>

	<form class="panel" onsubmit={save}>
		<label for="watch-goal">What should I watch for?</label>
		<textarea id="watch-goal" bind:value={goal} maxlength="1000" rows="3" required
			placeholder="Tell me when Qwen 4 27B or Qwen 4 Flash is released." use:focusable></textarea>
		<label for="watch-interval">Check every</label>
		<select id="watch-interval" bind:value={interval} use:focusable>
			{#each Object.entries(intervals) as [value, label]}<option value={Number(value)}>{label}</option>{/each}
			<option value="custom">Custom</option>
		</select>
		{#if interval === 'custom'}
			<label for="watch-custom-interval">Minutes between checks</label>
			<input id="watch-custom-interval" type="number" min="1" max="525600" bind:value={customInterval} required use:focusable />
		{/if}
		<div class="actions">
			<button class="btn" type="button" onclick={suggest} disabled={busy || goal.trim().length < 3} use:focusable>Suggest a check</button>
			{#if !showPlan}<button class="btn" type="button" onclick={() => (showPlan = true)} use:focusable>Set up manually</button>{/if}
		</div>
		{#if question}<p role="status">{question}</p>{/if}
		{#if showPlan}
			<label for="watch-mode">Check type</label>
			<select id="watch-mode" bind:value={plan.mode} use:focusable>
				{#each Object.entries(modes) as [value, label]}<option value={value as Mode}>{label}</option>{/each}
			</select>
			<label for="watch-source">{isSearch(plan.mode) ? 'Search query' : 'Public HTTPS URL'}</label>
			<input id="watch-source" type="text" bind:value={plan.source} maxlength="2000" required use:focusable />
			{#if plan.mode === 'page_watch'}
				<label for="watch-selector">Status CSS selector (optional)</label>
				<input id="watch-selector" bind:value={plan.selector} maxlength="300" use:focusable />
				<small>For parcels, use a carrier page that exposes the status in its HTML. The first check records the current text.</small>
			{:else}
				<label for="watch-terms">{isSearch(plan.mode) ? 'Conditions, one per line' : 'Title filters, one per line (optional)'}</label>
				<textarea id="watch-terms" bind:value={terms} rows="3" required={isSearch(plan.mode)} use:focusable></textarea>
			{/if}
			<button class="btn primary" type="submit" disabled={busy} use:focusable>Start watching</button>
		{/if}
	</form>
	{#if error}<p class="error" role="alert">{error}</p>{/if}

	{#if supportsAndroidPush()}
		<section class="panel">
			<h2>Android notifications</h2>
			{#if androidConfigured && typeof location !== 'undefined' && location.protocol === 'https:'}
				<div class="actions">
					{#if pairingLink}
						<a class="btn primary" href={pairingLink} data-sveltekit-reload use:focusable>Open connection in Android app</a>
					{:else}
						<button class="btn" disabled={busy} onclick={preparePairing} use:focusable>Set up background notifications</button>
					{/if}
					<a class="btn" href="watchcraft-watcher://disable" data-sveltekit-reload use:focusable>Disable on this device</a>
					<button class="btn" disabled={busy} onclick={revokePairing} use:focusable>Disconnect all Android devices</button>
				</div>
			{:else}<p>Android notifications need an ntfy-enabled Hub over HTTPS.</p>{/if}
			{#if androidMessage}<p role="status">{androidMessage}</p>{/if}
		</section>
	{:else if typeof navigator !== 'undefined' && navigator.userAgent.includes('WatcherPush/1')}
		<p>Update the Android app to set up Watcher background notifications.</p>
	{/if}

	<section aria-labelledby="watch-updates">
		<h2 id="watch-updates">Updates</h2>
		<button class="btn" disabled={busy} onclick={() => change(watcher.testNotification)} use:focusable>Send test notification</button>
		{#if !updates.length}<p>No updates yet.</p>{/if}
		{#each updates as update (update.id)}
			<article class="panel update" id={`update-${update.id}`}>
				<h3>{update.watch_id === 0 ? 'Test notification' : watches.find((watch) => watch.id === update.watch_id)?.goal ?? 'Watch update'}</h3>
				<p>{update.text}</p>
				<small>{formatTime(update.created_at)}</small>
				<div class="actions">
					{#if update.url}<a class="btn" href={update.url} target="_blank" rel="noreferrer" use:focusable>Open source</a>{/if}
					{#if update.unread}<button class="btn" disabled={busy} onclick={() => change(() => watcher.read(update.id))} use:focusable>Mark as read</button>{/if}
				</div>
			</article>
		{/each}
	</section>

	<section>
		<h2>Your watches</h2>
		{#if !watches.length}<p>No watches yet.</p>{/if}
		{#each watches as watch (watch.id)}
			<article class="panel">
				<h3>{watch.goal}</h3>
				<p>{modes[watch.plan.mode]} · {intervals[watch.interval] ?? `${watch.interval} ${watch.interval === 1 ? 'minute' : 'minutes'}`} · {watch.completed ? 'Completed' : watch.enabled ? 'Active' : 'Paused'}</p>
				<small>{watch.last_check === null ? 'Waiting for first check' : `Last checked ${formatTime(watch.last_check)}`}</small>
				{#if watch.enabled && watch.last_check !== null}<small>Next check around {formatTime(watch.next_check)}</small>{/if}
				{#if watch.error}<p class="error">{watch.error}</p>{/if}
				<div class="actions">
					{#if pendingRemoval === watch.id}
						<button class="btn" disabled={busy} onclick={() => change(() => watcher.remove(watch.id))} use:focusable>Delete watch and updates</button>
						<button class="btn" onclick={() => (pendingRemoval = null)} use:focusable>Cancel</button>
					{:else}
						{#if !watch.completed}<button class="btn" disabled={busy} onclick={() => change(() => watcher.enable(watch.id, !watch.enabled))} use:focusable>{watch.enabled ? 'Pause' : 'Resume'}</button>{/if}
						<button class="btn" disabled={busy} onclick={() => (pendingRemoval = watch.id)} use:focusable>Delete</button>
					{/if}
				</div>
			</article>
		{/each}
	</section>
</div>

<style>
	.watcher-page { max-width: 56rem; margin-inline: auto; display: grid; gap: 1.5rem; }
	.panel { display: grid; gap: .75rem; padding: 1rem; margin-block: .65rem; border: 1px solid var(--line, #49415c); border-radius: 12px; }
	h1, h2, h3, p { margin: 0; }
	form :is(input, textarea, select) { width: 100%; padding: .65rem; font: inherit; color: inherit; background: var(--bg, #18132b); border: 1px solid var(--line, #49415c); border-radius: 6px; }
	.actions { display: flex; flex-wrap: wrap; gap: .5rem; }
	.btn { justify-self: start; }
	.error { color: var(--danger, #ff9f9f); }
	.update { overflow-wrap: anywhere; }
</style>
