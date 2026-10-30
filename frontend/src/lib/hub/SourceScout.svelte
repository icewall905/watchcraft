<script lang="ts">
	import { onMount } from 'svelte';
	import { focusable } from '$lib/nav';
	import TextField from '$lib/components/form/TextField.svelte';
	import Select from '$lib/components/form/Select.svelte';
	import { toast } from '$lib/stores/toast.svelte';
	import { session } from '$lib/stores/session.svelte';
	import { hub, ago } from './api';
	import { errMsg } from './ui';
	import type { Curator, ScoutPlan, ScoutRun } from './types';

	let { onAdded }: { onAdded: () => Promise<void> } = $props();
	let query = $state('');
	let runs = $state<ScoutRun[]>([]);
	let curators = $state<Curator[]>([]);
	let selected = $state<number | null>(null);
	let curator = $state('');
	let interval = $state('0');
	let plans = $state<ScoutPlan[]>([]);
	let busy = $state(false);
	let accepting = $state<number | null>(null);
	let error = $state('');
	let lastRefresh = 0;
	const run = $derived(runs.find((r) => r.id === selected) ?? runs[0]);

	async function refresh() {
		lastRefresh = Date.now();
		try { [runs, plans] = await Promise.all([hub.scoutRuns(), hub.scoutPlans()]); }
		catch (e) { error = errMsg(e); }
	}
	onMount(() => {
		void refresh();
		void hub.curators().then((items) => { curators = items; }).catch((e) => { error = errMsg(e); });
		const timer = setInterval(() => { if (busy || runs.some(r => r.status === 'running') || (plans.some(p => p.enabled) && Date.now() - lastRefresh >= 15000)) void refresh(); }, 2500);
		return () => clearInterval(timer);
	});
	async function research(e: SubmitEvent) {
		e.preventDefault();
		if (!query.trim() || busy) return;
		const researchQuery = query.trim();
		const profileId = session.profileId;
		busy = true; error = ''; selected = null;
		try {
			if (Number(interval) > 0) await hub.scoutPlan(researchQuery, Number(interval));
			if (session.profileId !== profileId) return;
			const result = await hub.scoutRun(researchQuery);
			await refresh(); selected = result.id;
		} catch (e) { error = errMsg(e); } finally { busy = false; }
	}
	async function togglePlan(plan: ScoutPlan) {
		try { await hub.scoutPlanUpdate(plan.id, !plan.enabled); await refresh(); }
		catch (e) { error = errMsg(e); }
	}
	async function deletePlan(plan: ScoutPlan) {
		try { await hub.scoutPlanDelete(plan.id); await refresh(); }
		catch (e) { error = errMsg(e); }
	}

	async function accept(index: number) {
		if (!run || accepting !== null) return;
		accepting = index; error = '';
		try {
			await hub.scoutAccept(run.id, index, curator ? Number(curator) : null);
			await onAdded();
			toast.success(curator ? 'Source added to your curator' : 'Source added');
		} catch (e) { error = errMsg(e); } finally { accepting = null; }
	}
	async function remove(id: number) {
		try { await hub.scoutDelete(id); if (selected === id) selected = null; await refresh(); }
		catch (e) { error = errMsg(e); }
	}
</script>

<section class="scout" aria-labelledby="scout-title">
	<div class="intro">
		<div><p class="eyebrow">SOURCE SCOUT</p><h2 id="scout-title">Find places worth following</h2><p class="muted">Give Scout a public website or a topic. It finds and tests feeds or article listings; you decide what to follow.</p></div>
		<span class="mark" aria-hidden="true">✧</span>
	</div>
	<form onsubmit={research} class="research">
		<TextField label="Website or topic" placeholder="https://example.org or open-source AI" bind:value={query} group="scout-form" />
		<button type="submit" class="btn primary" disabled={busy || !query.trim()} use:focusable={{ group: 'scout-form' }}>{busy ? 'Researching…' : 'Find sources'}</button>
	</form>
	<div class="schedule"><Select label="Research again" bind:value={interval} group="scout-form"
		options={[{ value: '0', label: 'Only this time' }, { value: '24', label: 'Daily' }, { value: '168', label: 'Weekly' }]} /></div>
	<p class="small muted">Scheduled research creates suggestions for your approval. Topic searches use your configured web search. Websites must use public HTTPS. Nothing is added without your approval.</p>
	{#if error}<p class="err" role="alert">{error}</p>{/if}
	{#if plans.length}
		<div class="plans"><h3>Scheduled research</h3>
			{#each plans as plan (plan.id)}
				<div class="plan"><span>{plan.query}<small class="muted"> · {plan.enabled ? `Every ${plan.interval_hours}h · next ${ago(plan.next_run)}` : 'Paused'}</small></span>
					<button class="btn ghost small" use:focusable={{ group: 'scout-plans', onSelect: () => togglePlan(plan) }}>{plan.enabled ? 'Pause' : 'Resume'}</button>
					<button class="btn ghost small" aria-label="Remove schedule for {plan.query}" use:focusable={{ group: 'scout-plans', onSelect: () => deletePlan(plan) }}>Remove</button>
				</div>
			{/each}
		</div>
	{/if}
	{#if runs.length}
		<div class="history" aria-label="Research history">
			{#each runs as previous (previous.id)}
				<button class="history-item" class:active={run?.id === previous.id} aria-pressed={run?.id === previous.id}
					use:focusable={{ group: 'scout-history', onSelect: () => (selected = previous.id) }}>
					<span>{previous.query}</span><small>{previous.status} · {ago(previous.created_at)}</small>
				</button>
			{/each}
		</div>
	{/if}
	{#if run}
		<div class="report" aria-live="polite">
			<div class="hstack"><h3>Research: {run.query}</h3><span class="spacer"></span>{#if run.status !== 'running'}<button class="btn ghost small" use:focusable={{ onSelect: () => remove(run.id) }}>Remove from history</button>{/if}</div>
			{#each run.events as event}<p class="event"><span aria-hidden="true">✦</span> {event.message}</p>{/each}
			{#if run.status === 'running'}<p class="muted">Research is in progress…</p>{/if}
			{#if run.error}<p class="err">{run.error}</p>{/if}
			{#if run.status === 'complete' && !run.suggestions.length}<p class="muted">No working feeds or article listings found. Try another site or topic.</p>{/if}
			{#if run.sitemaps.length}
				<details><summary use:focusable>Sitemaps found ({run.sitemaps.length})</summary>
					<p class="small muted">These links are discovered, not validated for monitoring.</p>
					<ul>{#each run.sitemaps as sitemap}<li><a href={sitemap} target="_blank" rel="noopener noreferrer" use:focusable>{sitemap}</a></li>{/each}</ul>
				</details>
			{/if}
			{#if run.suggestions.length}
				<div class="assign">
					<Select label="Add to curator (optional)" bind:value={curator} group="scout-assign"
						options={[{ value: '', label: 'Source library only' }, ...curators.map(c => ({ value: String(c.id), label: c.name }))]} />
				</div>
				<div class="results">
					{#each run.suggestions as suggestion, index}
						<article class="result">
							<div class="hstack"><b>{suggestion.name}</b><span class="spacer"></span><span class="verified">✓ {suggestion.provider === 'rss' ? 'Feed validated' : 'Article listing tested'}</span></div>
							<a href={suggestion.url} target="_blank" rel="noopener noreferrer" use:focusable>{suggestion.url}</a>
							<p class="small muted">{suggestion.count} items found{suggestion.count === 0 ? ' — valid feed, currently empty' : ''}</p>
							{#if suggestion.sample.length}<ul>{#each suggestion.sample as item}<li>{item.title}</li>{/each}</ul>{/if}
							<button class="btn" disabled={accepting !== null} use:focusable={{ group: 'scout-actions', onSelect: () => accept(index) }}>{accepting === index ? 'Checking…' : 'Add source'}</button>
						</article>
					{/each}
				</div>
			{/if}
		</div>
	{/if}
</section>

<style>
	.scout { margin-top: 2rem; padding: clamp(1rem, 2vw, 1.6rem); border: 1px solid var(--line-strong); border-radius: var(--r-lg); background: linear-gradient(135deg, var(--surface), var(--bg-1)); }
	.intro { display: flex; align-items: center; justify-content: space-between; gap: 1rem; }
	.intro h2 { font-size: 1.5rem; margin: .3rem 0; }
	.intro p { margin: .3rem 0; }
	.eyebrow { color: var(--accent); letter-spacing: .14em; font-size: .72rem; font-weight: 800; }
	.mark { color: var(--accent); font-size: 3rem; }
	.research { display: flex; align-items: end; gap: .7rem; margin-top: 1.2rem; }
	.research :global(.field) { flex: 1; }
	.schedule { max-width: 24rem; margin-top: .8rem; }
	.plans { margin-top: 1rem; }
	.plan { display: flex; align-items: center; gap: .5rem; flex-wrap: wrap; padding: .4rem 0; overflow-wrap: anywhere; }
	.plan > span { flex: 1; min-width: 12rem; }
	.history { display: flex; gap: .5rem; overflow-x: auto; margin-top: 1.2rem; padding: .3rem; }
	.history-item { display: grid; gap: .2rem; min-width: 10rem; max-width: 15rem; text-align: left; padding: .6rem .8rem; border: 1px solid var(--line); border-radius: var(--r-md); background: var(--surface); color: var(--text); cursor: pointer; overflow-wrap: anywhere; }
	.history-item small { color: var(--muted); }
	.history-item.active { border-color: var(--accent); }
	.report { margin-top: 1.2rem; }
	.report h3 { margin: 0; overflow-wrap: anywhere; }
	.event { font-size: .9rem; color: var(--muted); margin: .4rem 0; }
	.event span, .verified { color: var(--accent); }
	.assign { display: grid; gap: .4rem; max-width: 24rem; margin: 1rem 0; color: var(--muted); }
	.results { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 20rem), 1fr)); gap: .7rem; }
	.result { display: grid; align-content: start; gap: .5rem; padding: 1rem; border: 1px solid var(--line); border-radius: var(--r-md); background: var(--surface); overflow-wrap: anywhere; }
	.result p, .result ul { margin: 0; }
	.result ul { padding-left: 1.2rem; font-size: .85rem; color: var(--muted); }
	.result .btn { justify-self: start; }
	.verified { font-size: .75rem; white-space: nowrap; }
	.err { color: var(--bad); }
	@media (max-width: 760px) { .research { flex-direction: column; align-items: stretch; } .mark { display: none; } }
</style>
