<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { focusable } from '$lib/nav';
	import Modal from '$lib/components/Modal.svelte';
	import TextField from '$lib/components/form/TextField.svelte';
	import Select from '$lib/components/form/Select.svelte';
	import { toast } from '$lib/stores/toast.svelte';
	import { hub, ago } from '$lib/hub/api';
	import { errMsg } from '$lib/hub/ui';
	import Cited from '$lib/hub/Cited.svelte';
	import ErrorBox from '$lib/hub/ErrorBox.svelte';
	import type { Briefing, Curator, Issue, Topic } from '$lib/hub/types';

	let briefings = $state<Briefing[]>([]);
	let curators = $state<Curator[]>([]);
	let topics = $state<Topic[]>([]);
	let loading = $state(true);
	let error = $state('');
	type Draft = { id?: number; name: string; curator_ids: number[]; topic_ids: number[]; frequency: Briefing['frequency']; length: Briefing['length'] };
	let editing = $state<Draft | null>(page.url.searchParams.get('new') === '1' ? { name: '', curator_ids: [], topic_ids: [], frequency: 'daily', length: 'concise' } : null);
	let formErr = $state('');
	let saving = $state(false);
	let open = $state<number | null>(null);
	let issues = $state<Record<number, Issue[]>>({});
	let issueBusy = $state<number | null>(null);
	let confirm = $state<Briefing | null>(null);

	async function load() {
		try { [briefings, curators, topics] = await Promise.all([hub.briefings(), hub.curators(), hub.topics('knowledge')]); error = ''; } catch (e) { error = errMsg(e); } finally { loading = false; }
	}
	onMount(load);

	const toggleIn = (arr: number[], v: number) => (arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);

	async function save(e: SubmitEvent) {
		e.preventDefault();
		if (!editing) return;
		if (!editing.name?.trim()) { formErr = 'Name the briefing.'; return; }
		if (!editing.curator_ids?.length && !editing.topic_ids?.length) { formErr = 'Pick at least one curator or topic.'; return; }
		saving = true; formErr = '';
		try {
			const body = { name: editing.name.trim(), curator_ids: editing.curator_ids, topic_ids: editing.topic_ids, frequency: editing.frequency, length: editing.length };
			if (editing.id) await hub.updateBriefing(editing.id, body); else await hub.createBriefing(body);
			editing = null; toast.success('Briefing saved'); await load();
		} catch (err) { formErr = errMsg(err); } finally { saving = false; }
	}
	async function toggleIssues(b: Briefing) {
		if (open === b.id) { open = null; return; }
		open = b.id;
		try { issues[b.id] = await hub.issues(b.id); } catch (e) { error = errMsg(e); }
	}
	async function generate(b: Briefing) {
		issueBusy = b.id;
		try {
			const i = await hub.generateBriefing(b.id);
			issues[b.id] = [i, ...(issues[b.id] ?? [])]; open = b.id; toast.success('Issue generated'); await load();
		} catch (e) { error = errMsg(e); } finally { issueBusy = null; }
	}
	async function remove() {
		if (!confirm) return;
		const b = confirm;
		try { await hub.deleteBriefing(b.id); briefings = briefings.filter((x) => x.id !== b.id); toast.success('Briefing deleted'); } catch (e) { error = errMsg(e); } finally { confirm = null; }
	}
</script>

<svelte:head><title>Briefings · WatchCraft</title></svelte:head>

<div class="page">
	<header class="head">
		<div><p class="eyebrow">EXPLORE</p><h1>Briefings</h1><p class="muted">A readable digest of what your curators and topics found. Every claim cites the item behind it.</p></div>
		<button class="btn primary big" use:focusable onclick={() => (editing = { name: '', curator_ids: [], topic_ids: [], frequency: 'daily', length: 'concise' })}>＋ New briefing</button>
	</header>

	{#if error}<ErrorBox message={error} onretry={load} />{/if}

	{#if editing}
		<form class="panel stack form" onsubmit={save}>
			<h2>{editing.id ? 'Edit briefing' : 'New briefing'}</h2>
			<TextField label="Name" bind:value={editing.name} placeholder="Morning security digest" autofocus group="br" />
			<div class="field"><span class="label">Curators</span>
				{#if !curators.length}<span class="hint">No curators yet. <a href="/curators/new" use:focusable>Create one</a>.</span>{/if}
				<div class="opts">{#each curators as c (c.id)}<label class="opt"><input type="checkbox" checked={editing.curator_ids?.includes(c.id)} onchange={() => editing && (editing.curator_ids = toggleIn(editing.curator_ids, c.id))} use:focusable /> {c.icon} {c.name}</label>{/each}</div></div>
			<div class="field"><span class="label">Knowledge topics</span>
				{#if !topics.length}<span class="hint">No topics yet.</span>{/if}
				<div class="opts">{#each topics as t (t.id)}<label class="opt"><input type="checkbox" checked={editing.topic_ids?.includes(t.id)} onchange={() => editing && (editing.topic_ids = toggleIn(editing.topic_ids, t.id))} use:focusable /> {t.name}</label>{/each}</div></div>
			<Select label="Frequency" bind:value={editing.frequency} options={[{ value: 'manual', label: 'Manual' }, { value: 'daily', label: 'Daily' }, { value: 'weekly', label: 'Weekly' }]} group="br" />
			<Select label="Length" bind:value={editing.length} options={[{ value: 'concise', label: 'Concise' }, { value: 'detailed', label: 'Detailed' }]} group="br" />
			{#if formErr}<ErrorBox message={formErr} />{/if}
			<div class="hstack"><button class="btn primary" type="submit" disabled={saving} use:focusable>{saving ? 'Saving…' : 'Save briefing'}</button><button class="btn ghost" type="button" use:focusable onclick={() => (editing = null)}>Cancel</button></div>
		</form>
	{/if}

	{#if loading}
		<div class="skeleton" style="height:9rem;margin-bottom:1rem"></div><div class="skeleton" style="height:9rem"></div>
	{:else if !briefings.length && !error && !editing}
		<section class="empty"><span class="bigic">🗞️</span><h2>No briefings yet.</h2><p>Combine curators and topics into one digest, daily or weekly.</p></section>
	{:else}
		<ul class="list">
			{#each briefings as b (b.id)}
				<li class="card">
					<div class="hstack">
						<div class="info"><h2>{b.name}</h2>
							<p class="small muted">{b.frequency} · {b.length} · {b.curator_ids.length} curator{b.curator_ids.length === 1 ? '' : 's'}, {b.topic_ids.length} topic{b.topic_ids.length === 1 ? '' : 's'} · {b.last_generated ? `last ${ago(b.last_generated)}` : 'never generated'}{#if b.next_run && b.frequency !== 'manual'} · next {ago(b.next_run)}{/if}</p></div>
						<span class="spacer"></span>
						<div class="acts">
							<button class="btn primary" disabled={issueBusy === b.id} use:focusable onclick={() => generate(b)}>{issueBusy === b.id ? 'Generating…' : 'Generate now'}</button>
							<button class="btn" aria-expanded={open === b.id} use:focusable onclick={() => toggleIssues(b)}>History</button>
							<button class="btn ghost" use:focusable onclick={() => (editing = { ...b })}>Edit</button>
							<button class="btn ghost danger" use:focusable onclick={() => (confirm = b)}>Delete</button>
						</div>
					</div>
					{#if open === b.id}
						<div class="issues">
							{#if !issues[b.id]}<div class="skeleton" style="height:6rem"></div>
							{:else if !issues[b.id].length}<p class="muted">No issues yet. Generate one.</p>
							{:else}
								{#each issues[b.id] as i (i.id)}
									<article class="issue"><p class="small faint">{new Date(i.created_at * 1000).toLocaleString()} · {i.ai ? '✦ AI-written' : 'Plain digest'}</p><Cited text={i.content} itemIds={i.item_ids} /></article>
								{/each}
							{/if}
						</div>
					{/if}
				</li>
			{/each}
		</ul>
	{/if}
</div>

{#if confirm}
	<Modal title="Delete briefing?" size="sm" onClose={() => (confirm = null)}>
		<p><b>{confirm.name}</b> and all its issues will be removed.</p>
		{#snippet footer()}
			<button class="btn ghost" use:focusable={{ autofocus: true }} onclick={() => (confirm = null)}>Cancel</button>
			<button class="btn danger" use:focusable onclick={remove}>Delete</button>
		{/snippet}
	</Modal>
{/if}

<style>
	.head { display: flex; justify-content: space-between; align-items: flex-end; gap: 1rem; flex-wrap: wrap; margin-bottom: 1.5rem; }
	.eyebrow { font-size: .7rem; letter-spacing: .15em; color: var(--accent); font-weight: 700; margin: 0; }
	h1 { font-size: clamp(1.8rem, 3vw, 2.8rem); margin: .2rem 0; }
	.form { max-width: 44rem; margin-bottom: 1.5rem; } .form h2 { margin: 0; }
	.opts { display: flex; flex-wrap: wrap; gap: .5rem; }
	.opt { display: flex; gap: .55rem; align-items: center; min-height: 2.75rem; padding: .4rem .8rem; border: 1px solid var(--line); border-radius: var(--r-md); cursor: pointer; }
	.opt input { width: 1.2rem; height: 1.2rem; accent-color: var(--accent); }
	.list { list-style: none; padding: 0; margin: 0; display: grid; gap: 1rem; }
	.card { padding: 1.1rem 1.3rem; border: 1px solid var(--line); border-radius: var(--r-lg); background: var(--surface); }
	.card h2 { margin: 0; font-size: 1.15rem; } .card p { margin: .2rem 0 0; }
	.acts { display: flex; gap: .4rem; flex-wrap: wrap; }
	.issues { display: grid; gap: 1.2rem; margin-top: 1rem; padding-top: 1rem; border-top: 1px solid var(--line); }
	.issue { padding: 1rem 1.2rem; border-radius: var(--r-md); background: rgba(255,255,255,.03); }
	.bigic { font-size: 3.5rem; } .faint { color: var(--faint); }
</style>
