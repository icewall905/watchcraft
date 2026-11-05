<script lang="ts">
	import { onDestroy, tick, untrack } from 'svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { focusable } from '$lib/nav';
	import { hub, ago } from '$lib/hub/api';
	import { errMsg } from '$lib/hub/util';
	import type { Item, NewsGroup, Topic } from '$lib/hub/types';
	import { StoryCard } from '$lib/hub/calm';
	import LocalImage from '$lib/hub/LocalImage.svelte';
	import LiveFilter from '$lib/hub/LiveFilter.svelte';
	import { session } from '$lib/stores/session.svelte';

	const ranges = [[1, 'Today'], [7, 'This week'], [30, 'Month']] as const;
	let days = $state<1 | 7 | 30>(7);
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
	let pageNumber = $state(1);
	const pageSize = 20;
	let weeklyProgress = $state<{ target: number; available: number; shortfall: number; met: boolean; pending_review: number } | null>(null);
	let seq = 0;
	let controller: AbortController | null = null;
	let loadedProfileId: number | null = null;
	let loadedProfileVersion = -1;

	async function load() {
		const id = ++seq, profileId = session.profileId, version = session.version;
		if (session.profile?.pin_locked || session.needsLogin || profileId == null) {
			controller?.abort(); groups = []; topics = []; open = {}; topic = null; publisher = '';
			weeklyProgress = null; loading = false; error = ''; return;
		}
		controller?.abort();
		controller = new AbortController();
		const signal = controller.signal;
		if (profileId !== loadedProfileId || version !== loadedProfileVersion) {
			groups = []; topics = []; open = {}; publisher = ''; topic = null; topicError = ''; pageNumber = 1;
		}
		loading = true; error = '';
		try {
			const r = await hub.news({ days, topic: topic ?? undefined }, signal);
			if (id !== seq || profileId !== session.profileId || version !== session.version || session.profile?.pin_locked || session.needsLogin) return;
			groups = r.groups; topics = r.topics;
			weeklyProgress = r.supply ?? null;
			loadedProfileId = profileId; loadedProfileVersion = version;
			pageNumber = 1;
		} catch (e) { if (id === seq && !signal.aborted && profileId === session.profileId && version === session.version && !session.profile?.pin_locked && !session.needsLogin) error = errMsg(e); }
		finally { if (id === seq && profileId === session.profileId && version === session.version) { loading = false; controller = null; } }
	}
	$effect(() => { session.profileId; session.version; session.profile?.pin_locked; session.needsLogin; untrack(() => { void load(); }); });
	onDestroy(() => controller?.abort());
	function pick(d: 1 | 7 | 30) { days = d; pageNumber = 1; load(); }
	function pickTopic(id: number | null) { topic = topic === id ? null : id; pageNumber = 1; load(); }

	const publishers = $derived([...new Set(groups.flatMap((g) => g.items.map((i) => i.publisher).filter(Boolean)))].sort());
	// News loads every story in the range, so the filter runs here instead of a round trip.
	const terms = $derived((page.url.searchParams.get('q') ?? '').toLowerCase().split(/\s+/).filter(Boolean));
	const matches = (it: Item) => {
		if (!terms.length) return true;
		const tags = [...(it.state.tags ?? []), ...((it.meta?.tags as unknown[]) ?? [])].map((x) => String(x).toLowerCase());
		const aiSummary = typeof it.enrichment?.summary === 'string' ? it.enrichment.summary : '';
		const hay = [it.title, it.summary, aiSummary, it.publisher, it.author, ...tags].join(' ').toLowerCase();
		return terms.every((term) => hay.includes(term));
	};
	const visible = $derived(groups.map((g, idx) => ({ g, idx })).filter(({ g }) =>
		g.items.some((i) => (!publisher || i.publisher === publisher) && matches(i))
	));
	const totalPages = $derived(Math.max(1, Math.ceil(visible.length / pageSize)));
	const pageGroups = $derived(visible.slice((pageNumber - 1) * pageSize, pageNumber * pageSize));
	const visibleArticleCount = $derived(visible.reduce((n, { g }) => n + g.items.filter((i) => matches(i) && (!publisher || i.publisher === publisher)).length, 0));
	const pageStart = $derived(visible.length ? (pageNumber - 1) * pageSize + 1 : 0);
	const pageEnd = $derived(Math.min(pageNumber * pageSize, visible.length));
	let previousFilterKey = '';
	$effect(() => {
		const key = `${days}|${topic ?? ''}|${publisher}|${page.url.searchParams.get('q') ?? ''}`;
		if (previousFilterKey && previousFilterKey !== key) pageNumber = 1;
		previousFilterKey = key;
	});
	async function goToPage(next: number) {
		pageNumber = Math.max(1, Math.min(totalPages, next));
		await tick();
		const headline = document.querySelector<HTMLAnchorElement>('.stories .title a');
		headline?.focus({ preventScroll: true });
		headline?.scrollIntoView({ block: 'start', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
	}
	async function addTopic(e: SubmitEvent) {
		e.preventDefault();
		const name = follow.trim();
		if (!name || following || session.profile?.pin_locked || session.needsLogin) return;
		const profileId = session.profileId, version = session.version;
		following = true; topicError = '';
		try {
			const t = await hub.createTopic({ name, kind: 'news', keywords: name.split(',').map((s) => s.trim()).filter(Boolean) });
			if (profileId !== session.profileId || version !== session.version || session.profile?.pin_locked || session.needsLogin) return;
			topics = [...topics, t]; follow = '';
			load();
		} catch (err) { if (profileId === session.profileId && version === session.version && !session.profile?.pin_locked && !session.needsLogin) topicError = errMsg(err); }
		finally { following = false; }
	}
	async function mute(t: Topic) {
		if (session.profile?.pin_locked || session.needsLogin) return;
		const profileId = session.profileId, version = session.version;
		try {
			const u = await hub.updateTopic(t.id, { muted: !t.muted });
			if (profileId !== session.profileId || version !== session.version || session.profile?.pin_locked || session.needsLogin) return;
			topics = topics.map((x) => (x.id === t.id ? { ...x, ...u } : x));
			load();
		} catch (err) { if (profileId === session.profileId && version === session.version && !session.profile?.pin_locked && !session.needsLogin) topicError = errMsg(err); }
	}
</script>

<svelte:head><title>News · WatchCraft</title></svelte:head>

<div class="page">
	<header class="head">
		<div><h1>News</h1><p class="muted">A considered week of reporting, grouped by story.</p></div>
		<a class="btn primary" href="/briefings?new=1" use:focusable>Generate briefing</a>
	</header>

	<div class="tabs" role="tablist" aria-label="Time range">
		{#each ranges as [d, name]}
			<button role="tab" aria-selected={days === d} class="chip tab" class:on={days === d} use:focusable onclick={() => pick(d)}>{name}</button>
		{/each}
	</div>

	<LiveFilter placeholder="Filter stories by words or tags…" label="Filter news" count={loading ? null : visible.length} />
	{#if !loading && !error && (visible.length || (days === 7 && weeklyProgress))}
		<div class="edition-count" aria-live="polite">
			<p><strong>{visibleArticleCount}</strong> articles across <strong>{visible.length}</strong> stories</p>
			<span>{days === 7 && weeklyProgress
				? `${weeklyProgress.available} approved this week · ${weeklyProgress.met ? `${weeklyProgress.target}+ goal met` : `goal: ${weeklyProgress.target}`}${weeklyProgress.pending_review ? ` · ${weeklyProgress.pending_review} in review` : ''}`
				: days === 7 ? 'Weekly curation goal: at least 100 articles' : days === 1 ? 'Today’s coverage' : 'Monthly coverage'}</span>
		</div>
	{/if}

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
				<select class="input pub" bind:value={publisher} aria-label="Publisher" onchange={() => (pageNumber = 1)}>
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
			<p>{publisher || topic || terms.length ? 'Try removing the publisher, topic or text filter.' : 'Create a curator that watches news feeds and stories will cluster here.'}</p>
			{#if !publisher && !topic}<a class="btn primary" href="/curators/new" use:focusable>Create a news curator</a>{/if}
		</div>
	{:else}
		{#if totalPages > 1}
			<nav class="pages" aria-label="News pages">
				<span>Stories {pageStart}–{pageEnd} of {visible.length}</span>
				<div class="page-actions">
					<button class="btn ghost" disabled={pageNumber <= 1} use:focusable onclick={() => goToPage(pageNumber - 1)}>Previous</button>
					<span aria-live="polite">Page {pageNumber} of {totalPages}</span>
					<button class="btn ghost" disabled={pageNumber >= totalPages} use:focusable onclick={() => goToPage(pageNumber + 1)}>Next</button>
				</div>
			</nav>
		{/if}
		<div class="stories" aria-label="Curated stories">
			{#each pageGroups as { g } (g.key)}
				{@const selectedItems = g.items.filter((i) => (!publisher || i.publisher === publisher) && matches(i))}
				{@const rest = selectedItems.length - 1}
				{@const lead = selectedItems[0] ?? g.items[0]}
				{@const summary = (typeof lead.enrichment?.summary === 'string' && lead.enrichment.summary.trim()) ? lead.enrichment.summary : lead.summary}
				{@const href = g.story_id ? `/news/${g.story_id}` : `/item/${lead.id}`}
				<section class="story" aria-label={g.title}>
					{#if lead.local_image}
						<StoryCard {href} title={g.title} source={lead.publisher || lead.provider} {summary}
							more={rest} moreSources={selectedItems.slice(1).map((i) => i.publisher || i.provider).filter(Boolean)}>
							{#snippet image()}<LocalImage id={lead.id} alt={lead.title} />{/snippet}
						</StoryCard>
					{:else}
						<StoryCard {href} title={g.title} source={lead.publisher || lead.provider} {summary}
							more={rest} moreSources={selectedItems.slice(1).map((i) => i.publisher || i.provider).filter(Boolean)} />
					{/if}
					<p class="story-meta">{selectedItems.length} source{selectedItems.length === 1 ? '' : 's'} · Updated {ago(g.latest)}</p>
					{#if rest > 0}
						<button class="btn ghost" aria-expanded={!!open[g.key]} use:focusable onclick={() => (open[g.key] = !open[g.key])}>
							{open[g.key] ? 'Hide sources' : `${rest} more source${rest === 1 ? '' : 's'}`}
						</button>
						{#if open[g.key]}
							<div class="more">
								{#each selectedItems.slice(1) as item (item.id)}
								<a class="source-link" href={`/item/${item.id}`} use:focusable>{item.publisher || item.provider}: {item.title}</a>
								{/each}
							</div>
						{/if}
					{/if}
				</section>
			{/each}
		</div>
		{#if totalPages > 1}
			<nav class="pages bottom-pages" aria-label="News pages">
				<span>Stories {pageStart}–{pageEnd} of {visible.length}</span>
				<div class="page-actions">
					<button class="btn ghost" disabled={pageNumber <= 1} use:focusable onclick={() => goToPage(pageNumber - 1)}>Previous</button>
					<span>Page {pageNumber} of {totalPages}</span>
					<button class="btn ghost" disabled={pageNumber >= totalPages} use:focusable onclick={() => goToPage(pageNumber + 1)}>Next</button>
				</div>
			</nav>
		{/if}
	{/if}
</div>

<style>
	.page { display: grid; gap: var(--sp-5); align-content: start; }
	.head { display: flex; justify-content: space-between; align-items: end; gap: 1rem; flex-wrap: wrap; }
	.edition-count { display: flex; justify-content: space-between; align-items: baseline; gap: 1rem; max-width: 62rem; padding: .7rem 0; border-block: 1px solid var(--rule); color: var(--ink-soft); }
	.edition-count p { margin: 0; }
	.edition-count strong { color: var(--ink); font-variant-numeric: tabular-nums; }
	.edition-count span { color: var(--faint); font-size: var(--fs-sm); }
	.tabs { display: flex; gap: .5rem; }
	.tab { min-height: 2.75rem; padding: .4rem 1.1rem; }
	.topics { display: grid; gap: .6rem; }
	.tchip { display: inline-flex; align-items: center; }
	.tchip .chip { min-height: 2.75rem; }
	.muted-t .chip { opacity: .55; }
	.sm { min-height: 2.75rem; padding: .2rem .6rem; font-size: .8rem; }
	.follow { flex: 1; min-width: 14rem; max-width: 26rem; }
	.pub { width: auto; min-width: 11rem; background: rgba(10, 8, 25, .85); }
	.stories { display: grid; max-width: 62rem; }
	.story { display: grid; gap: .2rem; }
	.story-meta { flex: none; padding: .15rem .55rem; border-radius: var(--r-pill); background: var(--bg-2);
		color: var(--muted); font-size: .75rem; white-space: nowrap; }
	.more { display: grid; gap: .5rem; padding-left: 1.2rem; border-left: 2px solid var(--line-strong); }
	.pages { display: flex; justify-content: space-between; align-items: center; gap: 1rem; max-width: 62rem; padding: .5rem 0; color: var(--faint); font-size: var(--fs-sm); }
	.page-actions { display: flex; align-items: center; gap: .75rem; }
	.page-actions .btn { min-height: 2.5rem; }
	.bottom-pages { margin-top: .5rem; }
	.source-link { color: var(--ink-soft); font-size: var(--fs-sm); padding: .35rem 0; }
	.sk { height: 9rem; }
	.err { color: var(--bad); font-size: .85rem; }
	@media (max-width: 760px) {
		.tabs { overflow-x: auto; }
		.topics form { display: grid; grid-template-columns: minmax(0, 1fr) auto; }
		.follow { grid-column: 1 / -1; width: 100%; min-width: 0; max-width: none; }
		.pub { grid-column: 1 / -1; width: 100%; }
		.more { padding-left: .6rem; }
		.pages { align-items: flex-start; flex-direction: column; }
	}
	@media (max-width: 480px) { .edition-count { align-items: flex-start; flex-direction: column; gap: .2rem; } .page-actions { width: 100%; justify-content: space-between; } }
</style>
