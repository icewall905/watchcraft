<script lang="ts">
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { focusable } from '$lib/nav';
	import Modal from '$lib/components/Modal.svelte';
	import { toast } from '$lib/stores/toast.svelte';
	import { hub, ago } from '$lib/hub/api';
	import { statusOf, scheduleLabel, duration, errMsg } from '$lib/hub/ui';
	import ContentCard from '$lib/hub/ContentCard.svelte';
	import CuratorForm from '$lib/hub/CuratorForm.svelte';
	import ErrorBox from '$lib/hub/ErrorBox.svelte';
	import type { CuratorDetail, CuratorInput } from '$lib/hub/types';

	const id = $derived(Number(page.params.id));
	let c = $state<CuratorDetail | null>(null);
	let loading = $state(true);
	let error = $state('');
	let busy = $state(false);
	let confirm = $state(false);
	let tab = $state<'overview' | 'runs' | 'settings'>((['overview', 'runs', 'settings'].includes(page.url.searchParams.get('tab') ?? '') ? page.url.searchParams.get('tab') : 'overview') as 'overview');

	async function load(quiet = false) {
		if (!quiet) loading = true;
		try {
			const d = await hub.curator(id);
			if (d.kind === 'research' && !quiet) { goto(`/research/${d.id}`, { replaceState: true }); return; }
			c = d; error = '';
		} catch (e) { error = errMsg(e); } finally { loading = false; }
	}
	$effect(() => { void id; load(); });
	$effect(() => {
		if (!active) return;
		const t = setInterval(() => load(true), 3000);
		return () => clearInterval(t);
	});

	async function act(a: 'run' | 'pause' | 'resume' | 'duplicate') {
		busy = true;
		try {
			const r = await hub.curatorAction(id, a);
			if (a === 'duplicate') { toast.success('Duplicated'); goto(`/curators/${r.id}`); return; }
			toast.success(a === 'run' ? 'Run started' : a === 'pause' ? 'Paused' : 'Resumed');
			await load(true);
		} catch (e) { error = errMsg(e); } finally { busy = false; }
	}
	async function remove() {
		try { await hub.deleteCurator(id); toast.success('Curator deleted'); goto('/curators'); } catch (e) { error = errMsg(e); confirm = false; }
	}
	async function save(v: CuratorInput) {
		await hub.updateCurator(id, v);
		toast.success('Curator saved');
		await load(true);
		tab = 'overview';
	}
	const active = $derived(c?.last_status === 'queued' || c?.last_status === 'running');
	async function exportJson() {
		if (!c) return;
		try {
			const data = await hub.exportCurator(c.id);
			const slug = c.name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || `curator-${c.id}`;
			const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
			const a = document.createElement('a'); a.href = url; a.download = `${slug}.watchcraft.json`; a.click();
			setTimeout(() => URL.revokeObjectURL(url), 1000);
		} catch (e) { error = errMsg(e); }
	}
	const st = $derived(c ? statusOf(c) : 'Active');
</script>

<svelte:head><title>{c?.name ?? 'Curator'} · WatchCraft</title></svelte:head>

<div class="page">
	<a class="small muted" href="/curators" use:focusable>← Curators</a>
	{#if loading}
		<div class="skeleton" style="height:6rem;margin:1rem 0"></div><div class="skeleton" style="height:18rem"></div>
	{:else if error && !c}
		<ErrorBox message={error} onretry={() => load()} />
	{:else if c}
		{#if error}<ErrorBox message={error} onretry={() => load(true)} />{/if}
		<header class="head">
			<span class="ic" style:--c={c.color}>{c.icon}</span>
			<div class="ttl">
				<h1>{c.name} <span class="st {st.toLowerCase()}">{st}</span></h1>
				{#if c.description}<p class="muted">{c.description}</p>{/if}
				<p class="small muted">{scheduleLabel(c.interval_hours)} · last run {c.last_run ? ago(c.last_run) : 'never'}{#if c.enabled && c.interval_hours && c.next_run != null} · next {ago(c.next_run)}{/if} · AI {c.ai_enabled ? 'on' : 'off'}</p>
			</div>
			<div class="acts">
				<button class="btn primary" disabled={busy || active} use:focusable onclick={() => act('run')}>{c.last_status === 'queued' ? 'Queued…' : c.last_status === 'running' ? 'Running…' : 'Run now'}</button>
				<button class="btn" disabled={busy} use:focusable onclick={() => act(c!.enabled ? 'pause' : 'resume')}>{c.enabled ? 'Pause' : 'Resume'}</button>
				<button class="btn ghost" disabled={busy} use:focusable onclick={() => act('duplicate')}>Duplicate</button>
				<button class="btn ghost" use:focusable onclick={exportJson}>Export JSON</button>
				<button class="btn ghost danger" use:focusable onclick={() => (confirm = true)}>Delete</button>
			</div>
		</header>
		{#if c.error}<div class="errbox" role="alert">Last run problem: {c.error}</div>{/if}

		<div class="tabs" role="tablist">
			{#each [['overview', 'Overview'], ['runs', `Runs (${c.runs.length})`], ['settings', 'Settings']] as [k, l]}
				<button role="tab" class="tab" class:on={tab === k} aria-selected={tab === k} use:focusable onclick={() => (tab = k as typeof tab)}>{l}</button>
			{/each}
		</div>

		{#if tab === 'overview'}
			<section>
				<div class="hstack"><h2>Recent discoveries</h2><span class="spacer"></span><a class="btn" href={`/discover?curator=${c.id}`} use:focusable>All {c.discovery_count} in Discover</a></div>
				{#if c.instructions}<p class="goal"><b>Goal:</b> {c.instructions}</p>{/if}
				{#if !c.recent.length}
					<div class="empty"><p>{c.last_run ? 'Nothing found yet. Check sources or loosen the filters in Settings.' : 'This curator has not run yet.'}</p><button class="btn primary" disabled={busy || active} use:focusable onclick={() => act('run')}>Run now</button></div>
				{:else}
					<div class="grid-cards">{#each c.recent as _, i (c.recent[i].id)}<ContentCard bind:item={c.recent[i]} layout="card" />{/each}</div>
				{/if}
			</section>
		{:else if tab === 'runs'}
			<section>
				{#if !c.runs.length}
					<div class="empty"><p>No runs recorded yet.</p></div>
				{:else}
					<div class="tablewrap">
						<table>
							<thead><tr><th>Started</th><th>Duration</th><th>Status</th><th>Trigger</th><th>New / seen</th><th>Errors</th></tr></thead>
							<tbody>
								{#each c.runs as r (r.id)}
									<tr>
										<td>{new Date(r.started_at * 1000).toLocaleString()}</td>
										<td>{duration(r.started_at, r.finished_at)}</td>
										<td><span class="st {r.status === 'error' || r.status === 'failed' ? 'error' : r.status === 'partial' ? 'warn' : r.status === 'running' || r.status === 'queued' ? 'running' : 'active'}">{r.status}</span></td>
										<td>{r.trigger}</td>
										<td>{r.new_items} / {r.seen_items}</td>
										<td>{#if r.errors.length}<ul class="errs">{#each r.errors as e}<li><b>{e.source}</b>: {e.message}</li>{/each}</ul>{:else}<span class="faint">none</span>{/if}</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				{/if}
			</section>
		{:else}
			{#key c.id + ':' + (c.created_at ?? 0)}
				<CuratorForm initial={c} submitLabel="Save changes" onsave={save} />
			{/key}
		{/if}
	{/if}
</div>

{#if confirm && c}
	<Modal title="Delete curator?" size="sm" onClose={() => (confirm = false)}>
		<p><b>{c.name}</b> and its run history will be removed.</p>
		{#snippet footer()}
			<button class="btn ghost" use:focusable={{ autofocus: true }} onclick={() => (confirm = false)}>Cancel</button>
			<button class="btn danger" use:focusable onclick={remove}>Delete</button>
		{/snippet}
	</Modal>
{/if}

<style>
	.head { display: grid; grid-template-columns: auto 1fr; gap: 1rem 1.2rem; margin: 1rem 0; align-items: center; }
	.acts { grid-column: 1 / -1; display: flex; flex-wrap: wrap; gap: .5rem; }
	.ic { font-size: 2rem; width: 3.6rem; height: 3.6rem; display: grid; place-items: center; border-radius: var(--r-md); background: color-mix(in srgb, var(--c) 30%, transparent); border: 1px solid var(--c); }
	h1 { margin: 0; font-size: clamp(1.6rem, 3vw, 2.4rem); display: flex; gap: .7rem; align-items: center; flex-wrap: wrap; }
	.ttl p { margin: .2rem 0 0; }
	.st { font-size: .72rem; font-weight: 700; padding: .2rem .6rem; border-radius: var(--r-pill); border: 1px solid var(--line-strong); vertical-align: middle; }
	.st.active { color: var(--ok); border-color: var(--ok); }
	.st.warn { color: var(--warn); border-color: var(--warn); }
	.st.error { color: var(--bad); border-color: var(--bad); }
	.st.running { color: var(--info); border-color: var(--info); animation: pulse 1.4s infinite; }
	.st.paused { color: var(--muted); }
	.errbox { padding: .8rem 1rem; border-radius: var(--r-md); border: 1px solid rgba(255,92,122,.45); background: rgba(255,92,122,.1); color: #ffd1d9; margin-bottom: 1rem; }
	.tabs { display: flex; gap: .4rem; border-bottom: 1px solid var(--line); margin: 1rem 0 1.4rem; overflow-x: auto; }
	.tab { min-height: 2.75rem; padding: .5rem 1.1rem; background: none; border: 0; border-bottom: 3px solid transparent; color: var(--muted); font-weight: 650; cursor: pointer; white-space: nowrap; }
	.tab.on { color: var(--text); border-bottom-color: var(--accent); }
	.goal { color: var(--muted); max-width: 50rem; }
	.tablewrap { overflow-x: auto; border: 1px solid var(--line); border-radius: var(--r-lg); background: var(--surface); }
	table { width: 100%; border-collapse: collapse; font-size: .9rem; }
	th, td { text-align: left; padding: .7rem .9rem; border-bottom: 1px solid var(--line); vertical-align: top; }
	th { color: var(--faint); font-size: .72rem; text-transform: uppercase; letter-spacing: .08em; }
	.errs { margin: 0; padding-left: 1rem; color: var(--bad); }
	@media (max-width: 760px) { .head { grid-template-columns: 1fr; } .acts .btn { flex: 1 1 auto; } }
</style>
