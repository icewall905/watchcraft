<script lang="ts">
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { focusable } from '$lib/nav';
	import Modal from '$lib/components/Modal.svelte';
	import { toast } from '$lib/stores/toast.svelte';
	import { hub } from '$lib/hub/api';
	import { errMsg } from '$lib/hub/ui';
	import ContentCard from '$lib/hub/ContentCard.svelte';
	import ErrorBox from '$lib/hub/ErrorBox.svelte';
	import type { TopicDetail } from '$lib/hub/types';

	const id = $derived(Number(page.params.id));
	let t = $state<TopicDetail | null>(null);
	let loading = $state(true);
	let error = $state('');
	let desc = $state('');
	let notes = $state('');
	let confirm = $state(false);

	async function load() {
		loading = true;
		try { const d = await hub.topic(id); t = d; desc = d.description; notes = d.notes; error = ''; } catch (e) { error = errMsg(e); } finally { loading = false; }
	}
	$effect(() => { void id; load(); });

	async function patch(body: Partial<TopicDetail>, msg?: string) {
		try { const r = await hub.updateTopic(id, body); if (t) t = { ...t, ...r, items: t.items, recent: t.recent, curators: t.curators, related: t.related }; if (msg) toast.success(msg); } catch (e) { error = errMsg(e); }
	}
	const saveDesc = () => t && desc !== t.description && patch({ description: desc }, 'Description saved');
	const saveNotes = () => t && notes !== t.notes && patch({ notes }, 'Notes saved');
	async function remove() {
		try { await hub.deleteTopic(id); goto('/knowledge'); } catch (e) { error = errMsg(e); confirm = false; }
	}
	async function unlink(itemId: number) {
		try { await hub.unlinkTopic(id, itemId); if (t) t = { ...t, items: t.items.filter((i) => i.id !== itemId), count: Math.max(0, t.count - 1) }; toast.success('Removed from topic'); } catch (e) { error = errMsg(e); }
	}
</script>

<svelte:head><title>{t?.name ?? 'Topic'} · WatchCraft</title></svelte:head>

<div class="page">
	<a class="small muted" href="/knowledge" use:focusable>← Knowledge</a>
	{#if loading}
		<div class="skeleton" style="height:5rem;margin:1rem 0"></div><div class="skeleton" style="height:14rem"></div>
	{:else if error && !t}
		<ErrorBox message={error} onretry={load} />
	{:else if t}
		{#if error}<ErrorBox message={error} onretry={load} />{/if}
		<header class="head">
			<div><p class="eyebrow">KNOWLEDGE TOPIC</p><h1>{t.name}</h1>
				{#if t.keywords.length}<div class="chips">{#each t.keywords as k}<span class="chip static">{k}</span>{/each}</div>{/if}</div>
			<div class="acts">
				<button class="btn" aria-pressed={t.pinned} use:focusable onclick={() => patch({ pinned: !t!.pinned }, t!.pinned ? 'Unpinned' : 'Pinned to sidebar')}>{t.pinned ? '📌 Pinned' : 'Pin'}</button>
				<button class="btn ghost danger" use:focusable onclick={() => (confirm = true)}>Delete</button>
			</div>
		</header>
		<p class="note">This topic is yours. Saved items and notes stay until you remove them. AI summaries always keep their sources.</p>

		<section class="two">
			<label class="field"><span class="label">Overview</span>
				<textarea class="input" rows="5" bind:value={desc} onblur={saveDesc} placeholder="What is this topic about?" use:focusable></textarea></label>
			<label class="field"><span class="label">Your notes</span>
				<textarea class="input" rows="5" bind:value={notes} onblur={saveNotes} placeholder="Private notes, saved when you leave the box." use:focusable></textarea></label>
		</section>

		<section>
			<h2>Saved knowledge ({t.items.length})</h2>
			{#if !t.items.length}
				<div class="empty"><p>Nothing saved yet. Open any item and add it to this topic.</p><a class="btn" href="/discover" use:focusable>Browse Discover</a></div>
			{:else}
				<div class="grid-cards">
					{#each t.items as _, i (t.items[i].id)}
						<div class="wrap"><ContentCard bind:item={t.items[i]} layout="card" /><button class="btn ghost small rm" use:focusable onclick={() => unlink(t!.items[i].id)}>Remove from topic</button></div>
					{/each}
				</div>
			{/if}
		</section>

		<section>
			<h2>Recent updates</h2>
			{#if !t.recent.length}<p class="muted small">No new matching finds yet.</p>{:else}
				<div class="grid-cards">{#each t.recent as _, i (t.recent[i].id)}<ContentCard bind:item={t.recent[i]} layout="card" />{/each}</div>
			{/if}
		</section>

		<section class="two">
			<div><h2>Relevant curators</h2>
				{#if !t.curators.length}<p class="muted small">No curator feeds this topic. Set one up in <a href="/curators" use:focusable>Curators</a>.</p>{:else}
					<ul class="plain">{#each t.curators as c (c.id)}<li><a href={`/curators/${c.id}`} use:focusable>{c.name}</a></li>{/each}</ul>{/if}</div>
			<div><h2>Related topics</h2>
				{#if !t.related.length}<p class="muted small">None yet.</p>{:else}
					<ul class="plain">{#each t.related as r (r.id)}<li><a href={`/knowledge/${r.id}`} use:focusable>{r.name}</a> <span class="faint small">{r.count}</span></li>{/each}</ul>{/if}</div>
		</section>
	{/if}
</div>

{#if confirm && t}
	<Modal title="Delete topic?" size="sm" onClose={() => (confirm = false)}>
		<p><b>{t.name}</b> and your notes will be removed. The saved items themselves stay in your library.</p>
		{#snippet footer()}
			<button class="btn ghost" use:focusable={{ autofocus: true }} onclick={() => (confirm = false)}>Cancel</button>
			<button class="btn danger" use:focusable onclick={remove}>Delete</button>
		{/snippet}
	</Modal>
{/if}

<style>
	.head { display: flex; justify-content: space-between; gap: 1rem; flex-wrap: wrap; align-items: flex-end; margin: 1rem 0 .6rem; }
	.eyebrow { font-size: .7rem; letter-spacing: .15em; color: var(--accent); font-weight: 700; margin: 0; }
	h1 { font-size: clamp(1.8rem, 3.2vw, 2.8rem); margin: .2rem 0; overflow-wrap: anywhere; }
	.acts { display: flex; gap: .5rem; }
	.chips { display: flex; gap: .4rem; flex-wrap: wrap; }
	.chip.static { cursor: default; }
	.note { color: var(--muted); font-size: .9rem; border-left: 3px solid var(--ai); padding-left: .8rem; max-width: 44rem; }
	section { margin-top: 2rem; }
	h2 { font-size: 1.2rem; }
	.two { display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; }
	.wrap { display: grid; gap: .3rem; }
	.rm { justify-self: start; }
	.plain { list-style: none; padding: 0; margin: 0; display: grid; gap: .4rem; }
	.faint { color: var(--faint); }
	@media (max-width: 760px) { .two { grid-template-columns: 1fr; } }
</style>
