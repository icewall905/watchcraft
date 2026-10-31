<script lang="ts">
	import { onMount } from 'svelte';
	import { focusable } from '$lib/nav';
	import { hub, ago } from '$lib/hub/api';
	import { errMsg } from '$lib/hub/util';
	import type { NewsGroup, Topic } from '$lib/hub/types';
	import ContentCard from '$lib/hub/ContentCard.svelte';

	const ranges = [[1, 'Today'], [7, 'This week'], [30, 'Month']] as const;
	let days = $state<1 | 7 | 30>(1);
	let topic = $state<number | null>(null);
	let publisher = $state('');
	let groups = $state<NewsGroup[]>([]);
	let topics = $state<Topic[]>([]);
	let loading = $state(true);
	let error = $state('');
	let open = $state<Record<string, boolean>>({});
	let follow = $state('');
	let following = $state(false);
	let topicError = $state('');
	let seq = 0;

	async function load() {
		const id = ++seq;
		loading = true; error = '';
		try {
			const r = await hub.news({ days, topic: topic ?? undefined });
			if (id !== seq) return;
			groups = r.groups; topics = r.topics;
		} catch (e) { if (id === seq) error = errMsg(e); }
		finally { if (id === seq) loading = false; }
	}
	onMount(load);
	function pick(d: 1 | 7 | 30) { days = d; load(); }
	function pickTopic(id: number | null) { topic = topic === id ? null : id; load(); }

	const publishers = $derived([...new Set(groups.flatMap((g) => g.items.map((i) => i.publisher).filter(Boolean)))].sort());
	const visible = $derived(publisher ? groups.map((g) => ({ g, idx: groups.indexOf(g) })).filter(({ g }) => g.items.some((i) => i.publisher === publisher)) : groups.map((g, idx) => ({ g, idx })));

	async function addTopic(e: SubmitEvent) {
		e.preventDefault();
		const name = follow.trim();
		if (!name || following) return;
		following = true; topicError = '';
		try {
			const t = await hub.createTopic({ name, kind: 'news', keywords: name.split(',').map((s) => s.trim()).filter(Boolean) });
			topics = [...topics, t]; follow = '';
			load();
		} catch (err) { topicError = errMsg(err); }
		finally { following = false; }
	}
	async function mute(t: Topic) {
		try {
			const u = await hub.updateTopic(t.id, { muted: !t.muted });
			topics = topics.map((x) => (x.id === t.id ? { ...x, ...u } : x));
			load();
		} catch (err) { topicError = errMsg(err); }
	}
</script>

<svelte:head><title>News · WatchCraft</title></svelte:head>

<div class="page">
	<header class="head">
		<div><h1>News</h1><p class="muted">Stories your curators gathered, clustered by story.</p></div>
		<a class="btn primary" href="/briefings?new=1" use:focusable>Generate briefing</a>
	</header>

	<div class="tabs" role="tablist" aria-label="Time range">
		{#each ranges as [d, name]}
			<button role="tab" aria-selected={days === d} class="chip tab" class:on={days === d} use:focusable onclick={() => pick(d)}>{name}</button>
		{/each}
	</div>

	<section class="topics" aria-label="Followed topics">
		<div class="hstack">
			{#each topics as t (t.id)}
				<span class="tchip" class:muted-t={t.muted}>
					<button class="chip" class:on={topic === t.id} aria-pressed={topic === t.id} use:focusable onclick={() => pickTopic(t.id)}>{t.name}{#if t.muted} (muted){/if}</button>
					<button class="btn ghost sm" use:focusable aria-label={`${t.muted ? 'Unmute' : 'Mute'} ${t.name}`} onclick={() => mute(t)}>{t.muted ? 'Unmute' : 'Mute'}</button>
				</span>
			{/each}
			{#if !topics.length && !loading}<span class="faint small">No followed topics yet.</span>{/if}
		</div>
		<form class="hstack" onsubmit={addTopic}>
			<input class="input follow" bind:value={follow} placeholder="Topic or keywords, comma separated" aria-label="Topic to follow" maxlength="100" />
			<button class="btn" disabled={!follow.trim() || following} use:focusable>Follow topic</button>
			{#if publishers.length}
				<select class="input pub" bind:value={publisher} aria-label="Publisher">
					<option value="">All publishers</option>
					{#each publishers as p}<option value={p}>{p}</option>{/each}
				</select>
			{/if}
		</form>
		{#if topicError}<p class="err" role="alert">{topicError}</p>{/if}
	</section>

	{#if loading}
		<div class="stack" aria-busy="true" aria-label="Loading">{#each Array(4) as _}<div class="skeleton sk"></div>{/each}</div>
	{:else if error}
		<div class="empty" role="alert"><p>Couldn't load news: {error}</p><button class="btn primary" use:focusable onclick={load}>Retry</button></div>
	{:else if !visible.length}
		<div class="empty">
			<span class="big-emoji">📰</span><h2>No news {days === 1 ? 'today' : 'in this range'}</h2>
			<p>{publisher || topic ? 'Try removing the publisher or topic filter.' : 'Create a curator that watches news feeds and stories will cluster here.'}</p>
			{#if !publisher && !topic}<a class="btn primary" href="/curators/new" use:focusable>Create a news curator</a>{/if}
		</div>
	{:else}
		<div class="stories">
			{#each visible as { g, idx } (g.key)}
				{@const rest = g.items.length - 1}
				<section class="story" aria-label={g.title}>
					<div class="story-head">
						<h2>{g.title}</h2>
						<span class="muted small">{g.items.length} article{g.items.length === 1 ? '' : 's'} · {ago(g.latest)}</span>
					</div>
					<ContentCard bind:item={groups[idx].items[0]} layout="row" />
					{#if rest > 0}
						<button class="btn ghost" aria-expanded={!!open[g.key]} use:focusable onclick={() => (open[g.key] = !open[g.key])}>
							{open[g.key] ? 'Hide' : `${rest} more article${rest === 1 ? '' : 's'}`}
						</button>
						{#if open[g.key]}
							<div class="more">
								{#each g.items.slice(1) as _, j (g.items[j + 1].id)}
									<ContentCard bind:item={groups[idx].items[j + 1]} layout="compact" />
								{/each}
							</div>
						{/if}
					{/if}
				</section>
			{/each}
		</div>
	{/if}
</div>

<style>
	.page { display: grid; gap: 1rem; align-content: start; }
	.head { display: flex; justify-content: space-between; align-items: end; gap: 1rem; flex-wrap: wrap; }
	.tabs { display: flex; gap: .5rem; }
	.tab { min-height: 2.75rem; padding: .4rem 1.1rem; }
	.topics { display: grid; gap: .6rem; }
	.tchip { display: inline-flex; align-items: center; }
	.tchip .chip { min-height: 2.75rem; }
	.muted-t .chip { opacity: .55; }
	.sm { min-height: 2.75rem; padding: .2rem .6rem; font-size: .8rem; }
	.follow { flex: 1; min-width: 14rem; max-width: 26rem; }
	.pub { width: auto; min-width: 11rem; background: rgba(10, 8, 25, .85); }
	.stories { display: grid; gap: 1.2rem; max-width: 62rem; }
	.story { display: grid; gap: .3rem; }
	.story-head { display: flex; align-items: baseline; justify-content: space-between; gap: .75rem; }
	.story-head h2 { font-size: 1.15rem; margin: 0; }
	.more { display: grid; gap: .5rem; padding-left: 1.2rem; border-left: 2px solid var(--line-strong); }
	.sk { height: 9rem; }
	.err { color: var(--bad); font-size: .85rem; }
	@media (max-width: 760px) { .tabs { overflow-x: auto; } .follow { min-width: 0; max-width: none; } .more { padding-left: .6rem; } .story-head { display: grid; gap: .1rem; } }
</style>
