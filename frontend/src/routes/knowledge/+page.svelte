<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { focusable } from '$lib/nav';
	import TextField from '$lib/components/form/TextField.svelte';
	import ChipInput from '$lib/components/form/ChipInput.svelte';
	import { hub } from '$lib/hub/api';
	import { errMsg } from '$lib/hub/ui';
	import ErrorBox from '$lib/hub/ErrorBox.svelte';
	import type { Topic } from '$lib/hub/types';

	let topics = $state<Topic[]>([]);
	let loading = $state(true);
	let error = $state('');
	let showNew = $state(false);
	let name = $state('');
	let description = $state('');
	let keywords = $state<string[]>([]);
	let busy = $state(false);
	let formErr = $state('');

	async function load() {
		try { topics = await hub.topics('knowledge'); error = ''; } catch (e) { error = errMsg(e); } finally { loading = false; }
	}
	onMount(load);

	async function create(e: SubmitEvent) {
		e.preventDefault();
		if (!name.trim()) { formErr = 'Name the topic.'; return; }
		busy = true; formErr = '';
		try { const t = await hub.createTopic({ name: name.trim(), description, keywords, kind: 'knowledge' }); goto(`/knowledge/${t.id}`); }
		catch (err) { formErr = errMsg(err); } finally { busy = false; }
	}
</script>

<svelte:head><title>Knowledge · WatchCraft</title></svelte:head>

<div class="page">
	<header class="head">
		<div>
			<p class="eyebrow">EXPLORE</p>
			<h1>Knowledge</h1>
			<p class="muted">Long-lived topics you own. Unlike News, which fades, topics keep what you save, your notes and the sources behind everything. AI summaries always keep their sources.</p>
		</div>
		<button class="btn primary big" use:focusable onclick={() => (showNew = !showNew)}>{showNew ? 'Close' : '＋ New topic'}</button>
	</header>

	{#if showNew}
		<form class="panel stack form" onsubmit={create}>
			<TextField label="Topic name" bind:value={name} placeholder="Home battery storage" autofocus group="kn" />
			<TextField label="Description" multiline rows={3} bind:value={description} placeholder="What do you want to understand or keep track of?" group="kn" />
			<ChipInput label="Keywords" hint="New finds matching these can be linked to the topic." bind:values={keywords} group="kn-k" />
			{#if formErr}<ErrorBox message={formErr} />{/if}
			<div><button class="btn primary" type="submit" disabled={busy} use:focusable>{busy ? 'Creating…' : 'Create topic'}</button></div>
		</form>
	{/if}

	{#if error}<ErrorBox message={error} onretry={load} />{/if}
	{#if loading}
		<div class="grid" aria-busy="true">{#each [0, 1, 2, 3] as i (i)}<div class="skeleton" style="height:8rem"></div>{/each}</div>
	{:else if !topics.length && !error}
		<section class="empty"><span class="bigic">📚</span><h2>No topics yet.</h2><p>Create a topic, then save items from Discover or Research into it.</p><button class="btn primary" use:focusable onclick={() => (showNew = true)}>Create your first topic</button></section>
	{:else}
		<div class="grid">
			{#each topics as t (t.id)}
				<a class="card" href={`/knowledge/${t.id}`} use:focusable>
					<div class="hstack"><b>{t.name}</b>{#if t.pinned}<span title="Pinned">📌</span>{/if}</div>
					{#if t.description}<p class="muted small desc">{t.description}</p>{/if}
					<span class="count">{t.count} saved</span>
					{#if t.keywords.length}<span class="small faint">{t.keywords.slice(0, 4).join(' · ')}</span>{/if}
				</a>
			{/each}
		</div>
	{/if}
</div>

<style>
	.head { display: flex; justify-content: space-between; align-items: flex-end; gap: 1rem; flex-wrap: wrap; margin-bottom: 1.5rem; }
	.head > div { max-width: 44rem; }
	.eyebrow { font-size: .7rem; letter-spacing: .15em; color: var(--accent); font-weight: 700; margin: 0; }
	h1 { font-size: clamp(1.8rem, 3vw, 2.8rem); margin: .2rem 0; }
	.form { max-width: 40rem; margin-bottom: 1.5rem; }
	.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 17rem), 1fr)); gap: 1rem; }
	.card { display: grid; gap: .45rem; padding: 1.2rem; border: 1px solid var(--line); border-radius: var(--r-lg); background: var(--surface); color: var(--text); align-content: start; min-height: 2.75rem; }
	.card:hover { border-color: var(--accent); }
	.card b { font-size: 1.1rem; }
	.card p { margin: 0; }
	.desc { display: -webkit-box; -webkit-line-clamp: 3; line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
	.count { color: var(--accent); font-weight: 700; font-size: .85rem; }
	.bigic { font-size: 3.5rem; }
	.faint { color: var(--faint); }
</style>
