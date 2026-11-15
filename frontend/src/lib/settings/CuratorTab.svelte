<script lang="ts">
	import { session } from '$lib/stores/session.svelte';
	import { focusable } from '$lib/nav';
	import { errMsg } from '$lib/hub/util';
	import Section from './Section.svelte';
	import SaveBar from './SaveBar.svelte';
	import { SettingsDraft } from './useSettings.svelte';
	import Toggle from '$lib/components/form/Toggle.svelte';
	import KnowledgePanel from './curator/KnowledgePanel.svelte';
	import KnowledgeChat from './curator/KnowledgeChat.svelte';
	import TeachingList from './curator/TeachingList.svelte';
	import History from './curator/History.svelte';
	import { SECTIONS, curator, surfaceIcon, surfaceLabel, type ChatMessage, type Knowledge, type Polarity, type SectionKey, type Snapshot } from './curator/api';
	const d = new SettingsDraft('hub_master');

	let knowledge = $state<Knowledge | null>(null), messages = $state<ChatMessage[]>([]);
	let loading = $state(true), error = $state(''), generation = 0;
	let chatDraft = $state(''), chat = $state<KnowledgeChat>();
	let showHistory = $state(false), surface = $state(''), refresh = $state(0);
	let mergedOpen = $state(false), rejectedOpen = $state(false);
	let text = $state(''), section = $state<SectionKey>('overall'), polarity = $state<Polarity>(1), teaching = $state(false);

	async function load() {
		const mine = ++generation; loading = true; error = ''; knowledge = null; messages = [];
		try {
			const [k, c] = await Promise.all([curator.knowledge(), curator.chat(false)]);
			if (mine !== generation) return;
			knowledge = k; messages = c.messages;
		} catch (e) { if (mine === generation) error = errMsg(e); }
		finally { if (mine === generation) loading = false; }
	}
	/** Re-read version, history and pending counts without blanking the page. */
	async function reloadKnowledge() {
		const mine = generation;
		try { const k = await curator.knowledge(); if (mine === generation) knowledge = k; } catch { /* keep what is shown */ }
	}
	$effect(() => { void session.version; text = ''; chatDraft = ''; surface = ''; showHistory = false; void load(); });

	function changed(s: Snapshot) {
		if (knowledge) knowledge = { ...knowledge, version: s.version, sections: s.sections };
		void reloadKnowledge();
	}
	function teachingsChanged() { refresh++; void reloadKnowledge(); }
	/** A reject can store the user's own message too, so re-read the whole thread. */
	async function reloadChat() {
		const mine = generation;
		try { const c = await curator.chat(false); if (mine === generation) messages = c.messages; } catch { /* keep what is shown */ }
	}
	function wrong(statement: string) { chatDraft = `That's wrong: "${statement}"`; void chat?.focus(); }
	async function teach(event: SubmitEvent) {
		event.preventDefault();
		if (teaching || text.trim().length < 3) return;
		const mine = generation;
		teaching = true;
		try { await curator.teach({ text: text.trim(), section, polarity }); if (mine === generation) { text = ''; teachingsChanged(); } }
		catch { /* the request already showed the error */ }
		finally { teaching = false; }
	}
	const pendingTotal = $derived(Object.values(knowledge?.pending ?? {}).reduce((a, b) => a + (b ?? 0), 0));
	const surfaces = $derived(Object.entries(knowledge?.pending ?? {}).filter(([, n]) => n));
</script>

<Section title="Curator" intro="What your curator has learned from what you watch, read, save and set aside — and why. Each night it weaves new lessons into what it knows. Correct it any time.">
	{#if error}<p role="alert" class="err">{error}</p><div><button class="btn" use:focusable onclick={load}>Retry</button></div>{/if}
	{#if loading}<p role="status">Loading what your curator knows…</p>
	{:else if knowledge}
		<div class="layout">
			<div class="main stack">
				{#if showHistory}
					<History history={knowledge.history} current={knowledge.version?.id ?? null} onclose={() => (showHistory = false)}
						onrestored={() => { showHistory = false; void reloadKnowledge(); }} />
				{/if}
				<KnowledgePanel {knowledge} onchange={changed} onwrong={wrong} onhistory={() => (showHistory = !showHistory)} />

				<section class="stack block" aria-labelledby="curator-pending">
					<h3 id="curator-pending">Learned since last night</h3>
					{#if surfaces.length}
						<div class="hstack chips" role="group" aria-label="Filter by where it was learned" data-nav-row="pending-filter">
							<button class="chip" class:on={!surface} aria-pressed={!surface} use:focusable onclick={() => (surface = '')}>All {pendingTotal}</button>
							{#each surfaces as [key, n] (key)}
								<button class="chip" class:on={surface === key} aria-pressed={surface === key} use:focusable onclick={() => (surface = key)}>{surfaceIcon(key)} {surfaceLabel(key)} {n}</button>
							{/each}
						</div>
					{/if}
					<TeachingList status="pending" {surface} {refresh} onchanged={teachingsChanged} onmessage={reloadChat} />
				</section>

				<details class="block" ontoggle={(e) => (mergedOpen = e.currentTarget.open)}>
					<summary use:focusable>Merged into what I know</summary>
					{#if mergedOpen}<TeachingList status="merged" {refresh} />{/if}
				</details>
				<details class="block" ontoggle={(e) => (rejectedOpen = e.currentTarget.open)}>
					<summary use:focusable>Rejected</summary>
					{#if rejectedOpen}<TeachingList status="rejected" {refresh} onchanged={teachingsChanged} />{/if}
				</details>

				<form class="stack block" onsubmit={teach}>
					<h3>Tell the curator directly</h3>
					<label class="field">Rule<input use:focusable class="input" bind:value={text} required minlength="3" maxlength="300" placeholder="I enjoy practical self-hosting guides" /></label>
					<div class="hstack" data-nav-row="teach-options">
						<label class="field">Applies to<select aria-label="Applies to" use:focusable class="input" bind:value={section}>{#each SECTIONS as s (s.key)}<option value={s.key}>{s.title}</option>{/each}</select></label>
						<label class="field">Direction<select aria-label="Direction" use:focusable class="input" bind:value={polarity}><option value={1}>Prefer</option><option value={-1}>Avoid</option></select></label>
					</div>
					<div><button class="btn" disabled={teaching || text.trim().length < 3} use:focusable>Teach the curator</button></div>
				</form>
			</div>
			<aside class="side"><KnowledgeChat bind:this={chat} bind:messages bind:draft={chatDraft} onapplied={(s) => { changed(s); refresh++; }} /></aside>
		</div>
	{/if}
	{#if session.isAdmin}
		<div class="schedule stack">
			<h3>Nightly selection</h3>
			<Toggle label="Run the master curator" hint="A light pass after retrieval and a fuller selection each night." bind:checked={d.value.enabled} />
			<label>Nightly time<input use:focusable class="input" type="time" bind:value={d.value.time} /></label>
			<p class="small muted">Uses the server's memory timezone: {session.settings?.memory.timezone || 'UTC'}.</p>
			<SaveBar dirty={d.dirty} busy={d.busy} onsave={() => d.save('Curator schedule saved')} onreset={() => d.reset()} />
		</div>
	{/if}
</Section>
<style>
	.layout { display: grid; gap: 1.5rem; grid-template-columns: minmax(0, 1fr) minmax(0, 24rem); align-items: start; }
	.main, .side { min-width: 0; }
	.side { position: sticky; top: calc(var(--topbar-h) + 1rem); }
	@media (max-width: 900px) {
		.layout { grid-template-columns: minmax(0, 1fr); }
		.side { position: static; }
	}
	.block { border-top: 1px solid var(--line); padding-top: 1.25rem; }
	h3 { margin: 0; }
	summary { cursor: pointer; font-weight: 600; padding: 0.25rem 0; }
	details[open] > summary { margin-bottom: 0.75rem; }
	.chips { gap: 0.4rem; }
	label { display: grid; gap: 0.4rem; }
	.field { flex: 1 1 10rem; }
	.schedule { border-top: 1px solid var(--line); padding-top: 1.5rem; margin-top: 1.5rem; }
	.err { color: var(--bad); }
</style>
