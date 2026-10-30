<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { focusable } from '$lib/nav';
	import { currentProfileId } from '$lib/api';
	import { hub, host } from '$lib/hub/api';
	import { lsGet, lsSet, errMsg } from '$lib/hub/util';
	import type { Registry, SavedSearch, SearchResult, WebResult } from '$lib/hub/types';
	import ContentCard from '$lib/hub/ContentCard.svelte';

	let q = $state('');
	let type = $state('');
	let scope = $state<'all' | 'library' | 'knowledge'>('all');
	let reg = $state<Registry | null>(null);
	let res = $state<SearchResult | null>(null);
	let loading = $state(false);
	let error = $state('');
	let ready = $state(false);
	let recent = $state<string[]>([]);
	let saved = $state<SavedSearch[]>([]);
	let savedErr = $state('');
	let ctl: AbortController | null = null;

	let picked = $state<Record<string, boolean>>({});
	let web = $state<{ results: WebResult[]; errors: { provider: string; message: string }[]; q: string } | null>(null);
	let webLoading = $state(false);
	let webError = $state('');
	let imported = $state<Record<string, 'busy' | 'done' | string>>({});

	const rkey = () => `wc.recent.${currentProfileId() ?? 'default'}`;
	const queryProviders = $derived((reg?.providers ?? []).filter((p) => p.mode === 'query'));
	const chosen = $derived(queryProviders.filter((p) => p.available && picked[p.key]).map((p) => p.key));

	onMount(() => {
		const p = page.url.searchParams;
		q = p.get('q') ?? ''; type = p.get('type') ?? '';
		const s = p.get('scope');
		scope = s === 'library' || s === 'knowledge' ? s : 'all';
		try { recent = JSON.parse(lsGet(rkey()) ?? '[]').filter((x: unknown) => typeof x === 'string'); } catch { recent = []; }
		hub.registry().then((r) => {
			reg = r;
			picked = Object.fromEntries(r.providers.filter((x) => x.mode === 'query' && x.available).map((x) => [x.key, true]));
		}).catch(() => {});
		loadSaved();
		ready = true;
	});

	async function loadSaved() {
		try { saved = await hub.savedSearches(); } catch (e) { savedErr = errMsg(e); }
	}

	function remember(term: string) {
		recent = [term, ...recent.filter((x) => x !== term)].slice(0, 8);
		lsSet(rkey(), JSON.stringify(recent));
	}

	// Debounced local search (300ms) with cancellation of the previous request.
	$effect(() => {
		if (!ready) return;
		const term = q.trim(), t = type, sc = scope;
		const u = new URL(page.url);
		for (const [k, v] of [['q', term], ['type', t], ['scope', sc === 'all' ? '' : sc]]) { if (v) u.searchParams.set(k, v); else u.searchParams.delete(k); }
		if (u.search !== page.url.search) goto(u, { replaceState: true, keepFocus: true, noScroll: true });
		ctl?.abort();
		if (!term) { res = null; loading = false; error = ''; return; }
		const timer = setTimeout(async () => {
			const c = (ctl = new AbortController());
			loading = true; error = '';
			try {
				const r = await hub.search({ q: term, type: t || undefined, scope: sc, limit: 20 }, c.signal);
				if (c.signal.aborted) return;
				res = r; remember(term);
			} catch (e) {
				if ((e as Error).name === 'AbortError') return;
				error = errMsg(e);
			} finally { if (!c.signal.aborted) loading = false; }
		}, 300);
		return () => clearTimeout(timer);
	});

	const empty = $derived(!!res && !res.items.length && !res.videos.length && !res.curators.length && !res.collections.length && !res.topics.length);

	async function runWeb() {
		const term = q.trim();
		if (!term || !chosen.length || webLoading) return;
		webLoading = true; webError = ''; web = null;
		try { const r = await hub.webSearch(term, chosen); web = { ...r, q: term }; }
		catch (e) { webError = errMsg(e); }
		finally { webLoading = false; }
	}
	async function importResult(url: string) {
		imported[url] = 'busy';
		try { await hub.importUrl(url); imported[url] = 'done'; }
		catch (e) { imported[url] = errMsg(e); }
	}

	async function saveCurrent() {
		const term = q.trim();
		if (!term) return;
		const filters: Record<string, string> = {};
		if (type) filters.type = type;
		if (scope !== 'all') filters.scope = scope;
		try { const s = await hub.saveSearch({ name: term, query: term, filters }); saved = [s, ...saved]; savedErr = ''; }
		catch (e) { savedErr = errMsg(e); }
	}
	async function delSaved(id: number) {
		try { await hub.deleteSavedSearch(id); saved = saved.filter((s) => s.id !== id); }
		catch (e) { savedErr = errMsg(e); }
	}
	function apply(s: SavedSearch) {
		q = s.query; type = s.filters?.type ?? '';
		scope = s.filters?.scope === 'library' || s.filters?.scope === 'knowledge' ? s.filters.scope : 'all';
	}
	const isSaved = $derived(saved.some((s) => s.query === q.trim()));
</script>

<svelte:head><title>Find · WatchCraft</title></svelte:head>

<div class="page">
	<header><h1>Find</h1><p class="muted">Search everything WatchCraft has collected, or look live on the web.</p></header>

	<search class="bar">
		<input class="input big" type="search" bind:value={q} placeholder="Search items, videos, curators, topics…" aria-label="Search" autocomplete="off" />
		<select class="input sel" bind:value={type} aria-label="Type">
			<option value="">All types</option>
			{#each reg?.types ?? [] as t}<option value={t.key}>{t.label}</option>{/each}
		</select>
		<select class="input sel" bind:value={scope} aria-label="Scope">
			<option value="all">Everywhere</option>
			<option value="library">Library</option>
			<option value="knowledge">Knowledge</option>
		</select>
	</search>

	<div class="hstack">
		<button class="btn" disabled={!q.trim() || isSaved} use:focusable onclick={saveCurrent}>{isSaved ? 'Search saved' : 'Save this search'}</button>
		{#if q.trim()}<a class="btn ghost" href={`/curators/new?goal=${encodeURIComponent(q.trim())}`} use:focusable>Turn this search into a curator</a>{/if}
	</div>

	<div class="layout">
		<div class="main">
			{#if !q.trim()}
				<section>
					{#if recent.length}
						<h2>Recent searches</h2>
						<div class="hstack">{#each recent as r}<button class="chip" use:focusable onclick={() => (q = r)}>{r}</button>{/each}
							<button class="btn ghost" use:focusable onclick={() => { recent = []; lsSet(rkey(), '[]'); }}>Clear</button></div>
					{:else}
						<div class="empty"><span class="big-emoji">🔎</span><h2>Start typing to search</h2><p>Results come from your items, videos, curators, collections and topics.</p></div>
					{/if}
				</section>
			{:else if error}
				<div class="empty" role="alert"><p>Search failed: {error}</p><button class="btn primary" use:focusable onclick={() => { const t = q; q = ''; q = t; }}>Retry</button></div>
			{:else if loading && !res}
				<div class="stack" aria-busy="true" aria-label="Searching">{#each Array(4) as _}<div class="skeleton sk"></div>{/each}</div>
			{:else if empty}
				<div class="empty"><span class="big-emoji">∅</span><h2>No local results for "{q.trim()}"</h2><p>Try the live web search, or create a curator to keep watching this.</p></div>
			{:else if res}
				<div class="results" class:busy={loading} aria-busy={loading}>
					{#if res.items.length}
						<section><h2>Content items <span class="faint small">{res.total}</span></h2>
							<div class="stack">{#each res.items as _, i (res.items[i].id)}<ContentCard bind:item={res.items[i]} layout="row" />{/each}</div></section>
					{/if}
					{#if res.videos.length}
						<section><h2>Videos</h2>
							<div class="vids">{#each res.videos as v (v.id)}
								<a class="vid" href={`/watch/${encodeURIComponent(v.id)}`} use:focusable>
									{#if v.thumbnail}<img src={v.thumbnail} alt="" loading="lazy" />{/if}
									<span class="vt">{v.title}</span><span class="faint small">{v.channel_name}</span>
								</a>{/each}</div></section>
					{/if}
					{#if res.curators.length}
						<section><h2>Curators</h2><div class="hstack">{#each res.curators as c (c.id)}<a class="chip" href={`/curators/${c.id}`} use:focusable>{c.name}</a>{/each}</div></section>
					{/if}
					{#if res.collections.length}
						<section><h2>Collections</h2><div class="hstack">{#each res.collections as c (c.id)}<a class="chip" href={`/library?view=collections&collection=${c.id}`} use:focusable>{c.name}</a>{/each}</div></section>
					{/if}
					{#if res.topics.length}
						<section><h2>Topics</h2><div class="hstack">{#each res.topics as t (t.id)}<a class="chip" href={`/knowledge/${t.id}`} use:focusable>{t.name}</a>{/each}</div></section>
					{/if}
				</div>
			{/if}
		</div>

		<aside>
			<section class="box web">
				<h2>Search the web</h2>
				<p class="faint small">Runs the query live against external providers. Nothing is saved unless you choose to.</p>
				<fieldset>
					<legend class="sr-only">Providers</legend>
					{#each queryProviders as p (p.key)}
						<label class="check" class:off={!p.available}>
							<input type="checkbox" disabled={!p.available} bind:checked={picked[p.key]} />
							<span>{p.label}{#if !p.available}<span class="faint small"> · unavailable{p.reason ? `: ${p.reason}` : ''}</span>{/if}</span>
						</label>
					{/each}
					{#if reg && !queryProviders.length}<p class="faint small">No query providers configured.</p>{/if}
				</fieldset>
				<button class="btn primary" disabled={!q.trim() || !chosen.length || webLoading} use:focusable onclick={runWeb}>{webLoading ? 'Searching…' : 'Search the web'}</button>

				{#if webError}<p class="err" role="alert">{webError} <button class="btn ghost" use:focusable onclick={runWeb}>Retry</button></p>{/if}
				{#if webLoading}<div class="skeleton" style="height:6rem" aria-busy="true"></div>{/if}
				{#if web}
					<p class="live">Live from the web — not saved</p>
					{#each web.errors as e}<p class="err small" role="alert">{e.provider}: {e.message}</p>{/each}
					{#if !web.results.length}<p class="muted small">No web results for "{web.q}".</p>{/if}
					<ul class="wr">
						{#each web.results as r (r.url)}
							<li>
								<a href={r.url} target="_blank" rel="noopener noreferrer" use:focusable>{r.title || r.url}</a>
								<p class="faint tiny">{r.provider} · {host(r.url)}{r.type ? ` · ${r.type}` : ''}</p>
								{#if r.snippet}<p class="small muted sn">{r.snippet}</p>{/if}
								<button class="btn ghost" disabled={imported[r.url] === 'busy' || imported[r.url] === 'done'} use:focusable onclick={() => importResult(r.url)}>
									{imported[r.url] === 'done' ? '✓ In Library' : imported[r.url] === 'busy' ? 'Saving…' : 'Save to Library'}
								</button>
								{#if imported[r.url] && imported[r.url] !== 'busy' && imported[r.url] !== 'done'}<span class="err tiny" role="alert">{imported[r.url]}</span>{/if}
							</li>
						{/each}
					</ul>
				{/if}
			</section>

			<section class="box">
				<h2>Saved searches</h2>
				{#if savedErr}<p class="err small" role="alert">{savedErr}</p>{/if}
				{#if !saved.length}<p class="faint small">None yet. Search, then "Save this search".</p>{/if}
				<ul class="ss">
					{#each saved as s (s.id)}
						<li><button class="chip" use:focusable onclick={() => apply(s)}>{s.name}</button>
							<button class="btn ghost" use:focusable aria-label={`Delete saved search ${s.name}`} onclick={() => delSaved(s.id)}>Delete</button></li>
					{/each}
				</ul>
			</section>
		</aside>
	</div>
</div>

<style>
	.page { display: grid; gap: 1rem; align-content: start; }
	.bar { display: flex; gap: .6rem; flex-wrap: wrap; }
	.bar .big { flex: 1; min-width: 14rem; min-height: 3.2rem; font-size: 1.1rem; }
	.sel { width: auto; min-height: 3.2rem; background: rgba(10, 8, 25, .85); }
	.layout { display: grid; grid-template-columns: minmax(0, 1fr) 22rem; gap: 2rem; align-items: start; }
	.main, .results { display: grid; gap: 1.5rem; }
	.results.busy { opacity: .6; }
	section h2 { font-size: 1.1rem; margin-bottom: .6rem; }
	.vids { display: grid; grid-template-columns: repeat(auto-fill, minmax(11rem, 1fr)); gap: .8rem; }
	.vid { display: grid; gap: .25rem; min-height: 2.75rem; }
	.vid img { width: 100%; aspect-ratio: 16/9; object-fit: cover; border-radius: var(--r-md); background: var(--bg-2); }
	.vt { font-weight: 650; font-size: .9rem; line-height: 1.25; }
	.box { border: 1px solid var(--line); background: var(--surface); border-radius: var(--r-lg); padding: 1rem 1.1rem; display: grid; gap: .7rem; }
	aside { display: grid; gap: 1rem; }
	.box h2 { margin: 0; }
	fieldset { border: 0; padding: 0; margin: 0; display: grid; gap: .1rem; }
	.check { display: flex; gap: .6rem; align-items: center; min-height: 2.75rem; font-size: .9rem; }
	.check.off { opacity: .6; }
	.check input { width: 1.2rem; height: 1.2rem; accent-color: var(--accent); }
	.live { font-size: .75rem; font-weight: 700; text-transform: uppercase; letter-spacing: .08em; color: var(--info); border-top: 1px solid var(--line); padding-top: .7rem; }
	.wr, .ss { list-style: none; margin: 0; padding: 0; display: grid; gap: .8rem; }
	.wr a { font-weight: 650; text-decoration: underline; overflow-wrap: anywhere; }
	.sn { display: -webkit-box; -webkit-line-clamp: 3; line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
	.ss li { display: flex; align-items: center; justify-content: space-between; gap: .4rem; }
	.err { color: var(--bad); }
	.sk { height: 8rem; }
	@media (max-width: 960px) { .layout { grid-template-columns: minmax(0, 1fr); } }
</style>
