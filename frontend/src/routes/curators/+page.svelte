<script lang="ts">
	import { onMount } from 'svelte';
	import { focusable } from '$lib/nav';
	import Modal from '$lib/components/Modal.svelte';
	import { toast } from '$lib/stores/toast.svelte';
	import { hub, ago } from '$lib/hub/api';
	import { statusOf, scheduleLabel, errMsg } from '$lib/hub/ui';
	import ErrorBox from '$lib/hub/ErrorBox.svelte';
	import type { Curator, Source, Template } from '$lib/hub/types';

	let curators = $state<Curator[]>([]);
	let sources = $state<Source[]>([]);
	let templates = $state<Template[]>([]);
	let loading = $state(true);
	let error = $state('');
	let kind = $state<'all' | 'watch' | 'research'>('all');
	let busyId = $state<number | null>(null);
	let confirm = $state<Curator | null>(null);

	const shown = $derived(curators.filter((c) => kind === 'all' || c.kind === kind));

	async function load() {
		try {
			[curators, sources, templates] = await Promise.all([hub.curators(), hub.sources(), hub.templates().catch(() => [])]);
			error = '';
		} catch (e) { error = errMsg(e); } finally { loading = false; }
	}
	onMount(() => { load(); const t = setInterval(() => { if (curators.some((c) => c.last_status === 'running' || c.last_status === 'queued')) load(); }, 3000); return () => clearInterval(t); });

	async function act(c: Curator, a: 'run' | 'pause' | 'resume' | 'duplicate') {
		busyId = c.id;
		try {
			await hub.curatorAction(c.id, a);
			toast.success(a === 'run' ? `${c.name} is running` : a === 'duplicate' ? 'Duplicated' : a === 'pause' ? 'Paused' : 'Resumed');
			await load();
		} catch (e) { error = errMsg(e); } finally { busyId = null; }
	}
	async function remove() {
		if (!confirm) return;
		const c = confirm;
		try { await hub.deleteCurator(c.id); confirm = null; toast.success(`Deleted ${c.name}`); await load(); } catch (e) { error = errMsg(e); confirm = null; }
	}
	const sourceSummary = (c: Curator) => {
		const rows = sources.filter((s) => c.source_ids.includes(s.id));
		if (!rows.length) return 'No sources';
		const names = [...new Set(rows.map((s) => s.provider.replace('_', ' ')))];
		return `${rows.length} source${rows.length > 1 ? 's' : ''} · ${names.slice(0, 3).join(', ')}${names.length > 3 ? '…' : ''}`;
	};
	const href = (c: Curator) => (c.kind === 'research' ? `/research/${c.id}` : `/curators/${c.id}`);
</script>

<svelte:head><title>Curators · WatchCraft</title></svelte:head>

<div class="page">
	<header class="head">
		<div>
			<p class="eyebrow">AUTOMATION</p>
			<h1>Curators</h1>
			<p class="muted">Each curator goes out on the internet for you, on a schedule, and brings back only what matters.</p>
		</div>
		<a class="btn primary big" href="/curators/new" use:focusable>＋ New curator</a>
	</header>

	<div class="hstack filters" role="group" aria-label="Filter by kind">
		{#each [['all', 'All'], ['watch', 'Watch'], ['research', 'Research']] as [k, l]}
			<button class="chip" class:on={kind === k} aria-pressed={kind === k} use:focusable onclick={() => (kind = k as typeof kind)}>{l}</button>
		{/each}
	</div>

	{#if error}<ErrorBox message={error} onretry={load} />{/if}

	{#if loading}
		<div class="grid" aria-busy="true">{#each [0, 1, 2] as i (i)}<div class="skeleton" style="height:14rem"></div>{/each}</div>
	{:else if !curators.length && !error}
		<section class="empty" aria-label="No curators yet">
			<span class="big-ic">🔭</span>
			<h2>Nothing is watching the internet for you yet.</h2>
			<p>Describe what you care about in a sentence, or start from a template.</p>
			<a class="btn primary" href="/curators/new" use:focusable>Describe what to find</a>
			{#if templates.length}
				<div class="tpls">
					{#each templates as t (t.key)}
						<a class="tpl" href={`/curators/new?template=${t.key}`} use:focusable><span class="ic">{t.icon}</span><b>{t.name}</b><span class="muted small">{t.description}</span></a>
					{/each}
				</div>
			{/if}
		</section>
	{:else if !shown.length}
		<section class="empty"><p>No {kind} curators.</p></section>
	{:else}
		<div class="grid">
			{#each shown as c (c.id)}
				{@const st = statusOf(c)}
				<article class="card" style:--c={c.color || 'var(--ai)'}>
					<div class="top">
						<span class="ic">{c.icon || '🔭'}</span>
						<div class="ttl">
							<h2><a href={href(c)} use:focusable>{c.name}</a></h2>
							<p class="muted small">{c.kind === 'research' ? 'Research' : 'Watch'} · {scheduleLabel(c.interval_hours)}</p>
						</div>
						<span class="st {st.toLowerCase()}">{st}</span>
					</div>
					{#if c.description || c.instructions}<p class="desc">{c.description || c.instructions}</p>{/if}
					{#if c.error}<p class="errtxt" title={c.error}>⚠ {c.error}</p>{/if}
					<dl>
						<div><dt>Last run</dt><dd>{c.last_run ? ago(c.last_run) : 'never'}</dd></div>
						<div><dt>Next run</dt><dd>{c.enabled && c.interval_hours && c.next_run ? ago(c.next_run) : '—'}</dd></div>
						<div><dt>Discoveries</dt><dd><a href={`/discover?curator=${c.id}`} use:focusable>{c.discovery_count}</a></dd></div>
						<div><dt>AI</dt><dd>{c.ai_enabled ? 'On' : 'Off'}</dd></div>
					</dl>
					<p class="small muted">{sourceSummary(c)}</p>
					<div class="acts">
						<button class="btn" disabled={busyId === c.id || st === 'Running'} use:focusable onclick={() => act(c, 'run')}>{st === 'Running' ? 'Running…' : 'Run now'}</button>
						<button class="btn" disabled={busyId === c.id} use:focusable onclick={() => act(c, c.enabled ? 'pause' : 'resume')}>{c.enabled ? 'Pause' : 'Resume'}</button>
						<button class="btn ghost" disabled={busyId === c.id} use:focusable onclick={() => act(c, 'duplicate')}>Duplicate</button>
						<a class="btn ghost" href={`${href(c)}${c.kind === 'research' ? '' : '?tab=settings'}`} use:focusable>Edit</a>
						<button class="btn ghost danger" use:focusable onclick={() => (confirm = c)}>Delete</button>
					</div>
				</article>
			{/each}
		</div>
	{/if}
</div>

{#if confirm}
	<Modal title="Delete curator?" size="sm" onClose={() => (confirm = null)}>
		<p><b>{confirm.name}</b> and its run history will be removed. Items it already found stay in your library if saved.</p>
		{#snippet footer()}
			<button class="btn ghost" use:focusable={{ autofocus: true }} onclick={() => (confirm = null)}>Cancel</button>
			<button class="btn danger" use:focusable onclick={remove}>Delete</button>
		{/snippet}
	</Modal>
{/if}

<style>
	.head { display: flex; justify-content: space-between; align-items: flex-end; gap: 1rem; flex-wrap: wrap; margin-bottom: 1.2rem; }
	.eyebrow { font-size: .7rem; letter-spacing: .15em; color: var(--accent); font-weight: 700; margin: 0; }
	h1 { font-size: clamp(1.8rem, 3vw, 2.8rem); margin: .2rem 0; }
	.filters { margin-bottom: 1.2rem; }
	.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 22rem), 1fr)); gap: 1.1rem; }
	.card { display: grid; gap: .8rem; padding: 1.2rem; border: 1px solid var(--line); border-top: 3px solid var(--c); border-radius: var(--r-lg); background: var(--surface); align-content: start; min-width: 0; }
	.top { display: flex; gap: .8rem; align-items: center; }
	.ic { font-size: 1.6rem; width: 2.8rem; height: 2.8rem; display: grid; place-items: center; border-radius: var(--r-md); background: color-mix(in srgb, var(--c, var(--ai)) 28%, transparent); flex: none; }
	.ttl { flex: 1; min-width: 0; }
	h2 { font-size: 1.1rem; margin: 0; overflow-wrap: anywhere; }
	h2 a { color: var(--text); }
	.st { font-size: .72rem; font-weight: 700; padding: .2rem .6rem; border-radius: var(--r-pill); border: 1px solid var(--line-strong); }
	.st.active { color: var(--ok); border-color: var(--ok); }
	.st.error { color: var(--bad); border-color: var(--bad); }
	.st.running { color: var(--info); border-color: var(--info); animation: pulse 1.4s infinite; }
	.st.paused { color: var(--muted); }
	.desc { margin: 0; color: var(--muted); font-size: .9rem; display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
	.errtxt { margin: 0; color: var(--bad); font-size: .82rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
	dl { display: grid; grid-template-columns: repeat(4, 1fr); gap: .5rem; margin: 0; }
	dt { font-size: .68rem; color: var(--faint); text-transform: uppercase; letter-spacing: .08em; }
	dd { margin: 0; font-weight: 650; font-size: .92rem; }
	.acts { display: flex; flex-wrap: wrap; gap: .4rem; }
	.acts .btn { min-height: 2.75rem; padding: .5rem .9rem; }
	.empty { padding: 3rem 1rem; }
	.big-ic { font-size: 3.5rem; }
	.tpls { display: grid; grid-template-columns: repeat(auto-fill, minmax(14rem, 1fr)); gap: .8rem; width: min(100%, 60rem); margin-top: 1rem; text-align: left; }
	.tpl { display: grid; gap: .3rem; padding: 1rem; border: 1px solid var(--line); border-radius: var(--r-lg); background: var(--surface); color: var(--text); min-height: 2.75rem; }
	.tpl .ic { width: auto; height: auto; background: none; justify-content: start; }
	@media (max-width: 760px) { dl { grid-template-columns: repeat(2, 1fr); } .head .btn { width: 100%; } }
</style>
