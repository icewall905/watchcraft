<script lang="ts">
	import { onMount } from 'svelte';
	import { focusable } from '$lib/nav';
	import { toast } from '$lib/stores/toast.svelte';
	import { hub, ago } from '$lib/hub/api';
	import { errMsg } from '$lib/hub/ui';
	import ErrorBox from '$lib/hub/ErrorBox.svelte';
	import type { Alert } from '$lib/hub/types';

	type Tab = 'new' | 'read' | 'snoozed' | 'resolved' | 'all';
	let snoozedIds = $state<number[]>([]);
	const isSnoozed = (a: Alert) => a.state === 'new' && (snoozedIds.includes(a.id) || (!!a.snooze_until && a.snooze_until * 1000 > Date.now()));
	const inTab = (a: Alert, t: Tab) => t === 'all' ? true : t === 'snoozed' ? isSnoozed(a) : t === 'new' ? a.state === 'new' && !isSnoozed(a) : a.state === t;
	let alerts = $state<Alert[]>([]);
	let curatorNames = $state<Record<number, string>>({});
	let loading = $state(true);
	let error = $state('');
	let tab = $state<Tab>('new');
	let busyId = $state<number | null>(null);
	let severityFilter = $state('');
	let curatorFilter = $state('');

	async function load() {
		try {
			const [a, sn, cs] = await Promise.all([hub.alerts('all', severityFilter || undefined, curatorFilter ? Number(curatorFilter) : undefined), hub.alerts('snoozed', severityFilter || undefined, curatorFilter ? Number(curatorFilter) : undefined).catch(() => ({ items: [] as Alert[] })), hub.curators().catch(() => [])]);
			snoozedIds = sn.items.map((x) => x.id); alerts = a.items; curatorNames = Object.fromEntries(cs.map((c) => [c.id, c.name])); error = '';
		} catch (e) { error = errMsg(e); } finally { loading = false; }
	}
	onMount(load);

	const count = (t: Tab) => alerts.filter((a) => inTab(a, t)).length;
	const shown = $derived(alerts.filter((a) => inTab(a, tab)));
	const SEV = ['critical', 'high', 'medium', 'low', 'info'];
	const sev = (s: string) => (SEV.includes(s) ? s : 'info');

	async function setState(a: Alert, state: string, snooze?: number) {
		busyId = a.id;
		try {
			await hub.alertState(a.id, state, snooze);
			alerts = alerts.map((x) => (x.id === a.id ? { ...x, state, snooze_until: snooze ? Date.now() / 1000 + snooze * 3600 : null } : x));
			snoozedIds = snooze ? [...snoozedIds, a.id] : snoozedIds.filter((i) => i !== a.id);
			toast.success(snooze ? 'Snoozed for 24 hours' : state === 'new' ? 'Reopened' : state === 'read' ? 'Marked read' : 'Resolved');
			if (snooze) await load();
		} catch (e) { error = errMsg(e); } finally { busyId = null; }
	}
	const exploited = (a: Alert) => a.item?.meta?.known_exploited === true || a.item?.meta?.known_exploited === 'true';
	const isAdvisory = (a: Alert) => a.item?.type === 'advisory';
	function applyFilters() { void load(); }
</script>

<svelte:head><title>Alerts · WatchCraft</title></svelte:head>

<div class="page">
	<header>
		<p class="eyebrow">EXPLORE</p>
		<h1>Alerts</h1>
		<p class="muted">Raised by curators when something matches your alert keywords. An alert means “look at this”, not “you are affected”.</p>
	</header>

	<div class="tabs" role="tablist">
		{#each [['new', 'New'], ['read', 'Read'], ['snoozed', 'Snoozed'], ['resolved', 'Resolved'], ['all', 'All']] as [k, l]}
			<button role="tab" class="tab" class:on={tab === k} aria-selected={tab === k} use:focusable onclick={() => (tab = k as Tab)}>{l} <span class="n">{count(k as Tab)}</span></button>
		{/each}
	</div>
	<div class="filters" aria-label="Alert filters">
		<label>Severity <select bind:value={severityFilter} onchange={applyFilters}><option value="">All severities</option><option value="critical">Critical</option><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option><option value="info">Info</option></select></label>
		<label>Curator <select bind:value={curatorFilter} onchange={applyFilters}><option value="">All curators</option>{#each Object.entries(curatorNames) as [id, name]}<option value={id}>{name}</option>{/each}</select></label>
	</div>

	{#if error}<ErrorBox message={error} onretry={load} />{/if}

	{#if loading}
		<div class="stack" aria-busy="true">{#each [0, 1, 2] as i (i)}<div class="skeleton" style="height:8rem"></div>{/each}</div>
	{:else if !shown.length && !error}
		<section class="empty"><span class="bigic">{tab === 'new' ? '✅' : '🔔'}</span><h2>{tab === 'new' ? 'Nothing needs attention.' : 'No alerts here.'}</h2><p>Turn on “Raise alerts” in a curator to be told about things like new vulnerabilities for software you use.</p><a class="btn" href="/curators" use:focusable>Open curators</a></section>
	{:else}
		<ul class="list">
			{#each shown as a (a.id)}
				<li class="card sev-{sev(a.severity)}" class:done={a.state === 'resolved'}>
					<div class="hstack"><span class="sev {sev(a.severity)}">{a.severity}</span><span class="small faint">{ago(a.created_at)}{#if a.curator_id && curatorNames[a.curator_id]} · <a href={`/curators/${a.curator_id}`} use:focusable>{curatorNames[a.curator_id]}</a>{/if} · {a.state}</span></div>
					<h2>{a.title}</h2>
					{#if a.reason}<p class="reason">{a.reason}</p>{/if}
					<p class="verify" role="note">
						{#if exploited(a)}<b class="kev">Confirmed by CISA: exploited in the wild.</b> That does not mean you are affected. Check whether you run an affected version.
						{:else if isAdvisory(a)}<b>Possibly relevant. Verify affected versions</b> before acting. Matched by keyword, not by your installed software.
						{:else}Possibly relevant: matched your keywords. Not a confirmed fact about your situation.{/if}
					</p>
					<div class="links small">
						{#if a.item}<a href={`/item/${a.item.id}`} use:focusable>Open item</a><a href={a.item.url} target="_blank" rel="noopener noreferrer" use:focusable>Original source ↗</a>{/if}
					</div>
					<div class="acts">
						{#if a.state === 'new' && !isSnoozed(a)}<button class="btn" disabled={busyId === a.id} use:focusable onclick={() => setState(a, 'read')}>Mark read</button>{/if}
						{#if a.state !== 'resolved'}<button class="btn" disabled={busyId === a.id} use:focusable onclick={() => setState(a, 'resolved')}>Resolve</button>
							<button class="btn ghost" disabled={busyId === a.id} use:focusable onclick={() => setState(a, 'new', 24)}>Snooze 24h</button>{/if}
						{#if a.state !== 'new' || isSnoozed(a)}<button class="btn ghost" disabled={busyId === a.id} use:focusable onclick={() => setState(a, 'new')}>Reopen</button>{/if}
					</div>
				</li>
			{/each}
		</ul>
	{/if}
</div>

<style>
	.eyebrow { font-size: .7rem; letter-spacing: .15em; color: var(--accent); font-weight: 700; margin: 0; }
	h1 { font-size: clamp(1.8rem, 3vw, 2.8rem); margin: .2rem 0; }
	.tabs { display: flex; gap: .4rem; border-bottom: 1px solid var(--line); margin: 1.2rem 0; overflow-x: auto; }
	.filters { display: flex; gap: .8rem; flex-wrap: wrap; margin: 0 0 1rem; }
	.filters label { display: grid; gap: .25rem; color: var(--muted); font-size: .8rem; }
	.filters select { min-height: 2.4rem; padding: .35rem .6rem; border: 1px solid var(--line); border-radius: var(--r-sm); background: var(--surface); color: var(--text); }
	.tab { min-height: 2.75rem; padding: .5rem 1.1rem; background: none; border: 0; border-bottom: 3px solid transparent; color: var(--muted); font-weight: 650; cursor: pointer; white-space: nowrap; }
	.tab.on { color: var(--text); border-bottom-color: var(--accent); }
	.n { font-size: .75rem; padding: .05rem .5rem; border-radius: var(--r-pill); background: var(--surface-2); margin-left: .3rem; }
	.list { list-style: none; padding: 0; margin: 0; display: grid; gap: .9rem; max-width: 56rem; }
	.card { display: grid; gap: .55rem; padding: 1.1rem 1.3rem; border: 1px solid var(--line); border-left: 4px solid var(--faint); border-radius: var(--r-lg); background: var(--surface); }
	.card.sev-critical { border-left-color: #ff3b5c; } .card.sev-high { border-left-color: var(--bad); } .card.sev-medium { border-left-color: var(--warn); } .card.sev-low { border-left-color: var(--info); }
	.card.done { opacity: .65; }
	.card h2 { margin: 0; font-size: 1.1rem; overflow-wrap: anywhere; }
	.card p { margin: 0; }
	.sev { font-size: .7rem; font-weight: 800; text-transform: uppercase; letter-spacing: .08em; padding: .2rem .6rem; border-radius: var(--r-pill); background: var(--surface-2); }
	.sev.critical { background: #ff3b5c; color: #fff; } .sev.high { background: rgba(255,92,122,.25); color: #ffb3c0; } .sev.medium { background: rgba(255,207,92,.2); color: var(--warn); } .sev.low { background: rgba(108,198,255,.2); color: var(--info); } .sev.info { color: var(--muted); }
	.reason { color: var(--muted); }
	.verify { font-size: .85rem; padding: .5rem .7rem; border-radius: var(--r-sm); background: rgba(255,255,255,.04); color: var(--muted); }
	.kev { color: var(--bad); }
	.links { display: flex; gap: 1rem; flex-wrap: wrap; }
	.acts { display: flex; gap: .4rem; flex-wrap: wrap; }
	.bigic { font-size: 3.5rem; } .faint { color: var(--faint); }
</style>
