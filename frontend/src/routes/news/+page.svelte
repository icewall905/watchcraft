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
	import SeenDismiss from '$lib/hub/SeenDismiss.svelte';

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
	const PAGE = 30; // stories per server page
	const RESTORE_PAGES = 10; // pages refetched at most to return to a saved place
	let shown = $state(step);
	let nextCursor = $state<string | null>(null);
	let totalStories = $state<number | null>(null);
	let totalArticles = $state<number | null>(null);
	let totalCapped = $state(false);
	let loadingMore = $state(false);
	let moreError = $state('');
	let autoPages = 5; // background page loads allowed while client filters hide most loaded stories
	let moreController: AbortController | null = null;
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
			controller?.abort(); clearRefreshState(); groups = []; topics = []; open = {}; topic = null; publisher = '';
			weeklyProgress = null; loading = false; error = ''; return;
		}
		// A cursor belongs to the list that issued it: never page a new filter (or a failed load) with an old one.
		controller?.abort(); moreController?.abort(); loadingMore = false; moreError = ''; nextCursor = null;
		controller = new AbortController();
		const signal = controller.signal;
		if (profileId !== loadedProfileId || version !== loadedProfileVersion) {
			groups = []; topics = []; open = {}; publisher = ''; topic = null; topicError = ''; shown = step; clearRefreshState();
			nextCursor = null; totalStories = null; totalArticles = null; totalCapped = false;
			pendingSeen.clear(); sentSeen.clear();
		}
		visit = currentVisit();
		loading = true; error = '';
		try {
			const params = { days, topic: topic ?? undefined, mode, seed: visit.seed, visit_at: visit.at, limit: PAGE };
			const stale = () => id !== seq || profileId !== session.profileId || version !== session.version || !!session.profile?.pin_locked || session.needsLogin;
			const r = await hub.news(params, signal);
			if (stale()) return;
			let loaded = r.groups, cursor = r.next_cursor ?? null;
			// Returning to a saved place: refetch (bounded) enough pages to cover what was shown.
			const wanted = Math.max(step, visit.shown ?? step);
			for (let n = 0; cursor && loaded.length < wanted && n < RESTORE_PAGES; n++) {
				const more = await hub.news({ ...params, cursor }, signal);
				if (stale()) return;
				loaded = appendGroups(loaded, more.groups); cursor = more.next_cursor ?? null;
			}
			groups = loaded; topics = r.topics; nextCursor = cursor;
			totalStories = r.total_stories ?? null; totalArticles = r.total_articles ?? null; totalCapped = !!r.total_capped;
			weeklyProgress = r.supply ?? null;
			loadedProfileId = profileId; loadedProfileVersion = version;
			newCount = r.new_count ?? 0;
			shown = Math.max(step, visit.shown ?? step);
			autoPages = 5;
			restoreScroll = visit.scroll ?? null;
		} catch (e) { if (id === seq && !signal.aborted && profileId === session.profileId && version === session.version && !session.profile?.pin_locked && !session.needsLogin) error = errMsg(e); }
		finally { if (id === seq && profileId === session.profileId && version === session.version) { loading = false; controller = null; } }
	}
	$effect(() => { session.profileId; session.version; session.profile?.pin_locked; session.needsLogin; untrack(() => { void load(); }); });
	onDestroy(() => { controller?.abort(); moreController?.abort(); });
	function appendGroups(have: NewsGroup[], more: NewsGroup[]) {
		// Pages of one visit never overlap; the guard keeps a story from rendering twice if data shifted.
		const keys = new Set(have.map((g) => g.key));
		return [...have, ...more.filter((g) => !keys.has(g.key))];
	}
	async function loadMore() {
		if (!nextCursor || loadingMore || loading) return;
		const id = seq, profileId = session.profileId, version = session.version, cursor = nextCursor;
		moreController = new AbortController();
		const signal = moreController.signal;
		loadingMore = true; moreError = '';
		try {
			const r = await hub.news({ days, topic: topic ?? undefined, mode, seed: visit.seed, visit_at: visit.at, limit: PAGE, cursor }, signal);
			if (id !== seq || profileId !== session.profileId || version !== session.version || session.profile?.pin_locked || session.needsLogin || cursor !== nextCursor) return;
			const before = visible.length;
			groups = appendGroups(groups, r.groups);
			nextCursor = r.next_cursor ?? null;
			// Only pages that add visible stories renew the background budget; a filter matching nothing stops.
			if (visible.length > before) autoPages = 5;
		} catch (e) { if (id === seq && !signal.aborted && profileId === session.profileId) moreError = errMsg(e); }
		finally { if (id === seq) { loadingMore = false; moreController = null; rearm(); } }
	}
	function resetPlace() { shown = step; visit.scroll = 0; visit.shown = step; store.set(visit); }
	function pick(d: 1 | 7 | 30) { days = d; resetPlace(); load(); }
	function pickMode(m: 'foryou' | 'latest') { if (mode === m) return; mode = m; resetPlace(); load(); }
	function pickTopic(id: number | null) { topic = topic === id ? null : id; resetPlace(); load(); }
	async function freshMix() {
		await flushSeen();
		visit = startVisit(); mode = 'foryou'; shown = step;
		window.scrollTo({ top: 0, behavior: 'smooth' });
		await load();
	}
	// Refresh: a fresh mix now, and the curators sent out for more. New stories surface as a pill.
	let refreshing = $state(false);
	let hunting = $state('');
	let freshWaiting = $state(false);
	let watchTimer: ReturnType<typeof setTimeout> | null = null;
	let newestAt = 0;
	// Baseline from the same query the watcher polls, so the pill only means "newer than when you refreshed".
	async function markNewest() {
		try { newestAt = (await hub.news({ days, mode: 'latest', limit: 1 })).groups[0]?.latest ?? 0; } catch { newestAt = Date.now() / 1000; }
	}
	function clearRefreshState() { stopWatch(); hunting = ''; freshWaiting = false; }
	function stopWatch() { if (watchTimer) clearTimeout(watchTimer); watchTimer = null; }
	function watchForNew(tries = 8) {
		stopWatch();
		if (tries <= 0) { hunting = ''; return; }
		const profileId = session.profileId, version = session.version;
		watchTimer = setTimeout(async () => {
			try {
				const r = await hub.news({ days, mode: 'latest', limit: 1 });
				if (profileId !== session.profileId || version !== session.version) return;
				if ((r.groups[0]?.latest ?? 0) > newestAt) { freshWaiting = true; hunting = ''; return; }
			} catch { /* best effort */ }
			watchForNew(tries - 1);
		}, 45_000);
	}
	async function refresh() {
		if (refreshing || session.profile?.pin_locked || session.needsLogin) return;
		const profileId = session.profileId, version = session.version;
		refreshing = true; freshWaiting = false;
		// Baseline first: a run that finishes quickly must still surface as new.
		await markNewest();
		const out = hub.newsRefresh().catch(() => null);
		try { await freshMix(); } finally { refreshing = false; }
		const r = await out;
		if (!r || profileId !== session.profileId || version !== session.version) return;
		const out_ = r.started + r.running;
		hunting = out_ ? `${out_} curator${out_ === 1 ? '' : 's'} hunting for more — new stories will pop up here`
			: r.recent ? 'Curators just ran — this is everything for now' : '';
		if (out_) watchForNew();
	}
	async function showFresh() { clearRefreshState(); await freshMix(); }
	onDestroy(stopWatch);
	// Pull down at the top of the feed (touch) to refresh.
	let pull = $state(0);
	let pullStart: number | null = null;
	const PULL_AT = 70;
	function touchStart(e: TouchEvent) {
		const inOverlay = (e.target as Element | null)?.closest?.('dialog, [role="dialog"], [aria-modal="true"], input, select, textarea');
		pullStart = window.scrollY <= 0 && !refreshing && !inOverlay && e.touches.length === 1 ? e.touches[0].clientY : null;
	}
	// Our pull replaces the browser's own, which would otherwise reload the whole page too.
	onMount(() => {
		const root = document.documentElement, before = root.style.overscrollBehaviorY;
		root.style.overscrollBehaviorY = 'contain';
		return () => { root.style.overscrollBehaviorY = before; };
	});
	function touchMove(e: TouchEvent) { if (pullStart != null) pull = Math.max(0, Math.min(110, (e.touches[0].clientY - pullStart) * .5)); }
	function touchEnd() { if (pull >= PULL_AT) void refresh(); pull = 0; pullStart = null; }

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
			moreObserver = new IntersectionObserver((entries) => { if (entries.some((e) => e.isIntersecting)) showMore(false); }, { rootMargin: '600px 0px' });
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
	// A story the reader has dealt with leaves this visit's mix without a reload.
	function removeGroup(key: string) { groups = groups.filter((g) => g.key !== key); }
	const filtering = $derived(!!publisher || terms.length > 0);
	const storyTotal = $derived(filtering || totalStories == null ? visible.length : Math.max(totalStories, visible.length));
	const articleTotal = $derived(filtering || totalArticles == null ? null : totalArticles);
	const DISCUSSION = new Set(['hackernews', 'lemmy', 'mastodon', 'mastodon_trends', 'lobsters', 'reddit']);
	const isDiscussion = (i: Item) => DISCUSSION.has((i.provider ?? '').toLowerCase()) || (i.provider ?? '').toLowerCase().startsWith('reddit');
	function coverageLabel(items: Item[]) {
		// Outlets, not raw article count: syndicated copies from one publisher count once; threads are discussions.
		const talk = items.filter(isDiscussion).length;
		const outlets = new Set(items.filter((i) => !isDiscussion(i)).map((i) => (i.publisher || i.provider || '').trim().toLowerCase()).filter(Boolean)).size;
		const parts = [];
		if (outlets) parts.push(`${outlets} outlet${outlets === 1 ? '' : 's'}`);
		if (talk) parts.push(`${talk} discussion${talk === 1 ? '' : 's'}`);
		return parts.join(' · ') || `${items.length} article${items.length === 1 ? '' : 's'}`;
	}
	// Every story carries its date; older ones say so plainly, and Latest groups stories by day.
	const DAY = 86400;
	const OLD_AFTER = 3 * DAY;
	const dayStart = (t: number) => { const d = new Date(t * 1000); d.setHours(0, 0, 0, 0); return d.getTime() / 1000; };
	function dayLabel(t: number) {
		const today = dayStart(Date.now() / 1000), day = dayStart(t);
		if (day >= today) return 'Today';
		if (day >= today - DAY) return 'Yesterday';
		return new Date(t * 1000).toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'short' });
	}
	function stamp(t: number) {
		if (!t) return '';
		if (Date.now() / 1000 - t < DAY) return ago(t);
		return `${new Date(t * 1000).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })} · ${ago(t)}`;
	}
	const isOld = (t: number) => !!t && Date.now() / 1000 - t > OLD_AFTER;
	const visibleArticleCount = $derived(visible.reduce((n, { g }) => n + g.items.filter((i) => matches(i) && (!publisher || i.publisher === publisher)).length, 0));
	let previousFilterKey = '';
	$effect(() => {
		const key = `${days}|${mode}|${topic ?? ''}|${publisher}|${page.url.searchParams.get('q') ?? ''}`;
		if (previousFilterKey && previousFilterKey !== key) { shown = step; autoPages = 5; }
		previousFilterKey = key;
	});
	let moreNode: HTMLElement | null = null;
	function showMore(byUser = true) {
		if (shown >= visible.length && !nextCursor) return;
		shown = Math.min(shown, visible.length) + step;
		if (byUser) autoPages = 5;
		// Fetch the next server page before the reader reaches the end of what is loaded; the sentinel
		// alone spends the bounded budget, so a filter that matches nothing cannot walk every page.
		if (nextCursor && visible.length < shown + step && (byUser || autoPages-- > 0)) void loadMore();
		rearm();
	}
	// Re-arm the sentinel: if it is still on screen after growing, load the next batch too.
	function rearm() { void tick().then(() => { if (moreNode && moreObserver) { moreObserver.unobserve(moreNode); moreObserver.observe(moreNode); } }); }
	// Client filters can hide most loaded stories: fetch a few more pages in the background (bounded).
	$effect(() => {
		if (loading || loadingMore || !nextCursor || moreError || visible.length >= shown || autoPages <= 0) return;
		autoPages -= 1;
		untrack(() => { void loadMore(); });
	});
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

<svelte:window ontouchstart={touchStart} ontouchmove={touchMove} ontouchend={touchEnd} />

<div class="page">
	<div class="pull" style:height={`${pull}px`} aria-hidden="true"><span class:ready={pull >= PULL_AT} style:transform={`rotate(${pull * 3}deg)`}>↻</span></div>
	<header class="head">
		<div><h1>News</h1><p class="muted">{mode === 'foryou' ? 'Your endless mix, grouped by story. Refresh any time for more.' : 'Newest first, grouped by story.'}</p></div>
		<div class="head-actions">
			<button class="btn primary refresh" class:spin={refreshing} use:focusable onclick={refresh} disabled={loading || refreshing} aria-label="Refresh feed"><span class="refresh-icon" aria-hidden="true">↻</span> Refresh</button>
			<a class="btn ghost" href="/briefings?new=1" use:focusable>Briefing</a>
		</div>
	</header>
	{#if hunting}<p class="hunting" role="status"><span class="dot" aria-hidden="true"></span>{hunting}</p>{/if}
	{#if freshWaiting}<button class="fresh-pill" use:focusable onclick={showFresh}>↑ New stories</button>{/if}

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

	<LiveFilter placeholder="Filter stories by words or tags…" label="Filter news" count={loading ? null : storyTotal} />
	{#if !loading && !error && (visible.length || (days === 7 && weeklyProgress))}
		<div class="edition-count" aria-live="polite">
			<p><strong>{articleTotal ?? visibleArticleCount}</strong> articles across <strong>{storyTotal}{totalCapped && !filtering ? '+' : ''}</strong> stories{#if mode === 'foryou' && newCount}{' · '}<strong class="new-count">{newCount}</strong> new since your last visit{/if}</p>
			<span>{days === 7 && weeklyProgress
				? `${weeklyProgress.available} approved this week · ${weeklyProgress.available >= weeklyProgress.target ? `${weeklyProgress.target}+ goal met` : `goal: ${weeklyProgress.target}`}${weeklyProgress.pending_review ? ` · ${weeklyProgress.pending_review} in review` : ''}`
				: days === 7 ? 'Weekly curation goal: at least 100 articles' : days === 1 ? 'Today’s coverage' : 'Monthly coverage'}</span>
		</div>
		{#if days === 7 && weeklyProgress?.ready_count !== undefined}
			<p class="small faint supply" aria-label="Article supply">{weeklyProgress.ready_count} / {weeklyProgress.ready_target ?? 100} ready to read · Cleaned local text and AI summary · {weeklyProgress.candidate_count ?? weeklyProgress.available} / {weeklyProgress.candidate_target ?? 300} approved candidates for refills.</p>
		{/if}
	{/if}

	<section class="topics" aria-label="Followed topics">
		<div class="hstack topic-row">
			{#each topics as t (t.id)}
				<span class="tchip" class:muted-t={t.muted}>
					<button class="chip" class:on={topic === t.id} aria-pressed={topic === t.id} use:focusable onclick={() => pickTopic(t.id)}>{t.name}{#if t.muted} (muted){/if}</button>
					<button class="btn ghost sm" use:focusable aria-label={`${t.muted ? 'Unmute' : 'Mute'} ${t.name}`} onclick={() => mute(t)}>{t.muted ? 'Unmute' : 'Mute'}</button>
				</span>
			{/each}
			{#if !topics.length && !loading}<span class="faint small">No followed topics yet.</span>{/if}
		</div>
		<details class="tools">
		<summary use:focusable>Follow a topic or pick a publisher{#if publisher}<span class="tools-on"> · {publisher}</span>{/if}</summary>
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
		</details>
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
			{#each pageGroups as { g }, i (g.key)}
				{#if mode === 'latest' && g.latest && (i === 0 || dayStart(pageGroups[i - 1].g.latest) !== dayStart(g.latest))}
					<h2 class="day">{dayLabel(g.latest)}</h2>
				{/if}
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
							{#snippet why()}<SeenDismiss itemIds={selectedItems.map((i) => i.id)} title={g.title} onchanged={() => removeGroup(g.key)} />{#if ['news', 'article', 'web'].includes(lead.type)}<ArticleFeedback itemId={lead.id} title={lead.title} rating={lead.state.rating} onchanged={(state) => { lead.state = state; }} />{/if}{/snippet}
						</StoryCard>
					{:else}
						<StoryCard {href} title={g.title} source={lead.publisher || lead.provider} {summary}
							more={rest} moreSources={selectedItems.slice(1).map((i) => i.publisher || i.provider).filter(Boolean)}>
							{#snippet why()}<SeenDismiss itemIds={selectedItems.map((i) => i.id)} title={g.title} onchanged={() => removeGroup(g.key)} />{#if ['news', 'article', 'web'].includes(lead.type)}<ArticleFeedback itemId={lead.id} title={lead.title} rating={lead.state.rating} onchanged={(state) => { lead.state = state; }} />{/if}{/snippet}
						</StoryCard>
					{/if}
					<p class="story-meta">{#if g.new}<span class="new-badge">New</span>{' · '}{/if}{#if isOld(g.latest)}<span class="old-badge">Older</span>{' · '}{/if}{coverageLabel(selectedItems)} · <time datetime={new Date(g.latest * 1000).toISOString()} title={new Date(g.latest * 1000).toLocaleString()}>{stamp(g.latest)}</time></p>
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
			<span aria-live="polite">Showing {Math.min(shown, visible.length)} of {storyTotal}{nextCursor && (filtering || totalCapped) ? '+' : ''} stories</span>
			{#if moreError}
				<button class="btn ghost" use:focusable onclick={() => { moreError = ''; void loadMore(); }}>Couldn't load more — retry</button>
			{:else if shown < visible.length || nextCursor}
				<div use:observeMore class="more-sentinel"></div>
				<button class="btn ghost" use:focusable onclick={() => showMore(true)} disabled={loadingMore && shown >= visible.length}>{loadingMore && shown >= visible.length ? 'Loading more stories…' : 'Show more stories'}</button>
			{/if}
		</nav>
		{#if !moreError && shown >= visible.length && !nextCursor}
			<div class="caught-up">
				<span class="big-emoji" aria-hidden="true">✨</span>
				<h2>You're all caught up</h2>
				<p class="muted">Refresh for a new mix — your curators go hunting for more while you read.</p>
				<div class="hstack caught-actions">
					<button class="btn primary" use:focusable onclick={refresh} disabled={refreshing}>Refresh feed</button>
					{#if days < 30}<button class="btn ghost" use:focusable onclick={() => { pick(days === 1 ? 7 : 30); window.scrollTo({ top: 0 }); }}>Dig deeper: {days === 1 ? 'this week' : 'this month'}</button>{/if}
				</div>
			</div>
		{/if}
	{/if}
</div>

<style>
	.page { display: grid; gap: var(--sp-5); align-content: start; }
	.pull { display: grid; place-items: end center; overflow: hidden; margin-bottom: calc(-1 * var(--sp-5)); transition: height .15s ease-out; }
	.pull span { font-size: 1.4rem; color: var(--faint); transition: color .15s; }
	.pull span.ready { color: var(--accent, var(--ink)); }
	.refresh-icon { display: inline-block; }
	.refresh.spin .refresh-icon { animation: spin .8s linear infinite; }
	@keyframes spin { to { transform: rotate(360deg); } }
	.hunting { display: flex; align-items: center; gap: .5rem; margin: 0; color: var(--ink-soft); font-size: var(--fs-sm); }
	.dot { width: .5rem; height: .5rem; border-radius: 50%; background: var(--accent, var(--ink)); animation: pulse 1.4s ease-in-out infinite; }
	@keyframes pulse { 50% { opacity: .25; transform: scale(.7); } }
	.fresh-pill { position: fixed; top: 4.5rem; left: 50%; transform: translateX(-50%); z-index: 30; border: 0; border-radius: var(--r-pill);
		padding: .55rem 1.1rem; background: var(--accent, var(--ink)); color: var(--bg, #fff); font-weight: 600; cursor: pointer;
		box-shadow: 0 6px 24px rgba(0, 0, 0, .35); animation: drop .3s ease-out; }
	@keyframes drop { from { opacity: 0; transform: translate(-50%, -.8rem); } }
	.tools { max-width: 62rem; }
	.topic-row { flex-wrap: nowrap; overflow-x: auto; scrollbar-width: none; padding-bottom: .2rem; }
	.topic-row > * { flex: none; }
	.tools summary { cursor: pointer; color: var(--ink-soft); font-size: var(--fs-sm); padding: .3rem 0; }
	.tools[open] summary { margin-bottom: .6rem; }
	.tools-on { color: var(--accent, var(--ink)); }
	.supply { margin: 0; }
	.story { animation: rise .35s ease-out both; }
	@keyframes rise { from { opacity: 0; transform: translateY(.6rem); } }
	@media (prefers-reduced-motion: reduce) { .story, .fresh-pill, .dot, .refresh.spin .refresh-icon { animation: none; } }
	.day { position: sticky; top: 3.5rem; z-index: 5; margin: 1.2rem 0 .2rem; padding: .35rem 0; background: var(--bg, transparent);
		color: var(--ink-soft); font-size: var(--fs-sm); font-weight: 600; letter-spacing: .04em; text-transform: uppercase; }
	.old-badge { color: var(--faint); font-weight: 600; }
	.caught-up { display: grid; justify-items: center; gap: .5rem; max-width: 62rem; padding: 2.5rem 1rem; text-align: center; border-top: 1px solid var(--rule); }
	.caught-up h2 { margin: 0; }
	.caught-up p { margin: 0; }
	.caught-actions { justify-content: center; gap: .5rem; flex-wrap: wrap; margin-top: .5rem; }
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
