<script lang="ts">
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { focusable } from '$lib/nav';
	import Modal from '$lib/components/Modal.svelte';
	import Toggle from '$lib/components/form/Toggle.svelte';
	import { toast } from '$lib/stores/toast.svelte';
	import { hub, ago } from '$lib/hub/api';
	import { errMsg, duration } from '$lib/hub/ui';
	import Cited from '$lib/hub/Cited.svelte';
	import ContentCard from '$lib/hub/ContentCard.svelte';
	import ErrorBox from '$lib/hub/ErrorBox.svelte';
	import type { CuratorDetail } from '$lib/hub/types';

	const id = $derived(Number(page.params.id));
	let c = $state<CuratorDetail | null>(null);
	let loading = $state(true);
	let error = $state('');
	let busy = $state('');
	let confirm = $state(false);
	let notes = $state('');

	async function load(quiet = false) {
		if (!quiet) loading = true;
		try { const d = await hub.curator(id); c = d; if (!quiet) notes = d.description; error = ''; } catch (e) { error = errMsg(e); } finally { loading = false; }
	}
	$effect(() => { void id; load(); });
	$effect(() => {
		if (c?.last_status !== 'queued' && c?.last_status !== 'running') return;
		const t = setInterval(() => load(true), 3000);
		return () => clearInterval(t);
	});

	const groups = $derived.by(() => {
		const m: Record<string, number[]> = {};
		(c?.recent ?? []).forEach((it, i) => (m[it.type] ??= []).push(i));
		return Object.entries(m);
	});
	const citeIds = $derived(c?.report_refs ?? []);

	async function run() {
		busy = 'run';
		try { await hub.curatorAction(id, 'run'); toast.success('Research running'); await load(true); } catch (e) { error = errMsg(e); } finally { busy = ''; }
	}
	async function report() {
		busy = 'report';
		try { const r = await hub.report(id); if (c) c = { ...c, report: r.report, report_at: r.report_at, report_refs: r.report_refs }; toast.success('Report generated'); } catch (e) { error = errMsg(e); } finally { busy = ''; }
	}
	async function keepWatching(on: boolean) {
		try { const r = await hub.updateCurator(id, { interval_hours: on ? 24 : 0 }); if (c) c = { ...c, interval_hours: r.interval_hours, next_run: r.next_run }; } catch (e) { error = errMsg(e); await load(true); }
	}
	async function saveNotes() {
		if (!c || notes === c.description) return;
		try { await hub.updateCurator(id, { description: notes }); c = { ...c, description: notes }; toast.success('Notes saved'); } catch (e) { error = errMsg(e); }
	}
	async function remove() {
		try { await hub.deleteCurator(id); goto('/research'); } catch (e) { error = errMsg(e); confirm = false; }
	}
	let watching = $state(false);
	$effect(() => { watching = (c?.interval_hours ?? 0) > 0; });
</script>

<svelte:head><title>{c?.subject || c?.name || 'Research'} · WatchCraft</title></svelte:head>

<div class="page">
	<a class="small muted" href="/research" use:focusable>← Research</a>
	{#if loading}
		<div class="skeleton" style="height:5rem;margin:1rem 0"></div><div class="skeleton" style="height:16rem"></div>
	{:else if error && !c}
		<ErrorBox message={error} onretry={() => load()} />
	{:else if c}
		{#if error}<ErrorBox message={error} onretry={() => load(true)} />{/if}
		<header class="head">
			<div>
				<p class="eyebrow">{c.subject_type.toUpperCase()} RESEARCH</p>
				<h1>{c.subject || c.name}</h1>
				{#if c.aliases.length}<p class="muted small">Also: {c.aliases.join(', ')}</p>{/if}
				<p class="small faint">{c.last_status === 'queued' ? 'Queued…' : c.last_status === 'running' ? 'Running now…' : c.last_run ? `Last run ${ago(c.last_run)}` : 'Not run yet'} · {c.discovery_count} findings</p>
			</div>
			<div class="acts">
				<button class="btn primary" disabled={busy === 'run' || c.last_status === 'running' || c.last_status === 'queued'} use:focusable onclick={run}>{c.last_status === 'queued' ? 'Queued…' : c.last_status === 'running' ? 'Running…' : 'Run again'}</button>
				<button class="btn ghost danger" use:focusable onclick={() => (confirm = true)}>Delete</button>
			</div>
		</header>
		<p class="ethics" role="note">Public sources only. Use lawfully and respect privacy. No doxxing or harassment.</p>
		{#if c.error}<div class="errbox" role="alert">Last run problem: {c.error}</div>{/if}

		<div class="narrow"><Toggle label="Keep watching" hint="Re-run daily and flag new findings." bind:checked={watching} onchange={keepWatching} /></div>

		<section>
			<div class="hstack"><h2>Report</h2><span class="spacer"></span>
				{#if c.report_at}<span class="small faint">Generated {ago(c.report_at)}</span>{/if}
				<button class="btn" disabled={busy === 'report' || !c.recent.length} use:focusable onclick={report}>{busy === 'report' ? 'Writing…' : c.report ? 'Regenerate report' : 'Generate report'}</button>
			</div>
			{#if c.report}
				<div class="panel"><Cited text={c.report} itemIds={citeIds} /></div>
				<p class="small faint">Numbers like [1] link to the finding they cite. Verify claims at the original source.</p>
			{:else}
				<div class="empty"><p>{c.recent.length ? 'No report yet. Generate one from the findings below.' : 'Run the research first, then generate a report.'}</p></div>
			{/if}
		</section>

		<section>
			<h2>Findings</h2>
			{#if !c.recent.length}
				<div class="empty"><p>{c.last_run ? 'No findings yet.' : 'Findings will appear here after the first run.'}</p></div>
			{:else}
				{#each groups as [type, idxs] (type)}
					<h3 class="gt">{type} <span class="faint">({idxs.length})</span></h3>
					<div class="grid-cards">
						{#each idxs as i (c.recent[i].id)}<ContentCard bind:item={c.recent[i]} layout="card" />{/each}
					</div>
				{/each}
			{/if}
		</section>

		<section class="two">
			<div>
				<h2>Queries used</h2>
				{#if c.queries.length}<div class="chips">{#each c.queries as q}<span class="chip">{q}</span>{/each}</div>{:else}<p class="muted small">None.</p>{/if}
			</div>
			<div>
				<h2>Notes</h2>
				<textarea class="input" rows="4" aria-label="Notes" placeholder="Your own notes on this research…" bind:value={notes} maxlength="4000" onblur={saveNotes} use:focusable></textarea>
				<p class="small faint">{notes.length} / 4000</p>
			</div>
		</section>

		<section>
			<h2>Run history</h2>
			{#if !c.runs.length}<p class="muted small">No runs yet.</p>{:else}
				<div class="tablewrap"><table>
					<thead><tr><th>Started</th><th>Duration</th><th>Status</th><th>New / seen</th><th>Errors</th></tr></thead>
					<tbody>{#each c.runs as r (r.id)}<tr><td>{new Date(r.started_at * 1000).toLocaleString()}</td><td>{duration(r.started_at, r.finished_at)}</td><td>{r.status}</td><td>{r.new_items} / {r.seen_items}</td><td>{#if r.errors.length}{#each r.errors as e}<div class="bad"><b>{e.source}</b>: {e.message}</div>{/each}{:else}<span class="faint">none</span>{/if}</td></tr>{/each}</tbody>
				</table></div>
			{/if}
		</section>
	{/if}
</div>

{#if confirm && c}
	<Modal title="Delete research?" size="sm" onClose={() => (confirm = false)}>
		<p><b>{c.subject || c.name}</b>, its report and run history will be removed.</p>
		{#snippet footer()}
			<button class="btn ghost" use:focusable={{ autofocus: true }} onclick={() => (confirm = false)}>Cancel</button>
			<button class="btn danger" use:focusable onclick={remove}>Delete</button>
		{/snippet}
	</Modal>
{/if}

<style>
	.head { display: flex; justify-content: space-between; gap: 1rem; flex-wrap: wrap; margin: 1rem 0 .6rem; align-items: flex-end; }
	.eyebrow { font-size: .7rem; letter-spacing: .15em; color: var(--accent); font-weight: 700; margin: 0; }
	h1 { font-size: clamp(1.8rem, 3.2vw, 2.8rem); margin: .2rem 0; overflow-wrap: anywhere; }
	.head p { margin: .15rem 0; }
	.acts { display: flex; gap: .5rem; flex-wrap: wrap; }
	.ethics { padding: .6rem 1rem; border-left: 3px solid var(--warn); background: rgba(255,207,92,.08); border-radius: var(--r-sm); color: var(--warn); font-size: .85rem; }
	.errbox { padding: .8rem 1rem; border-radius: var(--r-md); border: 1px solid rgba(255,92,122,.45); background: rgba(255,92,122,.1); color: #ffd1d9; margin: 1rem 0; }
	.narrow { max-width: 34rem; margin: 1rem 0; }
	section { margin-top: 2rem; }
	h2 { font-size: 1.2rem; }
	.gt { text-transform: capitalize; margin: 1.4rem 0 .6rem; font-size: 1rem; color: var(--muted); }
	.two { display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; }
	.chips { display: flex; flex-wrap: wrap; gap: .4rem; }
	.chip { cursor: default; }
	.tablewrap { overflow-x: auto; border: 1px solid var(--line); border-radius: var(--r-lg); background: var(--surface); }
	table { width: 100%; border-collapse: collapse; font-size: .9rem; }
	th, td { text-align: left; padding: .7rem .9rem; border-bottom: 1px solid var(--line); vertical-align: top; }
	th { color: var(--faint); font-size: .72rem; text-transform: uppercase; letter-spacing: .08em; }
	.bad { color: var(--bad); }
	.faint { color: var(--faint); }
	@media (max-width: 760px) { .two { grid-template-columns: 1fr; } }
</style>
