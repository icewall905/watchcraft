<script lang="ts">
	import { onMount } from 'svelte';
	import { focusable } from '$lib/nav';
	import Select from '$lib/components/form/Select.svelte';
	import Toggle from '$lib/components/form/Toggle.svelte';
	import ErrorBox from '$lib/hub/ErrorBox.svelte';
	import { hub, ago } from '$lib/hub/api';
	import { errMsg } from '$lib/hub/ui';
	import { toast } from '$lib/stores/toast.svelte';
	import type { Curator, ScoutReport, ScoutReportConfig } from '$lib/hub/types';

	let reports = $state<ScoutReport[]>([]);
	let curators = $state<Curator[]>([]);
	let config = $state<ScoutReportConfig | null>(null);
	let period = $state<'daily' | 'weekly'>('daily');
	let curator = $state('');
	let loading = $state(true);
	let busy = $state(false);
	let saving = $state(false);
	let error = $state('');
	let selected = $state<number | null>(null);
	const current = $derived(reports.find(r => r.id === selected) ?? reports[0]);

	async function load() {
		try { [reports, config, curators] = await Promise.all([hub.scoutReports(), hub.reportConfig(), hub.curators()]); error = ''; }
		catch (e) { error = errMsg(e); } finally { loading = false; }
	}
	onMount(load);
	async function generate() {
		busy = true; error = '';
		try { const issue = await hub.generateScoutReport(period, curator ? Number(curator) : null); reports = [issue, ...reports]; selected = issue.id; }
		catch (e) { error = errMsg(e); } finally { busy = false; }
	}
	async function saveConfig() {
		if (!config) return;
		saving = true;
		try { config = await hub.updateReportConfig({ frequency: config.frequency, show_home: config.show_home }); toast.success('Report settings saved'); }
		catch (e) { error = errMsg(e); } finally { saving = false; }
	}
</script>

<svelte:head><title>Scout Report · WatchCraft</title></svelte:head>
<div class="page report">
	<header><p class="eyebrow">RESEARCH TEAM</p><h1>Your Scout Report</h1><p class="muted">What your research team has been up to, with the discoveries and sources behind it.</p></header>
	{#if error}<ErrorBox message={error} onretry={load} />{/if}
	{#if loading}<div class="skeleton" style="height:10rem"></div>{:else if config}
		<section class="controls panel">
			<h2>Create a report</h2>
			<div class="options">
				<Select label="Time period" bind:value={period} options={[{ value: 'daily', label: 'Last 24 hours' }, { value: 'weekly', label: 'Last 7 days' }]} group="report-controls" />
				<Select label="Curator" bind:value={curator} options={[{ value: '', label: 'All curators' }, ...curators.map(c => ({ value: String(c.id), label: c.name }))]} group="report-controls" />
			</div>
			<button class="btn primary" disabled={busy} use:focusable={{ group: 'report-controls', onSelect: generate }}>{busy ? 'Creating…' : 'Generate report'}</button>
			<details><summary use:focusable>Report schedule and home preview</summary>
				<div class="settings">
					<Select label="Automatic reports" bind:value={config.frequency} options={[{ value: 'manual', label: 'Off' }, { value: 'daily', label: 'Daily' }, { value: 'weekly', label: 'Weekly' }]} group="report-settings" />
					<Toggle label="Show a Scout Report preview on home" hint="Only for this profile; the video home stays in place." bind:checked={config.show_home} group="report-settings" />
					<button class="btn" disabled={saving} use:focusable={{ group: 'report-settings', onSelect: saveConfig }}>{saving ? 'Saving…' : 'Save report settings'}</button>
					{#if config.next_run}<p class="small muted">Next report {ago(config.next_run)}</p>{/if}
				</div>
			</details>
		</section>
		{#if reports.length}
			<nav class="history" aria-label="Report history">
				{#each reports as r}<button class:active={current?.id === r.id} aria-pressed={current?.id === r.id} use:focusable={{ group: 'report-history', onSelect: () => (selected = r.id) }}>{new Date(r.created_at * 1000).toLocaleString()} · {r.sections.curator_name || 'All curators'}</button>{/each}
			</nav>
		{/if}
		{#if current}
			<article class="issue">
				<p class="eyebrow">{current.period === 'daily' ? 'LAST 24 HOURS' : 'LAST 7 DAYS'} · {new Date(current.created_at * 1000).toLocaleString()}</p>
				<h2>{current.sections.curator_name || 'Your research team'}</h2>
				<p>{current.sections.runs.count} curator runs · {current.sections.runs.new_items} new findings{#if current.sections.runs.failed} · {current.sections.runs.failed} runs need attention{/if}</p>
				{#if current.curator_id === null}<p>Source Scout completed {current.sections.scout.runs} research runs and validated {current.sections.scout.validated_sources} source suggestions. <a href="/sources" use:focusable>View sources</a></p>{/if}
				{#if current.sections.stories?.length}
					<section class="stories">
						<h3>Discovery stories</h3>
						<ul>
							{#each current.sections.stories as story}
								<li>
									<p class="story-title">{story.title}</p>
									<p class="small muted">{story.text}</p>
									<div class="story-links">{#each story.links as link}
										<a href={link.href} use:focusable>{link.label} →</a>
									{/each}</div>
								</li>
							{/each}
						</ul>
					</section>
				{/if}
				<h3>Discoveries</h3>
				{#if current.sections.findings.length}
					<ul class="findings">{#each current.sections.findings as finding}
						<li><a href="/item/{finding.id}" use:focusable>{finding.title}</a><p class="small muted">{finding.curator_name}{finding.publisher ? ` · ${finding.publisher}` : ''} · found {ago(finding.found_at)}</p>{#if finding.summary}<p>{#if finding.summary_ai}<span class="small muted">AI summary · </span>{/if}{finding.summary}</p>{/if}<a class="small" href={finding.url} target="_blank" rel="noopener noreferrer" use:focusable>Original source ↗</a></li>
					{/each}</ul>
				{:else}<p class="muted">No new unreported discoveries in this period.</p>{/if}
				{#if current.sections.alerts.length}<h3>Needs attention</h3><ul>{#each current.sections.alerts as alert}<li><a href="/alerts" use:focusable>{alert.title} · {alert.severity}</a></li>{/each}</ul>{/if}
				{#if current.sections.memes?.length}
					<h3>Meme Radar</h3>
					<ul>{#each current.sections.memes as m}<li><a href="/memes" use:focusable>{m.name}</a> · {m.state}{#if m.last7 != null} · {m.last7} example{m.last7 === 1 ? '' : 's'} in 7 days{#if m.communities != null} across {m.communities} communities{/if}{/if}</li>{/each}</ul>
				{/if}
				{#if current.sections.upcoming.length}<h3>What's next</h3><ul>{#each current.sections.upcoming as item}<li><a href={item.href} use:focusable>{item.name}</a> · {ago(item.next_run)}</li>{/each}</ul>{/if}
			</article>
		{:else}<div class="empty"><h2>No reports yet</h2><p>Generate a report to see what your researchers found.</p></div>{/if}
	{/if}
</div>
<style>
	.report { max-width: 82rem; margin: auto; }
	.eyebrow { color: var(--accent); font-size: .7rem; font-weight: 800; letter-spacing: .14em; }
	h1 { font-size: clamp(1.8rem, 3vw, 2.8rem); margin: .2rem 0; }
	.controls { display: grid; gap: 1rem; margin: 1.5rem 0; }
	.controls h2 { margin: 0; }
	.options { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 17rem), 1fr)); gap: .8rem; }
	.controls > .btn { justify-self: start; }
	summary { cursor: pointer; color: var(--accent); font-weight: 600; }
	.settings { display: grid; gap: .7rem; max-width: 36rem; margin-top: .8rem; }
	.settings .btn { justify-self: start; }
	.history { display: flex; gap: .5rem; overflow-x: auto; padding: .4rem 0; }
	.history button { padding: .7rem .9rem; background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-md); color: var(--text); white-space: nowrap; cursor: pointer; }
	.history button.active { border-color: var(--accent); }
	.issue { margin-top: 1rem; padding: clamp(1rem, 2.5vw, 2rem); border: 1px solid var(--line); border-radius: var(--r-lg); background: var(--surface); }
	.issue h2 { font-size: 1.6rem; }
	.issue h3 { margin-top: 2rem; }
	.stories ul { list-style: none; padding: 0; display: grid; gap: .8rem; }
	.stories li { padding: 1rem; border: 1px dashed var(--line-strong); border-radius: var(--r-md); }
	.story-title { font-weight: 750; margin: 0 0 .3rem; }
	.story-links { display: flex; gap: .9rem; flex-wrap: wrap; }
	.findings { list-style: none; padding: 0; display: grid; gap: .8rem; }
	.findings li { padding: 1rem; border: 1px solid var(--line); border-radius: var(--r-md); overflow-wrap: anywhere; }
	.findings li > a:first-child { font-weight: 750; font-size: 1.05rem; }
	.findings p { margin: .4rem 0; }
</style>
