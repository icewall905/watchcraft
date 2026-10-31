<script lang="ts">
	import { onMount } from 'svelte';
	import { focusable } from '$lib/nav';
	import { session } from '$lib/stores/session.svelte';
	import { hub, ago } from './api';
	import type { ReportPreview } from './types';
	let enabled = $state(false);
	let preview = $state<ReportPreview | null>(null);
	onMount(() => {
		if (!session.hubAllowed) return;
		void hub.scoutReportPreview().then((result) => { enabled = result.enabled; preview = result; }).catch(() => {});
	});
	const headline = $derived.by(() => {
		const issue = preview?.issue;
		if (!issue) return null;
		const stories = issue.sections.stories ?? [];
		if (stories.length) return stories[0];
		const finding = issue.sections.findings[0];
		return finding ? { kind: 'finding', title: finding.title, text: '', links: [{ label: 'Show the finding', href: `/item/${finding.id}` }] } : null;
	});
</script>

{#if enabled}
	<section class="report-preview" aria-label="Scout Report preview">
		<div>
			<p class="eyebrow">YOUR RESEARCH TEAM</p><h2>Scout Report</h2>
				{#if preview?.issue}
				<p>{preview!.issue!.sections.findings.length} new findings · {preview!.issue!.sections.runs.count} curator runs · {ago(preview!.issue!.created_at)}</p>
				{#if preview!.issue!.sections.runs.failed}
					<p class="attention">{preview!.issue!.sections.runs.failed} run{preview!.issue!.sections.runs.failed === 1 ? '' : 's'} need attention</p>
				{/if}
				{#if preview!.issue!.sections.alerts.length}
					<p class="attention">{preview!.issue!.sections.alerts.length} alert{preview!.issue!.sections.alerts.length === 1 ? '' : 's'} need a look</p>
				{/if}
				{#if headline}
					<p class="story"><b>{headline.title}</b>{headline.text ? ` — ${headline.text.slice(0, 180)}` : ''}</p>
					{#each headline.links as link}
						<a class="small" href={link.href} use:focusable={{ group: 'report-preview' }}>{link.label} →</a>
					{/each}
				{/if}
			{:else}
				<p>Your first report is waiting to be created.</p>
			{/if}
			{#if preview?.frequency && preview!.frequency !== 'manual' && preview?.next_run}
				<p class="small muted">Next automatic report {ago(preview!.next_run!)}</p>
			{/if}
		</div>
		<a class="btn" href="/scout-report" use:focusable={{ group: 'report-preview', id: 'open-scout-report' }}>Open Scout Report →</a>
	</section>
{/if}
<style>
	.report-preview { margin: 1.5rem var(--gutter); padding: 1.2rem 1.4rem; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 1rem; border: 1px solid var(--line-strong); border-radius: var(--r-lg); background: var(--surface); }
	.report-preview h2, .report-preview p { margin: .25rem 0; }
	.eyebrow { color: var(--accent); font-size: .7rem; font-weight: 800; letter-spacing: .12em; }
	.story { max-width: 46rem; }
	.attention { color: var(--warn, #ffb547); font-weight: 600; }
</style>
