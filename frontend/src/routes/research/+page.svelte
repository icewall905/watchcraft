<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { focusable } from '$lib/nav';
	import TextField from '$lib/components/form/TextField.svelte';
	import Select from '$lib/components/form/Select.svelte';
	import Toggle from '$lib/components/form/Toggle.svelte';
	import ChipInput from '$lib/components/form/ChipInput.svelte';
	import { hub, ago } from '$lib/hub/api';
	import { errMsg, statusOf } from '$lib/hub/ui';
	import ErrorBox from '$lib/hub/ErrorBox.svelte';
	import type { Curator, ProviderInfo } from '$lib/hub/types';

	const TYPES = [
		{ value: 'topic', label: 'Topic', hint: 'e.g. solid-state batteries' },
		{ value: 'organization', label: 'Organisation', hint: 'e.g. Acme Corp' },
		{ value: 'person', label: 'Person (public role)', hint: 'e.g. CEO of Acme' },
		{ value: 'domain', label: 'Domain', hint: 'e.g. example.com' },
		{ value: 'product', label: 'Product or software', hint: 'e.g. nginx 1.25' }
	];
	const DEFAULTS = ['web_search', 'wikipedia', 'hackernews', 'github_search'];
	const defaultsFor = (t: string) => [...DEFAULTS, ...(t === 'product' ? ['nvd'] : []), ...(t === 'domain' ? ['rdap'] : [])];

	let briefs = $state<Curator[]>([]);
	let providers = $state<ProviderInfo[]>([]);
	let loading = $state(true);
	let error = $state('');
	let showNew = $state(page.url.searchParams.get('new') === '1');
	let subject = $state('');
	let subjectType = $state('topic');
	let aliases = $state<string[]>([]);
	let selected = $state<string[]>(defaultsFor('topic'));
	let watch = $state(false);
	let busy = $state(false);
	let formErr = $state('');

	const queryProviders = $derived(providers.filter((p) => p.mode === 'query' && !p.needs_url || p.key === 'rdap'));

	async function load() {
		try { [briefs, providers] = await Promise.all([hub.curators('research'), hub.registry().then((r) => r.providers)]); error = ''; if (!briefs.length) showNew = true; }
		catch (e) { error = errMsg(e); } finally { loading = false; }
	}
	onMount(load);

	function typeChanged(t: string) {
		selected = defaultsFor(t).filter((k) => providers.find((p) => p.key === k)?.available);
	}
	$effect(() => {
		if (!providers.length) return;
		const keep = untrack(() => selected).filter((k) => providers.find((p) => p.key === k)?.available !== false);
		if (keep.length !== untrack(() => selected).length) selected = keep;
	});
	const toggle = (k: string) => (selected = selected.includes(k) ? selected.filter((x) => x !== k) : [...selected, k]);

	async function start(e: SubmitEvent) {
		e.preventDefault(); formErr = '';
		const subj = subject.trim();
		if (!subj) { formErr = 'Enter what to research.'; return; }
		busy = true;
		try {
			const d = await hub.draft(subj, 'research');
			const existing = await hub.sources();
			const ids: number[] = [];
			for (const key of selected) {
				let row = existing.find((s) => s.provider === key && !s.url);
				if (!row) row = await hub.createSource({ provider: key });
				ids.push(row.id);
			}
			const queries = [...new Set([...(d.queries ?? []), subj, ...aliases])];
			const c = await hub.createCurator({
				name: subj.slice(0, 100), kind: 'research', subject: subj, subject_type: subjectType, aliases, queries,
				keywords: [...new Set([subj, ...aliases])], exclusions: d.exclusions ?? [], content_types: d.content_types ?? [],
				instructions: d.instructions || `Research ${subj} from public sources and report with citations.`,
				source_ids: ids, interval_hours: watch ? 24 : 0, ai_enabled: true, icon: '🕵️', color: '#6cc6ff', min_score: 0, max_per_run: 40
			});
			await hub.curatorAction(c.id, 'run');
			goto(`/research/${c.id}`);
		} catch (err) { formErr = errMsg(err); } finally { busy = false; }
	}
</script>

<svelte:head><title>Research · WatchCraft</title></svelte:head>

<div class="page">
	<header class="head">
		<div>
			<p class="eyebrow">RESEARCH</p>
			<h1>Deep research from public sources</h1>
			<p class="muted">Name a subject. WatchCraft searches, collects findings, and writes a cited report you can verify.</p>
		</div>
		{#if !showNew}<button class="btn primary big" use:focusable onclick={() => (showNew = true)}>＋ New research</button>{/if}
	</header>
	<p class="ethics" role="note">Public sources only. Use lawfully and respect privacy. No doxxing or harassment.</p>

	{#if showNew}
		<form class="panel stack form" onsubmit={start}>
			<h2>New research brief</h2>
			<TextField label="Subject" bind:value={subject} placeholder="Acme Corp, example.com, solid-state batteries…" autofocus group="rs" />
			<Select label="Subject type" bind:value={subjectType} options={TYPES} onchange={typeChanged} group="rs" />
			<ChipInput label="Aliases" hint="Other names, spellings or handles to look for." bind:values={aliases} group="rs-al" />
			<div class="field">
				<span class="label">Search these providers</span>
				<div class="opts">
					{#each queryProviders as p (p.key)}
						<label class="opt" class:off={!p.available}>
							<input type="checkbox" disabled={!p.available} checked={selected.includes(p.key)} onchange={() => toggle(p.key)} use:focusable />
							<span>{p.label} <span class="faint">{p.available ? '' : p.reason}</span></span>
						</label>
					{/each}
				</div>
				{#if providers.find((p) => p.key === 'web_search' && !p.available)}<span class="hint"><a href="/settings?tab=search" use:focusable>Set up web search</a> for much better coverage.</span>{/if}
			</div>
			<Toggle label="Keep watching" hint="Re-run daily and flag new findings. Off = one-off research." bind:checked={watch} group="rs" />
			{#if formErr}<ErrorBox message={formErr} />{/if}
			<div class="hstack">
				<button class="btn primary big" type="submit" disabled={busy} use:focusable>{busy ? 'Starting research…' : 'Start research'}</button>
				{#if briefs.length}<button class="btn ghost" type="button" use:focusable onclick={() => (showNew = false)}>Cancel</button>{/if}
			</div>
		</form>
	{/if}

	{#if error}<ErrorBox message={error} onretry={load} />{/if}
	{#if loading}
		<div class="grid" aria-busy="true">{#each [0, 1, 2] as i (i)}<div class="skeleton" style="height:9rem"></div>{/each}</div>
	{:else if briefs.length}
		<h2>Your briefs</h2>
		<div class="grid">
			{#each briefs as c (c.id)}
				{@const st = statusOf(c)}
				<a class="card" href={`/research/${c.id}`} use:focusable>
					<span class="type">{c.subject_type}</span>
					<b>{c.subject || c.name}</b>
					<span class="small muted">{c.discovery_count} findings · {c.report ? `report ${ago(c.report_at)}` : 'no report yet'}</span>
					<span class="small faint">{st}{c.interval_hours ? ' · watching' : ' · one-off'}{c.last_run ? ` · ran ${ago(c.last_run)}` : ''}</span>
				</a>
			{/each}
		</div>
	{/if}
</div>

<style>
	.head { display: flex; justify-content: space-between; align-items: flex-end; gap: 1rem; flex-wrap: wrap; }
	.eyebrow { font-size: .7rem; letter-spacing: .15em; color: var(--accent); font-weight: 700; margin: 0; }
	h1 { font-size: clamp(1.7rem, 3vw, 2.6rem); margin: .2rem 0; }
	.ethics { padding: .7rem 1rem; border-left: 3px solid var(--warn); background: rgba(255, 207, 92, .08); border-radius: var(--r-sm); color: var(--warn); font-size: .9rem; margin: 1rem 0 1.5rem; }
	.form { max-width: 44rem; margin-bottom: 2rem; }
	.form h2 { margin: 0; }
	.opts { display: flex; flex-wrap: wrap; gap: .5rem; }
	.opt { display: flex; gap: .55rem; align-items: center; min-height: 2.75rem; padding: .4rem .8rem; border: 1px solid var(--line); border-radius: var(--r-md); cursor: pointer; }
	.opt input { width: 1.2rem; height: 1.2rem; accent-color: var(--accent); }
	.opt.off { opacity: .55; cursor: not-allowed; }
	.faint { color: var(--faint); font-size: .8rem; }
	.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 18rem), 1fr)); gap: 1rem; }
	.card { display: grid; gap: .35rem; padding: 1.1rem; border: 1px solid var(--line); border-radius: var(--r-lg); background: var(--surface); color: var(--text); min-height: 2.75rem; align-content: start; }
	.card:hover { border-color: var(--accent); }
	.card b { font-size: 1.1rem; overflow-wrap: anywhere; }
	.type { text-transform: uppercase; letter-spacing: .1em; font-size: .7rem; font-weight: 700; color: var(--accent); }
</style>
