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
	let notice = $state('');
	let busy = $state(false);
	let showPlan = $state(false);
	let watches = $state<Watch[]>([]);
	let updates = $state<WatchUpdate[]>([]);
	let pendingRemoval = $state<number | null>(null);
	const active = () => session.ready && !session.needsLogin && !session.choosing && !!session.profileId && !session.profile?.pin_locked;
	const identity = () => active() ? `${session.profileId}:${session.version}` : '';

	async function load(key: string) {
		try {
			const [list, events] = await Promise.all([watcher.list(), watcher.updates(false, undefined, 'watch')]);
			if (key === identity()) { watches = list; updates = events; }
		} catch (e) {
			if (key === identity()) error = (e as Error).message;
		}
	}

	$effect(() => {
		const key = identity();
		watches = []; updates = []; goal = ''; terms = ''; question = ''; error = ''; notice = ''; busy = false;
		showPlan = false; pendingRemoval = null;
		plan = { mode: 'page_watch', source: '', selector: '', terms: [] };
		if (key) void load(key);
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
		busy = true; error = ''; notice = '';
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

	function change(fn: () => Promise<unknown>) {
		const key = identity();
		void action(async () => { await fn(); if (key === identity()) { pendingRemoval = null; await load(key); } });
	}

	function retryWatch(id: number) {
		const key = identity();
		void action(async () => {
			await watcher.retry(id);
			if (key !== identity()) return;
			notice = 'Check queued. Results will appear here when it finishes.';
			await load(key);
		});
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
	{#if notice}<p role="status">{notice}</p>{/if}

	<section aria-labelledby="watch-updates">
		<h2 id="watch-updates">Updates</h2>
		{#if !updates.length}<p>No updates yet.</p>{/if}
		{#each updates as update (update.id)}
			<article class="panel update" id={`update-${update.id}`}>
				<h3>{watches.find((watch) => watch.id === update.watch_id)?.goal ?? 'Watch update'}</h3>
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
				<p>{modes[watch.plan.mode]} · {intervals[watch.interval] ?? `${watch.interval} ${watch.interval === 1 ? 'minute' : 'minutes'}`} · {watch.running ? 'Checking…' : watch.completed ? 'Completed' : watch.enabled ? 'Active' : 'Paused'}</p>
				<small>{watch.last_check === null ? 'Waiting for first check' : `Last checked ${formatTime(watch.last_check)}`}</small>
				{#if watch.enabled && !watch.running && watch.last_check !== null}<small>Next check around {formatTime(watch.next_check)}</small>{/if}
				{#if watch.error}<p class="error">{watch.error}</p>{/if}
				<details class="check-report">
					<summary use:focusable>Last check details</summary>
					<p class="watch-source"><strong>{isSearch(watch.plan.mode) ? 'Search query' : 'Source'}</strong> {watch.plan.source}</p>
					{#if watch.report}
						<p><small>{formatTime(watch.report.checked_at)} · {watch.report.result_count} {watch.report.result_count === 1 ? 'result' : 'results'}</small></p>
						<p>{watch.report.summary}</p>
						{#if watch.report.decisions?.length}
							<ul>
								{#each watch.report.decisions as decision}
									<li>
										<strong>{decision.matched ? 'Matched' : 'Unconfirmed'}</strong> · {decision.condition}
										{#if decision.reason}<p>{decision.reason}</p>{/if}
										{#if decision.url}<a href={decision.url} target="_blank" rel="noreferrer" use:focusable>{decision.title}</a>{/if}
									</li>
								{/each}
							</ul>
						{/if}
						{#if watch.report.samples.length}
							<h4>Source samples</h4>
							<ul>
								{#each watch.report.samples as sample}
									<li>
										{#if sample.url}<a href={sample.url} target="_blank" rel="noreferrer" use:focusable>{sample.title}</a>{:else}{sample.title}{/if}
										{#if sample.snippet}<p>{sample.snippet}</p>{/if}
									</li>
								{/each}
							</ul>
						{/if}
					{:else}
						<p>Details will appear after the next check that reaches the source.</p>
					{/if}
				</details>
				<div class="actions">
					{#if pendingRemoval === watch.id}
						<button class="btn" disabled={busy} onclick={() => change(() => watcher.remove(watch.id))} use:focusable>Delete watch and updates</button>
						<button class="btn" onclick={() => (pendingRemoval = null)} use:focusable>Cancel</button>
					{:else}
						{#if watch.enabled}
							<button class="btn" disabled={busy || watch.running || watch.retry_at > Date.now() / 1000} onclick={() => retryWatch(watch.id)} use:focusable>Retry now</button>
							{#if !watch.running && watch.retry_at > Date.now() / 1000}<small>Retry available at {formatTime(watch.retry_at)}</small>{/if}
						{/if}
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
	.update, .check-report { overflow-wrap: anywhere; }
	.check-report summary { cursor: pointer; padding-block: .45rem; }
	.check-report > p { margin-block: .5rem; }
	.check-report ul { padding-inline-start: 1.25rem; }
	.check-report li + li { margin-top: .5rem; }
	.check-report h4 { margin-bottom: .25rem; }
	.watch-source strong { margin-inline-end: .25rem; }
</style>
