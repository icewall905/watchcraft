<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { focusable } from '$lib/nav';
	import TextField from '$lib/components/form/TextField.svelte';
	import { hub } from '$lib/hub/api';
	import { emptyCurator, errMsg } from '$lib/hub/ui';
	import CuratorForm from '$lib/hub/CuratorForm.svelte';
	import ErrorBox from '$lib/hub/ErrorBox.svelte';
	import { toast } from '$lib/stores/toast.svelte';
	import type { CuratorInput, Template } from '$lib/hub/types';

	type Suggestion = { provider: string; url: string; name: string };
	let goal = $state(page.url.searchParams.get('goal') ?? '');
	let templates = $state<Template[]>([]);
	let tplLoading = $state(true);
	let drafting = $state(false);
	let error = $state('');
	let proposal = $state<CuratorInput | null>(null);
	let ai = $state(false);
	let fromTemplate = $state('');
	let suggestions = $state<Suggestion[]>([]);
	let providerKeys = $state<string[]>([]);

	onMount(async () => {
		try { templates = await hub.templates(); } catch { /* optional */ } finally { tplLoading = false; }
		const key = page.url.searchParams.get('template');
		const t = templates.find((x) => x.key === key);
		if (t) useTemplate(t);
	});

	async function draft() {
		if (!goal.trim()) { error = 'Describe what you would like WatchCraft to find.'; return; }
		drafting = true; error = '';
		try {
			const d = await hub.draft(goal.trim(), 'watch');
			ai = d.ai; suggestions = d.source_suggestions ?? [];
			providerKeys = ((d as unknown as { provider_keys?: string[] }).provider_keys) ?? [];
			proposal = { ...emptyCurator(), ...d, kind: 'watch', instructions: d.instructions || goal.trim() };
			fromTemplate = '';
		} catch (e) { error = errMsg(e); } finally { drafting = false; }
	}
	function useTemplate(t: Template) {
		providerKeys = t.provider_keys; suggestions = []; ai = false; fromTemplate = t.name;
		proposal = { ...emptyCurator(t.kind === 'research' ? 'research' : 'watch'), name: t.name, description: t.description, icon: t.icon, queries: [...t.queries], keywords: [...t.keywords], content_types: [...t.content_types], interval_hours: t.interval_hours, instructions: goal.trim() || t.description };
	}
	async function approve(v: CuratorInput) {
		const c = await hub.createCurator(v);
		toast.success(`${c.name} created`);
		goto(`/curators/${c.id}`);
	}
</script>

<svelte:head><title>New curator · WatchCraft</title></svelte:head>

<div class="page narrow">
	<a class="small muted" href="/curators" use:focusable>← Curators</a>
	{#if !proposal}
		<h1>What would you like WatchCraft to find?</h1>
		<p class="muted">Say it the way you would to a research assistant. WatchCraft proposes sources, filters and a schedule. Nothing runs until you approve.</p>
		<form class="stack" onsubmit={(e) => { e.preventDefault(); draft(); }}>
			<TextField multiline rows={4} label="Your goal" bind:value={goal} placeholder="Tell me when new open-source AI model releases come out, and security advisories for nginx." autofocus />
			{#if error}<ErrorBox message={error} />{/if}
			<div class="hstack"><button class="btn primary big" type="submit" disabled={drafting || !goal.trim()} use:focusable>{drafting ? 'Drafting…' : 'Draft a curator'}</button></div>
		</form>

		<h2>Or start from a template</h2>
		{#if tplLoading}
			<div class="tpls">{#each [0, 1, 2, 3] as i (i)}<div class="skeleton" style="height:7rem"></div>{/each}</div>
		{:else if !templates.length}
			<p class="muted">No templates available.</p>
		{:else}
			<div class="tpls">
				{#each templates as t (t.key)}
					<button class="tpl" use:focusable onclick={() => useTemplate(t)}><span class="ic">{t.icon}</span><b>{t.name}</b><span class="muted small">{t.description}</span></button>
				{/each}
			</div>
		{/if}
	{:else}
		<h1>Review the proposal</h1>
		<p class="src" role="status">
			{#if fromTemplate}<span class="badge">Template</span> Based on “{fromTemplate}”. Adjust anything below.
			{:else if ai}<span class="badge ai">✦ AI draft</span> Written by your configured AI model. Check it before approving.
			{:else}<span class="badge">Heuristic draft</span> AI is not configured, so this was built from your wording with simple rules. Edit freely.{/if}
		</p>
		<CuratorForm initial={proposal} {providerKeys} {suggestions} submitLabel="Approve and create" onsave={approve} oncancel={() => (proposal = null)} />
	{/if}
</div>

<style>
	.narrow { max-width: 56rem; margin: 0 auto; }
	h1 { font-size: clamp(1.7rem, 3vw, 2.6rem); margin: .6rem 0; }
	h2 { margin-top: 2.2rem; font-size: 1.15rem; }
	.tpls { display: grid; grid-template-columns: repeat(auto-fill, minmax(15rem, 1fr)); gap: .8rem; }
	.tpl { display: grid; gap: .35rem; text-align: left; padding: 1rem; border: 1px solid var(--line); border-radius: var(--r-lg); background: var(--surface); color: var(--text); cursor: pointer; min-height: 2.75rem; align-content: start; }
	.tpl:hover { border-color: var(--accent); }
	.ic { font-size: 1.5rem; }
	.src { display: flex; align-items: center; gap: .6rem; flex-wrap: wrap; color: var(--muted); margin-bottom: 1.2rem; }
	.badge { font-size: .72rem; font-weight: 700; padding: .2rem .6rem; border-radius: var(--r-pill); border: 1px solid var(--line-strong); color: var(--text); }
	.badge.ai { color: var(--ai); border-color: var(--ai); }
</style>
