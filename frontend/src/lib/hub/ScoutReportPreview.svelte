<script lang="ts">
	import { onMount } from 'svelte';
	import { focusable } from '$lib/nav';
	import { session } from '$lib/stores/session.svelte';
	import { hub, ago } from './api';
	import type { ScoutReport } from './types';
	let enabled = $state(false);
	let issue = $state<ScoutReport | null>(null);
	onMount(() => {
		if (!session.hubAllowed) return;
		void hub.scoutReportPreview().then((result) => { enabled = result.enabled; issue = result.issue; }).catch(() => {});
	});
</script>

{#if enabled}
	<section class="report-preview" aria-label="Scout Report preview">
		<div><p class="eyebrow">YOUR RESEARCH TEAM</p><h2>Scout Report</h2>
			{#if issue}<p>{issue.sections.findings.length} new findings · {issue.sections.runs.count} curator runs · {ago(issue.created_at)}</p>
			{:else}<p>Your first report is waiting to be created.</p>{/if}
		</div>
		<a class="btn" href="/scout-report" use:focusable={{ group: 'report-preview' }}>Open Scout Report →</a>
	</section>
{/if}
<style>
	.report-preview { margin: 1.5rem var(--gutter); padding: 1.2rem 1.4rem; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 1rem; border: 1px solid var(--line-strong); border-radius: var(--r-lg); background: var(--surface); }
	.report-preview h2, .report-preview p { margin: .25rem 0; }
	.eyebrow { color: var(--accent); font-size: .7rem; font-weight: 800; letter-spacing: .12em; }
</style>
