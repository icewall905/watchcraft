<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { focusable } from '$lib/nav';
	import { hub, ago } from '$lib/hub/api';
	import { sinceTs, errMsg } from '$lib/hub/util';
	import type { Item, MemeExplain, MemeFormat, MemeTrend, Topic } from '$lib/hub/types';
	import MemeCard from '$lib/hub/MemeCard.svelte';
	import LocalImage from '$lib/hub/LocalImage.svelte';
	import Icon from '$lib/components/Icon.svelte';

	const LIMIT = 24;
	const TABS = [['trending', 'Trending'], ['emerging', 'Emerging'], ['formats', 'Formats'], ['examples', 'Explore'], ['saved', 'Saved'], ['history', 'History']] as const;
	const STATE_LABEL: Record<string, string> = { newly_observed: 'Newly observed', emerging: 'Emerging', trending: 'Trending', established: 'Established', fading: 'Fading' };
	const SINCE = [['', 'All time'], ['168', 'Last 7 days'], ['720', 'Last 30 days']] as const;

	let tab = $derived(page.url.searchParams.get('tab') ?? 'trending');
	let items = $state<Item[]>([]);
	let total = $state(0);
	let trends = $state<MemeTrend[]>([]);
	let formats = $state<MemeFormat[]>([]);
	let communities = $state<{ key: string; count: number }[]>([]);
	let languages = $state<{ key: string; count: number }[]>([]);
	let topics = $state<Topic[]>([]);
	let loading = $state(true);
	let more = $state(false);
	let error = $state('');
	let search = $state('');
	let seq = 0;
	let open = $state<Item | null>(null);
	let similar = $state<Item[]>([]);
	let explanation = $state<MemeExplain | null>(null);
	let explainError = $state('');
	let explaining = $state(false);

	const feed = $derived(tab === 'examples' || tab === 'saved' || tab === 'history');
	const params = $derived.by(() => {
		const p = page.url.searchParams;
		return {
			view: tab, q: p.get('q') || undefined, community: p.get('community') || undefined,
			language: p.get('language') || undefined, provider: p.get('provider') || undefined,
			topic: p.get('topic') || undefined, format_id: p.get('format_id') || undefined,
			since: sinceTs(p.get('since')), limit: LIMIT, offset: 0
		};
	});

	function setParam(key: string, value: string) {
		const u = new URL(page.url);
		if (value) u.searchParams.set(key, value); else u.searchParams.delete(key);
		goto(u, { replaceState: true, keepFocus: true, noScroll: true });
	}
	function setTab(t: string) {
		const u = new URL(page.url);
		if (t === 'trending') u.searchParams.delete('tab'); else u.searchParams.set('tab', t);
		if (t !== 'examples') u.searchParams.delete('format_id');
		goto(u, { replaceState: true, keepFocus: true, noScroll: true });
	}

	async function loadFeed(append = false) {
		const id = ++seq;
		if (append) more = true; else loading = true;
		error = '';
		try {
			const r = await hub.memeItems({ ...params, offset: append ? items.length : 0 });
			if (id !== seq) return;
			items = append ? [...items, ...r.items.filter((n) => !items.some((o) => o.id === n.id))] : r.items;
			total = r.total;
		} catch (e) { if (id === seq) error = errMsg(e); }
		finally { if (id === seq) { loading = false; more = false; } }
	}
	async function loadSide() {
		try {
			if (tab === 'trending') trends = (await hub.memeTrends({ state: 'trending,established' })).trends;
			else if (tab === 'emerging') trends = (await hub.memeTrends({ state: 'newly_observed,emerging' })).trends;
			else if (tab === 'formats') formats = (await hub.memeFormats({ sort: 'new' })).formats;
			if (feed && !communities.length) {
				const f = await hub.memeFacets();
				communities = f.communities; languages = f.languages;
			}
		} catch (e) { error = error || errMsg(e); }
	}
	$effect(() => { if (feed) void loadFeed(false); else { loading = true; void loadSide().finally(() => (loading = false)); } });
	$effect(() => { if (feed && !topics.length) hub.topics().then((t) => (topics = t)).catch(() => {}); });

	onMount(() => { search = page.url.searchParams.get('q') ?? ''; });

	async function toggleTrend(t: MemeTrend, patch: { followed?: boolean; hidden?: boolean }) {
		const r = await hub.memeTrendState(t.id, patch);
		t.followed = r.followed; t.hidden = r.hidden;
		if (r.hidden) trends = trends.filter((x) => x.id !== t.id);
	}
	function trendToFeed(t: MemeTrend) {
		const u = new URL(page.url);
		u.searchParams.set('tab', 'examples');
		if (t.target_kind === 'format') { u.searchParams.set('format_id', String(t.target_key)); u.searchParams.delete('community'); }
		else { u.searchParams.set('community', t.target_key); u.searchParams.delete('format_id'); }
		goto(u, { keepFocus: true, noScroll: true });
	}
	function formatToFeed(f: MemeFormat) {
		const u = new URL(page.url);
		u.searchParams.set('tab', 'examples');
		u.searchParams.set('format_id', String(f.id));
		goto(u, { keepFocus: true, noScroll: true });
	}

	async function openItem(it: Item) {
		open = it; similar = []; explanation = (it.enrichment?.meme_explain as MemeExplain) ?? null; explainError = '';
		hub.memeSimilar(it.id).then((r) => (similar = r.items)).catch(() => {});
	}
	function closeDrawer() { open = null; }
	function onKey(e: KeyboardEvent) { if (e.key === 'Escape' && open) closeDrawer(); }

	async function toggleState(key: 'saved' | 'dismissed') {
		if (!open) return;
		const st = await hub.setState(open.id, { [key]: !open.state[key] });
		open = { ...open, state: st };
		if (st.dismissed) items = items.filter((x) => x.id !== open?.id);
	}
	async function explain() {
		if (!open) return;
		explaining = true; explainError = '';
		try { explanation = (await hub.memeExplain(open.id)).explanation; }
		catch (e) { explainError = errMsg(e); }
		finally { explaining = false; }
	}
	function applySearch(e: SubmitEvent) {
		e.preventDefault();
		setParam('q', search.trim());
	}
	const spark = (t: MemeTrend) => t.observations.map((o) => o.count);
</script>

<svelte:head><title>Meme Radar · WatchCraft</title></svelte:head>

<svelte:window onkeydown={onKey} />

<div class="page">
	<header class="head">
		<div>
			<h1>Meme Radar</h1>
			<p class="muted">What your curators see spreading — counted, not guessed.</p>
		</div>
		<a class="btn ghost" href="/curators" use:focusable>Manage curators</a>
	</header>

	<div class="tabs" role="tablist" aria-label="Meme Radar views">
		{#each TABS as [k, name]}
			<button role="tab" aria-selected={tab === k} class="chip tab" class:on={tab === k} use:focusable onclick={() => setTab(k)}>{name}</button>
		{/each}
		{#if feed && !loading && !error}<span class="faint small count">{total} meme{total === 1 ? '' : 's'}</span>{/if}
	</div>

	{#if feed}
		<form class="radar-search" onsubmit={applySearch}>
			<input class="input" type="search" bind:value={search} placeholder="Search memes…" aria-label="Search memes" />
			<button class="btn" use:focusable>Search</button>
		</form>
		<div class="filters">
			<select class="input" aria-label="Community filter" value={page.url.searchParams.get('community') ?? ''} onchange={(e) => setParam('community', e.currentTarget.value)}>
				<option value="">All communities</option>
				{#each communities as c}<option value={c.key}>{c.key} ({c.count})</option>{/each}
			</select>
			<select class="input" aria-label="Language filter" value={page.url.searchParams.get('language') ?? ''} onchange={(e) => setParam('language', e.currentTarget.value)}>
				<option value="">All languages</option>
				{#each languages as l}<option value={l.key}>{l.key} ({l.count})</option>{/each}
			</select>
			<select class="input" aria-label="Topic filter" value={page.url.searchParams.get('topic') ?? ''} onchange={(e) => setParam('topic', e.currentTarget.value)}>
				<option value="">All topics</option>
				{#each topics as t}<option value={t.id}>{t.name}</option>{/each}
			</select>
			<select class="input" aria-label="Time range filter" value={page.url.searchParams.get('since') ?? ''} onchange={(e) => setParam('since', e.currentTarget.value)}>
				{#each SINCE as [v, label]}{#if v || label === 'All time'}<option value={v}>{label}</option>{/if}{/each}
			</select>
			{#if page.url.searchParams.get('format_id')}
				<button class="chip on" use:focusable onclick={() => setParam('format_id', '')}>Format filter — clear ✕</button>
			{/if}
		</div>
	{/if}

	{#if loading}
		<div class="skeletons" aria-busy="true">{#each Array(6) as _}<div class="skeleton sk"></div>{/each}</div>
	{:else if error && !items.length && !trends.length && !formats.length}
		<div class="empty" role="alert"><p>Couldn't load the radar: {error}</p><button class="btn primary" use:focusable onclick={() => location.reload()}>Retry</button></div>
	{:else if tab === 'trending' || tab === 'emerging'}
		{#if !trends.length}
			<div class="empty"><span class="big-emoji">✧</span><h2>Nothing counted {tab} yet</h2><p>Trends need repeated examples across communities before they are labelled. Give your curators a few runs.</p></div>
		{:else}
			<div class="trends">
				{#each trends as t (t.id)}
					<article class="panel trend" class:hidden-trend={t.hidden}>
						<header>
							<h2>{t.name}</h2>
							<span class="badge b-{t.state}">{STATE_LABEL[t.state] ?? t.state}</span>
						</header>
						<div class="spark" aria-hidden="true">
							{#each t.observations as o (o.day)}<div class="bar" style={`height: ${Math.max(4, (o.count / Math.max(...spark(t), 1)) * 100)}%`} title={`${o.day}: ${o.count}`}></div>{/each}
						</div>
						<p class="evidence">{t.evidence.join(' · ')}</p>
						<p class="faint small">First seen {ago(t.first_observed)} · last {ago(t.last_observed)} · {t.metrics.communities} communit{t.metrics.communities === 1 ? 'y' : 'ies'}</p>
						<footer>
							<button class="btn" use:focusable onclick={() => trendToFeed(t)}>View examples</button>
							<button class="btn ghost" aria-pressed={t.followed} use:focusable onclick={() => toggleTrend(t, { followed: !t.followed })}>{t.followed ? 'Following' : 'Follow'}</button>
							<button class="btn ghost" use:focusable onclick={() => toggleTrend(t, { hidden: true })}>Hide</button>
						</footer>
					</article>
				{/each}
			</div>
			{#if error}<p class="warn small">{error}</p>{/if}
			{/if}
	{:else if tab === 'formats'}
		{#if !formats.length}
			<div class="empty"><span class="big-emoji">✧</span><h2>No formats grouped yet</h2><p>When the same image or caption pattern shows up in more than one place, the radar groups it here.</p></div>
		{:else}
			<div class="formats">
				{#each formats as f (f.id)}
					<button class="panel fmt-card" use:focusable onclick={() => formatToFeed(f)}>
						<h2>{f.name}</h2>
						<p class="faint small">{f.kind} · {f.example_count} examples · {f.community_count} communities · first {ago(f.first_seen)}</p>
						{#if f.first_seen > (Date.now() / 1000 - 7 * 86400)}<span class="badge b-newly_observed">New</span>{/if}
					</button>
				{/each}
			</div>
		{/if}
	{:else if !items.length}
		<div class="empty"><span class="big-emoji">✧</span>
			{#if tab === 'saved'}<h2>Nothing saved yet</h2><p>Open a meme and save it and it lands here.</p>
			{:else}<h2>No memes yet</h2><p>Create a Meme Tracker curator and it starts collecting formats here.</p><a class="btn primary big" href="/curators/new?template=meme-radar" use:focusable>Create a Meme Tracker</a>{/if}
		</div>
	{:else}
		<div class="gallery">
			{#each items as it (it.id)}
				<MemeCard item={it} onopen={openItem} />
			{/each}
		</div>
		{#if items.length < total}
			<div class="more"><button class="btn big" disabled={more} use:focusable onclick={() => loadFeed(true)}>{more ? 'Loading…' : `Load more (${total - items.length} left)`}</button></div>
		{/if}
	{/if}
</div>

{#if open}
	<div class="scrim" role="dialog" aria-modal="true" aria-label="Meme detail" tabindex="-1" onclick={(e) => e.target === e.currentTarget && closeDrawer()} onkeydown={(e) => e.key === 'Escape' && closeDrawer()}>
		<div class="drawer">
			<button class="btn ghost close" use:focusable onclick={closeDrawer} aria-label="Close"><Icon name="x" /></button>
			<div class="shot">
				{#if open.local_image}<LocalImage id={open.id} alt={open.meme?.spoiler || open.title} loading="eager" />
				{:else}<p class="text-meme">{open.title || open.summary}</p>{/if}
			</div>
			<div class="body">
				<h2>{open.meme?.spoiler || open.title || open.summary}</h2>
				<p class="faint small">
					{open.meme?.community || open.publisher || open.provider}
					{#if open.meme?.language}· {open.meme.language}{/if} · seen {ago(open.first_seen)}
					{#if open.meme?.format_id}· <button class="link" use:focusable onclick={() => { const f = open?.meme?.format_id; closeDrawer(); if (f) formatToFeed({ id: f } as MemeFormat); }}>in format “{open.meme?.format_name}”</button>{/if}
				</p>
				{#if open.meme?.sensitive}<p class="warn small">Marked sensitive by the source{open.meme.spoiler ? `: ${open.meme.spoiler}` : ''}.</p>{/if}
				<div class="acts">
					<button class="btn" aria-pressed={open.state.saved} use:focusable onclick={() => toggleState('saved')}>{open.state.saved ? 'Saved' : 'Save'}</button>
					<button class="btn ghost" aria-pressed={open.state.dismissed} use:focusable onclick={() => toggleState('dismissed')}>{open.state.dismissed ? 'Dismissed' : 'Dismiss'}</button>
					<a class="btn ghost" href={open.url} target="_blank" rel="noreferrer" use:focusable>Original</a>
					<button class="btn ghost" disabled={explaining} use:focusable onclick={explain}>{explaining ? 'Explaining…' : explanation ? 'Re-explain' : 'Why is this funny?'}</button>
				</div>
				{#if explainError}<p class="warn small">{explainError}</p>{/if}
				{#if explanation}
					<dl class="explain">
						{#if explanation.what_shows}<dt>What shows</dt><dd>{explanation.what_shows}</dd>{/if}
						{#if explanation.why_funny}<dt>Why it lands</dt><dd>{explanation.why_funny}</dd>{/if}
						{#if explanation.referencing}<dt>Referencing</dt><dd>{explanation.referencing}</dd>{/if}
						{#if explanation.origin && explanation.origin !== 'unknown'}<dt>Origin</dt><dd>{explanation.origin}</dd>{/if}
						{#if explanation.uncertain}<dt>Uncertain</dt><dd>{explanation.uncertain}</dd>{/if}
					</dl>
				{/if}
				{#if similar.length}
					<h3>Similar memes</h3>
					<div class="similar">
						{#each similar.filter((x) => x.id !== open?.id) as s (s.id)}
							<button class="sim" use:focusable onclick={() => openItem(s)} title={s.title}>
								{#if s.local_image}<LocalImage id={s.id} alt={s.title} />{:else}<span>{s.title}</span>{/if}
							</button>
						{/each}
					</div>
				{/if}
			</div>
		</div>
	</div>
{/if}

<style>
	.page { display: grid; gap: 1rem; align-content: start; }
	.head { display: flex; justify-content: space-between; align-items: end; gap: 1rem; flex-wrap: wrap; }
	.tabs { display: flex; gap: .5rem; flex-wrap: wrap; align-items: center; }
	.tab { min-height: 2.75rem; padding: .4rem 1rem; }
	.count { margin-left: auto; }
	.radar-search { display: flex; gap: .5rem; max-width: 42rem; }
	.radar-search .input { flex: 1; min-width: 0; min-height: 2.75rem; }
	.filters { display: flex; gap: .6rem; flex-wrap: wrap; }
	.filters .input { min-height: 2.75rem; width: auto; max-width: 16rem; }
	.skeletons { display: grid; grid-template-columns: repeat(auto-fill, minmax(15rem, 1fr)); gap: 1rem; }
	.sk { height: 16rem; }
	.gallery { columns: 4 15rem; column-gap: 1rem; }
	.gallery > :global(*) { break-inside: avoid; margin-bottom: 1rem; }
	.more { display: grid; place-items: center; padding: 1rem; }
	.trends { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fill, minmax(20rem, 1fr)); }
	.trend { display: grid; gap: .6rem; }
	.trend header { display: flex; justify-content: space-between; align-items: start; gap: .5rem; }
	.trend h2 { margin: 0; font-size: 1.05rem; }
	.hidden-trend { opacity: .5; }
	.badge { font-size: .72rem; font-weight: 700; border-radius: var(--r-pill); padding: .2rem .6rem; background: var(--surface); color: var(--muted); white-space: nowrap; }
	.b-trending { background: var(--accent); color: var(--accent-ink); }
	.b-emerging, .b-newly_observed { background: var(--success); color: #05140b; }
	.spark { display: flex; gap: 3px; align-items: end; height: 3.5rem; }
	.spark .bar { flex: 1; background: var(--accent); border-radius: 2px 2px 0 0; min-height: 3px; opacity: .85; }
	.evidence { margin: 0; font-size: .9rem; }
	.trend footer { display: flex; gap: .5rem; flex-wrap: wrap; }
	.formats { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fill, minmax(16rem, 1fr)); }
	.fmt-card { display: grid; gap: .4rem; text-align: start; color: inherit; cursor: pointer; }
	.fmt-card h2 { margin: 0; font-size: 1rem; }
	.scrim { position: fixed; inset: 0; background: rgba(0, 0, 0, .6); display: grid; place-items: center; z-index: 60; padding: 1rem; }
	.drawer { position: relative; background: var(--bg); border: 1px solid var(--border); border-radius: var(--r-card); max-width: min(72rem, 96vw); max-height: 92vh; display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr); overflow: hidden; }
	.close { position: absolute; top: .6rem; right: .6rem; z-index: 2; }
	.shot { display: grid; place-items: center; background: rgba(255, 255, 255, .04); overflow: auto; }
	.shot :global(img) { max-width: 100%; max-height: 92vh; object-fit: contain; }
	.text-meme { padding: 2rem 1.5rem; font-size: 1.3rem; font-weight: 600; }
	.body { padding: 1.4rem; overflow: auto; display: grid; gap: .7rem; align-content: start; }
	.acts { display: flex; gap: .5rem; flex-wrap: wrap; }
	.explain { display: grid; grid-template-columns: auto 1fr; gap: .3rem .8rem; margin: 0; font-size: .92rem; }
	.explain dt { color: var(--muted); font-weight: 600; }
	.explain dd { margin: 0; }
	.link { background: none; border: 0; color: var(--accent); padding: 0; font: inherit; cursor: pointer; text-decoration: underline; }
	.similar { display: flex; gap: .5rem; flex-wrap: wrap; }
	.sim { width: 6rem; height: 6rem; border-radius: var(--r-card); overflow: hidden; border: 1px solid var(--border); background: var(--surface); padding: 0; cursor: pointer; display: grid; place-items: center; }
	.sim :global(img) { width: 100%; height: 100%; object-fit: cover; }
	.sim span { font-size: .7rem; padding: .3rem; display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical; overflow: hidden; }
	@media (max-width: 900px) { .drawer { grid-template-columns: 1fr; overflow: auto; } .shot :global(img) { max-height: 50vh; } }
</style>
