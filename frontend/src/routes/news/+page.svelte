<script lang="ts">
	import { onDestroy, onMount, tick, untrack } from 'svelte';
	import { page } from '$app/state';
	import { beforeNavigate } from '$app/navigation';
	import { focusable } from '$lib/nav';
	import { hub, ago } from '$lib/hub/api';
	import { errMsg } from '$lib/hub/util';
	import type { Item, NewsGroup, Topic } from '$lib/hub/types';
	import { StoryCard } from '$lib/hub/calm';
	import LocalImage from '$lib/hub/LocalImage.svelte';
	import LiveFilter from '$lib/hub/LiveFilter.svelte';
	import { session } from '$lib/stores/session.svelte';
	import ArticleFeedback from '$lib/hub/ArticleFeedback.svelte';

	const ranges = [[1, 'Today'], [7, 'This week'], [30, 'Month']] as const;
	const modes = [['foryou', 'For you'], ['latest', 'Latest']] as const;
	let days = $state<1 | 7 | 30>(7);
	let mode = $state<'foryou' | 'latest'>('foryou');
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
	const step = 20;
	let shown = $state(step);
	let newCount = $state(0);
	let restoreScroll: number | null = null;
	// A visit is one sitting: coming back from an article keeps the same mix and place;
	// returning after a break (or asking for a fresh mix) ranks unseen stories first.
	const VISIT_KEY = 'watchcraft:news-visit';
	const VISIT_GAP_MS = 30 * 60 * 1000;
	type Visit = { seed: string; at: number; last: number; scroll?: number; shown?: number; profile?: number | null };
	const store = { get: (): Visit | null => { try { return JSON.parse(sessionStorage.getItem(VISIT_KEY) ?? 'null'); } catch { return null; } },
		set: (v: Visit) => { try { sessionStorage.setItem(VISIT_KEY, JSON.stringify(v)); } catch { /* storage unavailable */ } } };
	function startVisit(): Visit { const v = { seed: Math.random().toString(36).slice(2, 10), at: Date.now() / 1000, last: Date.now(), profile: session.profileId }; store.set(v); return v; }
	function currentVisit(): Visit {
		const v = store.get();
		if (v && v.profile === session.profileId && Date.now() - v.last < VISIT_GAP_MS) { v.last = Date.now(); store.set(v); return v; }
		return startVisit();
	}
	let visit: Visit = { seed: '', at: Date.now() / 1000, last: Date.now() };
	let weeklyProgress = $state<Awaited<ReturnType<typeof hub.news>>['supply'] | null>(null);
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
			groups = []; topics = []; open = {}; publisher = ''; topic = null; topicError = ''; shown = step;
			pendingSeen.clear(); sentSeen.clear();
		}
		visit = currentVisit();
		loading = true; error = '';
		try {
			const r = await hub.news({ days, topic: topic ?? undefined, mode, seed: visit.seed, visit_at: visit.at }, signal);
			if (id !== seq || profileId !== session.profileId || version !== session.version || session.profile?.pin_locked || session.needsLogin) return;
			groups = r.groups; topics = r.topics;
			weeklyProgress = r.supply ?? null;
			loadedProfileId = profileId; loadedProfileVersion = version;
			newCount = r.new_count ?? 0;
			shown = Math.max(step, visit.shown ?? step);
			restoreScroll = visit.scroll ?? null;
		} catch (e) { if (id === seq && !signal.aborted && profileId === session.profileId && version === session.version && !session.profile?.pin_locked && !session.needsLogin) error = errMsg(e); }
		finally { if (id === seq && profileId === session.profileId && version === session.version) { loading = false; controller = null; } }
	}
	$effect(() => { session.profileId; session.version; session.profile?.pin_locked; session.needsLogin; untrack(() => { void load(); }); });
	onDestroy(() => controller?.abort());
	function resetPlace() { shown = step; visit.scroll = 0; visit.shown = step; store.set(visit); }
	function pick(d: 1 | 7 | 30) { days = d; resetPlace(); load(); }
	function pickMode(m: 'foryou' | 'latest') { if (mode === m) return; mode = m; resetPlace(); load(); }
	function pickTopic(id: number | null) { topic = topic === id ? null : id; resetPlace(); load(); }
	async function freshMix() {
		await flushSeen();
		visit = startVisit(); mode = 'foryou'; shown = step;
		window.scrollTo({ top: 0 });
		await load();
	}
	beforeNavigate(() => { if (!visit.seed) return; visit.scroll = window.scrollY; visit.shown = shown; visit.last = Date.now(); store.set(visit); });

	// Impressions: a story counts as shown once most of it was on screen, so the next visit can lead with others.
	const pendingSeen = new Set<number>();
	const sentSeen = new Set<string>();
	let seenObserver: IntersectionObserver | null = null;
	let moreObserver: IntersectionObserver | null = null;
	let flushTimer: ReturnType<typeof setInterval> | null = null;
	async function flushSeen(keepalive = false) {
		// Impressions belong to the visit (and profile) that showed them, never to whoever is active at flush time.
		if (!pendingSeen.size || session.profile?.pin_locked || session.needsLogin || !visit.seed) return;
		if (visit.profile == null || visit.profile !== session.profileId) { pendingSeen.clear(); return; }
		const ids = [...pendingSeen].slice(0, 300); ids.forEach((i) => pendingSeen.delete(i));
		try { await hub.newsSeen(ids, visit.at, visit.profile, keepalive); } catch { /* best effort; ordering still works */ }
	}
	function observeSeen(node: HTMLElement, ids: number[]) {
		node.dataset.seen = ids.join(',');
		seenObserver?.observe(node);
		return { update(next: number[]) { node.dataset.seen = next.join(','); }, destroy() { seenObserver?.unobserve(node); } };
	}
	function observeMore(node: HTMLElement) { moreNode = node; moreObserver?.observe(node); return { destroy() { moreObserver?.unobserve(node); if (moreNode === node) moreNode = null; } }; }
	const onHide = () => { if (document.visibilityState === 'hidden') void flushSeen(true); };
	onMount(() => {
		if ('IntersectionObserver' in window) {
			seenObserver = new IntersectionObserver((entries) => {
				for (const e of entries) if (e.isIntersecting) {
					const key = `${visit.seed}:${(e.target as HTMLElement).dataset.seen}`;
					if (sentSeen.has(key)) continue;
					sentSeen.add(key);
					for (const id of ((e.target as HTMLElement).dataset.seen ?? '').split(',').map(Number).filter(Boolean)) pendingSeen.add(id);
				}
			}, { threshold: 0.6 });
			moreObserver = new IntersectionObserver((entries) => { if (entries.some((e) => e.isIntersecting)) showMore(); }, { rootMargin: '600px 0px' });
		}
		flushTimer = setInterval(() => void flushSeen(), 4000);
		document.addEventListener('visibilitychange', onHide);
		window.addEventListener('pagehide', onHide);
	});
	onDestroy(() => {
		void flushSeen(true);
		seenObserver?.disconnect(); moreObserver?.disconnect();
		if (flushTimer) clearInterval(flushTimer);
		if (typeof document !== 'undefined') { document.removeEventListener('visibilitychange', onHide); window.removeEventListener('pagehide', onHide); }
	});

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
	const pageGroups = $derived(visible.slice(0, shown));
	const visibleArticleCount = $derived(visible.reduce((n, { g }) => n + g.items.filter((i) => matches(i) && (!publisher || i.publisher === publisher)).length, 0));
	let previousFilterKey = '';
	$effect(() => {
		const key = `${days}|${mode}|${topic ?? ''}|${publisher}|${page.url.searchParams.get('q') ?? ''}`;
		if (previousFilterKey && previousFilterKey !== key) shown = step;
		previousFilterKey = key;
	});
	let moreNode: HTMLElement | null = null;
	function showMore() {
		if (shown >= visible.length) return;
		shown += step;
		// Re-arm the sentinel: if it is still on screen after growing, load the next batch too.
		void tick().then(() => { if (moreNode && moreObserver) { moreObserver.unobserve(moreNode); moreObserver.observe(moreNode); } });
	}
	// Returning from an article puts the reader back where they were in this visit's mix.
	$effect(() => {
		if (loading || restoreScroll == null || !pageGroups.length) return;
		const y = restoreScroll; restoreScroll = null;
		void tick().then(() => window.scrollTo({ top: y }));
	});
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
		<div><h1>News</h1><p class="muted">{mode === 'foryou' ? 'A fresh mix of the week’s reporting every visit, grouped by story.' : 'The week’s reporting, newest first, grouped by story.'}</p></div>
		<div class="head-actions">
			<button class="btn" use:focusable onclick={freshMix} disabled={loading}>Fresh mix</button>
			<a class="btn primary" href="/briefings?new=1" use:focusable>Generate briefing</a>
		</div>
	</header>

	<div class="controls">
	<div class="tabs" role="tablist" aria-label="Feed order">
		{#each modes as [m, name]}
			<button role="tab" aria-selected={mode === m} class="chip tab" class:on={mode === m} use:focusable onclick={() => pickMode(m)}>{name}</button>
		{/each}
	</div>
	<div class="tabs" role="tablist" aria-label="Time range">
		{#each ranges as [d, name]}
			<button role="tab" aria-selected={days === d} class="chip tab" class:on={days === d} use:focusable onclick={() => pick(d)}>{name}</button>
		{/each}
	</div>
	</div>

	<LiveFilter placeholder="Filter stories by words or tags…" label="Filter news" count={loading ? null : visible.length} />
	{#if !loading && !error && (visible.length || (days === 7 && weeklyProgress))}
		<div class="edition-count" aria-live="polite">
			<p><strong>{visibleArticleCount}</strong> articles across <strong>{visible.length}</strong> stories{#if mode === 'foryou' && newCount}{' · '}<strong class="new-count">{newCount}</strong> new since your last visit{/if}</p>
			<span>{days === 7 && weeklyProgress
				? `${weeklyProgress.available} approved this week · ${weeklyProgress.available >= weeklyProgress.target ? `${weeklyProgress.target}+ goal met` : `goal: ${weeklyProgress.target}`}${weeklyProgress.pending_review ? ` · ${weeklyProgress.pending_review} in review` : ''}`
				: days === 7 ? 'Weekly curation goal: at least 100 articles' : days === 1 ? 'Today’s coverage' : 'Monthly coverage'}</span>
		</div>
		{#if days === 7 && weeklyProgress?.ready_count !== undefined}
			<p class="small muted" aria-label="Article supply">{weeklyProgress.ready_count} / {weeklyProgress.ready_target ?? 100} ready to read · Cleaned local text and AI summary · {weeklyProgress.candidate_count ?? weeklyProgress.available} / {weeklyProgress.candidate_target ?? 300} approved candidates for refills.</p>
		{/if}
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
				<select class="input pub" bind:value={publisher} aria-label="Publisher" onchange={() => (shown = step)}>
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
		<div class="stories" aria-label="Curated stories">
			{#each pageGroups as { g } (g.key)}
				{@const selectedItems = g.items.filter((i) => (!publisher || i.publisher === publisher) && matches(i))}
				{@const rest = selectedItems.length - 1}
				{@const lead = selectedItems[0] ?? g.items[0]}
				{@const summary = (typeof lead.enrichment?.summary === 'string' && lead.enrichment.summary.trim()) ? lead.enrichment.summary : lead.summary}
				{@const href = g.story_id ? `/news/${g.story_id}` : `/item/${lead.id}`}
				<section class="story" aria-label={g.title} use:observeSeen={selectedItems.map((i) => i.id)}>
					{#if lead.local_image}
						<StoryCard {href} title={g.title} source={lead.publisher || lead.provider} {summary}
							more={rest} moreSources={selectedItems.slice(1).map((i) => i.publisher || i.provider).filter(Boolean)}>
							{#snippet image()}<LocalImage id={lead.id} alt={lead.title} />{/snippet}
							{#snippet why()}{#if ['news', 'article', 'web'].includes(lead.type)}<ArticleFeedback itemId={lead.id} title={lead.title} rating={lead.state.rating} onchanged={(state) => { lead.state = state; }} />{/if}{/snippet}
						</StoryCard>
					{:else}
						<StoryCard {href} title={g.title} source={lead.publisher || lead.provider} {summary}
							more={rest} moreSources={selectedItems.slice(1).map((i) => i.publisher || i.provider).filter(Boolean)}>
							{#snippet why()}{#if ['news', 'article', 'web'].includes(lead.type)}<ArticleFeedback itemId={lead.id} title={lead.title} rating={lead.state.rating} onchanged={(state) => { lead.state = state; }} />{/if}{/snippet}
						</StoryCard>
					{/if}
					<p class="story-meta">{#if g.new}<span class="new-badge">New</span>{' · '}{/if}{selectedItems.length} source{selectedItems.length === 1 ? '' : 's'} · Updated {ago(g.latest)}</p>
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
		<nav class="pages bottom-pages" aria-label="More stories">
			<span aria-live="polite">Showing {Math.min(shown, visible.length)} of {visible.length} stories</span>
			{#if shown < visible.length}
				<div use:observeMore class="more-sentinel"></div>
				<button class="btn ghost" use:focusable onclick={showMore}>Show more stories</button>
			{:else}
				<button class="btn ghost" use:focusable onclick={freshMix}>You're through this mix — get a fresh one</button>
			{/if}
		</nav>
	{/if}
</div>

<style>
	.page { display: grid; gap: var(--sp-5); align-content: start; }
	.head { display: flex; justify-content: space-between; align-items: end; gap: 1rem; flex-wrap: wrap; }
	.head-actions { display: flex; gap: .5rem; flex-wrap: wrap; }
	.new-count { color: var(--accent, var(--ink)); }
	.new-badge { color: var(--accent, var(--ink)); font-weight: 600; }
	.more-sentinel { width: 1px; height: 1px; }
	.edition-count { display: flex; justify-content: space-between; align-items: baseline; gap: 1rem; max-width: 62rem; padding: .7rem 0; border-block: 1px solid var(--rule); color: var(--ink-soft); }
	.edition-count p { margin: 0; }
	.edition-count strong { color: var(--ink); font-variant-numeric: tabular-nums; }
	.edition-count span { color: var(--faint); font-size: var(--fs-sm); }
	.controls { display: flex; flex-wrap: wrap; gap: .5rem 1.25rem; }
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
	.bottom-pages { margin-top: .5rem; }
	.pages .btn { min-height: 2.5rem; }
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
	@media (max-width: 480px) { .edition-count { align-items: flex-start; flex-direction: column; gap: .2rem; } }
</style>
