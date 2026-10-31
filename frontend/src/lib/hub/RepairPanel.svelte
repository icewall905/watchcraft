<script lang="ts">
	import { onMount } from 'svelte';
	import { focusable } from '$lib/nav';
	import { toast } from '$lib/stores/toast.svelte';
	import { hub } from './api';
	import { errMsg } from './ui';
	import { describeRules } from './util';
	import SampleList from './SampleList.svelte';
	import type { RepairProposal, RepairResult, Source, SourceTest } from './types';

	// Proposing changes nothing; only "Apply" saves rules, and only after a live test passes (or the user forces it).
	let { source, onchanged, onadded }: { source: Source; onchanged: (s: Source) => void; onadded: () => void } = $props();

	const STEP: Record<string, string> = { revalidate: 'Re-tested the current rules', alternatives: 'Looked for feeds and sitemaps', reinspect: 'Re-inspected the page structure', test: 'Tested proposed rules' };

	let data = $state<RepairResult | null>(null);
	let running = $state(false);
	let error = $state('');
	let busy = $state<string>('');
	let applied = $state<Record<number, { test: SourceTest; saved: boolean }>>({});
	let added = $state<Record<number, boolean>>({});

	async function run() {
		running = true; error = ''; applied = {};
		try { data = await hub.repairSource(source.id); } catch (e) { error = errMsg(e); } finally { running = false; }
	}
	onMount(run);

	async function apply(i: number, p: RepairProposal, force = false) {
		busy = `apply-${i}`; error = '';
		try {
			const r = await hub.saveWatcher(source.id, p.config, 'repair', force);
			applied[i] = { test: r.test, saved: r.saved };
			if (r.saved && r.source) { onchanged(r.source); toast.success(r.test.ok ? 'Repair applied' : 'Repair saved without a passing test'); }
		} catch (e) { error = errMsg(e); } finally { busy = ''; }
	}

	async function addAlternate(i: number) {
		const a = data?.alternates[i];
		if (!a) return;
		busy = `alt-${i}`; error = '';
		try { await hub.createSource({ provider: a.provider, url: a.url, name: a.name }); added[i] = true; toast.success('Source added'); onadded(); }
		catch (e) { error = errMsg(e); } finally { busy = ''; }
	}
	const pct = (n: number | null) => (n === null ? '–' : `${Math.round(n * 100)}%`);
</script>

<div class="panel-in" aria-label="Repair">
	<div class="hstack"><b>Repair</b><span class="spacer"></span>
		<button class="btn" disabled={running} use:focusable onclick={run}>{running ? 'Diagnosing…' : 'Run again'}</button>
	</div>
	{#if running && !data}<span class="small muted">Testing the current rules, looking for alternatives and re-inspecting the page…</span>{/if}
	{#if error}<div class="err" role="alert">{error}</div>{/if}
	{#if data}
		<ul class="steps">
			{#each data.steps as st, i (i)}
				<li class="small"><span class={st.ok ? 'okc' : 'bad'}>{st.ok ? '✓' : '✗'}</span> {STEP[st.step] ?? st.step}{#if st.detail} — <span class="muted">{st.detail}</span>{/if}</li>
			{/each}
		</ul>
		<div class="block">
			<span class="small"><b>Current rules</b> · <span class="faint">{describeRules(data.current.config)}</span></span>
			<SampleList result={data.current} label="Current rules" limit={3} />
		</div>
		{#if !data.proposals.length}<p class="small muted">No better rules were found. The site may be down, or it may need manual rules.</p>{/if}
		{#each data.proposals as p, i (i)}
			{@const c = p.comparison}
			<div class="block proposal">
				<span class="small"><b>Proposal {i + 1}</b> · {p.ok ? `${p.count} items` : 'failed test'} · typical {c.typical_count ?? '–'} · now {c.current_count ?? '–'} · overlap {pct(c.overlap)}</span>
				<code class="small rules">{describeRules(p.config)}</code>
				{#if p.sample.length}<span class="small">{p.sample.slice(0, 3).map((s) => s.title).join(' · ')}</span>{/if}
				{#if !p.ok}<span class="small bad">{p.error || 'This proposal extracted nothing.'}</span>{/if}
				{#if applied[i]}
					{#if applied[i].saved}<b class="small okc">Applied.</b>{/if}
					<SampleList result={applied[i].test} label="Live test" limit={3} />
					{#if !applied[i].saved}
						<span class="small bad">Not applied: the rules failed the live test.</span>
						<button class="btn" disabled={!!busy} use:focusable onclick={() => apply(i, p, true)}>Apply anyway</button>
					{/if}
				{:else}
					<div><button class="btn primary" disabled={!!busy} use:focusable onclick={() => apply(i, p)}>{busy === `apply-${i}` ? 'Applying…' : 'Apply'}</button></div>
				{/if}
			</div>
		{/each}
		{#if data.alternates.length}
			<b class="small">Other ways to follow this site</b>
			{#each data.alternates as a, i (i)}
				<div class="block">
					<span class="small"><b>{a.name || a.url}</b> · {a.provider === 'rss' ? 'RSS/Atom feed' : a.provider} · {a.count} items{a.validated ? ' · validated' : ''}</span>
					<span class="small faint rules">{a.url}</span>
					{#if a.sample.length}<span class="small">{a.sample.slice(0, 3).map((s) => s.title).join(' · ')}</span>{/if}
					<div>{#if added[i]}<b class="small okc">Added.</b>{:else}<button class="btn" disabled={!!busy} use:focusable onclick={() => addAlternate(i)}>{busy === `alt-${i}` ? 'Adding…' : 'Add as source'}</button>{/if}</div>
				</div>
			{/each}
		{/if}
	{/if}
</div>

<style>
	.panel-in { display: grid; gap: .5rem; margin-top: .3rem; padding: .7rem .8rem; border-radius: var(--r-md); background: rgba(255,255,255,.04); }
	.panel-in p { margin: 0; }
	.steps { list-style: none; margin: 0; padding: 0; display: grid; gap: .15rem; }
	.block { display: grid; gap: .2rem; padding: .5rem .6rem; border-radius: var(--r-md); border: 1px solid var(--line); }
	.rules { overflow-wrap: anywhere; white-space: normal; }
	.err { padding: .6rem .9rem; border-radius: var(--r-md); border: 1px solid rgba(255,92,122,.45); background: rgba(255,92,122,.1); color: #ffd1d9; }
	.bad { color: var(--bad); } .okc { color: var(--ok); }
</style>
