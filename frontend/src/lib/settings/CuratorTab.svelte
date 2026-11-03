<script lang="ts">
	import { session } from '$lib/stores/session.svelte';
	import { focusable } from '$lib/nav';
	import { hub } from '$lib/hub/api';
	import type { HubLesson } from '$lib/hub/types';
	import { errMsg } from '$lib/hub/util';
	import Section from './Section.svelte';
	import SaveBar from './SaveBar.svelte';
	import { SettingsDraft } from './useSettings.svelte';
	import Toggle from '$lib/components/form/Toggle.svelte';
	const d = new SettingsDraft('hub_master');
	let lessons = $state<HubLesson[]>([]), summary = $state(''), error = $state('');
	let text = $state(''), scope = $state(''), polarity = $state<1 | -1>(1);
	let loading = $state(true), busy = $state(false), generation = 0;
	let edits = $state<Record<number, string>>({});
	async function load() {
		const mine = ++generation; loading = true; error = ''; lessons = []; summary = '';
		try { const r = await hub.preferences(); if (mine !== generation) return; lessons = r.lessons; summary = r.summary; edits = Object.fromEntries(r.lessons.map(l => [l.id, l.text])); }
		catch (e) { if (mine === generation) error = errMsg(e); }
		finally { if (mine === generation) loading = false; }
	}
	$effect(() => { void session.version; text = ''; void load(); });
	async function change(action: () => Promise<unknown>) {
		if (busy) return; const mine = generation; busy = true; error = '';
		try { await action(); if (mine === generation) { text = ''; await load(); } }
		catch (e) { if (mine === generation) error = errMsg(e); }
		finally { busy = false; }
	}
</script>

<Section title="Curator" intro="Your curator learns from what you save, read and set aside. You can change what it remembers.">
	{#if error}<p role="alert" class="err">{error}</p><button class="btn" use:focusable onclick={load}>Retry</button>{/if}
	{#if loading}<p role="status">Loading preferences…</p>{:else}
		{#if summary}<p class="summary">{summary}</p>{/if}
		<div class="lessons">
			{#each lessons as lesson (lesson.id)}
				<article class="lesson" data-nav-row={`lesson-${lesson.id}`}>
					<label>Preference<input use:focusable class="input" maxlength="300" bind:value={edits[lesson.id]} /></label>
					<p class="small muted">{lesson.scope || 'All content'} · {lesson.polarity === 1 ? 'Prefer' : 'Avoid'}{lesson.user_edited ? ' · Edited by you' : ''}</p>
					<div class="hstack">
						<button class="btn" disabled={busy || !edits[lesson.id]?.trim() || edits[lesson.id] === lesson.text} use:focusable onclick={() => change(() => hub.editPreference(lesson.id, { text: edits[lesson.id].trim() }))}>Save preference</button>
						<button class="btn ghost" aria-pressed={lesson.active} disabled={busy} use:focusable onclick={() => change(() => hub.editPreference(lesson.id, { active: !lesson.active }))}>{lesson.active ? 'Pause' : 'Enable'}</button>
						<button class="btn ghost" disabled={busy} use:focusable onclick={() => change(() => hub.editPreference(lesson.id, { polarity: lesson.polarity === 1 ? -1 : 1 }))}>{lesson.polarity === 1 ? 'Avoid instead' : 'Prefer instead'}</button>
						<button class="btn danger" disabled={busy} use:focusable onclick={() => change(() => hub.deletePreference(lesson.id))}>Delete</button>
					</div>
				</article>
			{:else}<p class="muted">No learned preferences yet. Add one below, or let the curator learn as you read.</p>{/each}
		</div>
		<form class="stack" onsubmit={(event) => { event.preventDefault(); void change(() => hub.addPreference({ text: text.trim(), scope, polarity })); }}>
			<label>New preference<input use:focusable class="input" bind:value={text} required maxlength="300" placeholder="I enjoy practical self-hosting guides" /></label>
			<div class="hstack"><label>Applies to<select use:focusable class="input" bind:value={scope}><option value="">All content</option><option value="news">News</option><option value="article">Articles</option><option value="meme">Memes</option><option value="project">Projects</option></select></label>
			<label>Direction<select use:focusable class="input" bind:value={polarity}><option value={1}>Prefer</option><option value={-1}>Avoid</option></select></label></div>
			<div><button class="btn" disabled={busy || !text.trim()} use:focusable>Add preference</button></div>
		</form>
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
	.lessons { display: grid; gap: var(--sp-5); }
	.lesson { display: grid; gap: var(--sp-3); border-bottom: 1px solid var(--rule); padding-bottom: var(--sp-5); }
	label { display: grid; gap: var(--sp-2); }
	.summary { max-width: var(--measure); white-space: pre-line; color: var(--ink-soft); }
	.schedule { border-top: 1px solid var(--rule); padding-top: var(--sp-6); margin-top: var(--sp-6); }
	.err { color: var(--bad); }
</style>
