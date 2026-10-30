<script lang="ts">
	// Shared content card. Props are a stable contract (docs/HUB.md); visuals may evolve.
	import type { Snippet } from 'svelte';
	import { focusable } from '$lib/nav';
	import { hub, ago, host } from './api';
	import { str, num, list, bool, duration, sevClass, safeUrl } from './util';
	import type { Item } from './types';

	let { item = $bindable(), layout = 'card', onchange, extra }: {
		item: Item; layout?: 'card' | 'row' | 'compact'; onchange?: (item: Item) => void; extra?: Snippet<[Item]>;
	} = $props();

	let why = $state(false);
	let busy = $state(false);
	let failed = $state('');

	async function toggle(key: 'saved' | 'favorite' | 'read_later' | 'dismissed') {
		if (busy) return;
		busy = true; failed = '';
		try {
			const state = await hub.setState(item.id, { [key]: !item.state[key] });
			item = { ...item, state };
			onchange?.(item);
		} catch { failed = 'Could not update. Try again.'; }
		finally { busy = false; }
	}
	function opened() {
		if (!item.state.read_at) hub.setState(item.id, { read: true }).then((s) => { item = { ...item, state: s }; onchange?.(item); }).catch(() => {});
	}

	const m = $derived(item.meta ?? {});
	const videoId = $derived(str(m, 'video_id'));
	const href = $derived(item.type === 'video' && videoId ? `/watch/${encodeURIComponent(videoId)}` : `/item/${item.id}`);
	const isNews = $derived(item.type === 'news' || item.type === 'article');
	const entity = $derived(['person', 'organization', 'domain'].includes(item.type));
	const cvss = $derived(num(m, 'cvss_score'));
	const sev = $derived(str(m, 'severity'));
	const ingredients = $derived(list(m, 'ingredients').length);
	const materials = $derived(list(m, 'materials'));
	const dur = $derived(duration(num(m, 'duration')));
	const unread = $derived(!item.state.read_at);
	const pub = $derived(item.publisher || item.author || host(item.url));
</script>

<article class="cc {layout}" class:unread class:dim={item.state.dismissed} data-type={item.type}>
	{#if item.image && layout !== 'compact'}
		<a class="thumb" href={href} tabindex="-1" aria-hidden="true" onclick={opened}>
			<img src={item.image} alt="" loading="lazy" referrerpolicy="no-referrer" />
			{#if item.type === 'video' && dur}<span class="dur">{dur}</span>{/if}
		</a>
	{/if}
	<div class="body">
		<p class="meta">
			<span class="type">{item.type}</span>
			<span>{pub}</span>
			{#if isNews && item.published_at}
				<span title="Published vs found">Published {ago(item.published_at)} · found {ago(item.first_seen)}</span>
			{:else if item.published_at}<span>{ago(item.published_at)}</span>
			{:else}<span>found {ago(item.first_seen)}</span>{/if}
			{#if unread}<span class="dot" title="Unread" aria-label="Unread"></span>{/if}
		</p>
		<h3><a href={href} use:focusable onclick={opened}>{item.title}</a></h3>

		<div class="badges">
			{#if item.type === 'advisory'}
				{#if str(m, 'cve_id')}<span class="b mono">{str(m, 'cve_id')}</span>{/if}
				{#if cvss !== null}<span class="b {sevClass(sev)}">CVSS {cvss.toFixed(1)}{sev ? ` · ${sev}` : ''}</span>{:else if sev}<span class="b {sevClass(sev)}">{sev}</span>{/if}
				{#if bool(m, 'known_exploited')}<span class="b bad strong">Known exploited (CISA KEV)</span>{/if}
			{:else if item.type === 'recipe'}
				{#if str(m, 'total_time')}<span class="b">{str(m, 'total_time')}</span>{/if}
				{#if str(m, 'yield')}<span class="b">Yield {str(m, 'yield')}</span>{/if}
				{#if ingredients}<span class="b">{ingredients} ingredients</span>{/if}
			{:else if item.type === 'pattern'}
				{#if str(m, 'difficulty')}<span class="b">{str(m, 'difficulty')}</span>{/if}
				{#if materials.length}<span class="b">{materials.slice(0, 3).map(String).join(', ')}{materials.length > 3 ? '…' : ''}</span>{/if}
			{:else if item.type === 'project'}
				{#if num(m, 'stars') !== null}<span class="b">★ {num(m, 'stars')?.toLocaleString()}</span>{/if}
				{#if str(m, 'language')}<span class="b">{str(m, 'language')}</span>{/if}
				{#if str(m, 'version')}<span class="b mono">{str(m, 'version')}</span>{/if}
			{:else if entity}
				<span class="b ai">OSINT entity</span>
			{/if}
			{#if item.score !== null && item.score !== undefined}<span class="score" title="Relevance to your curators">{Math.round(item.score <= 1 ? item.score * 100 : item.score)}</span>{/if}
		</div>

		{#if item.summary && layout !== 'compact'}<p class="summary">{item.summary}</p>{/if}
		{#if item.state.tags.length && layout === 'card'}<p class="tags">{#each item.state.tags as t}<span>#{t}</span>{/each}</p>{/if}

		<div class="actions">
			<button class="btn ghost" aria-pressed={item.state.saved} disabled={busy} use:focusable onclick={() => toggle('saved')}>{item.state.saved ? '✓ Saved' : 'Save'}</button>
			<button class="btn ghost" aria-pressed={item.state.favorite} disabled={busy} use:focusable onclick={() => toggle('favorite')} aria-label={item.state.favorite ? 'Remove favorite' : 'Favorite'}>{item.state.favorite ? '★' : '☆'}<span class="lbl"> Favorite</span></button>
			<button class="btn ghost" aria-pressed={item.state.read_later} disabled={busy} use:focusable onclick={() => toggle('read_later')}>{item.state.read_later ? 'Queued' : 'Read later'}</button>
			<a class="btn ghost" href={safeUrl(item.url) || undefined} target="_blank" rel="noopener noreferrer" use:focusable onclick={opened}>Open original ↗</a>
			{#if item.reason || item.curator_names.length}
				<button class="btn ghost" aria-expanded={why} use:focusable onclick={() => (why = !why)}>Why?</button>
			{/if}
			<button class="btn ghost" disabled={busy} use:focusable onclick={() => toggle('dismissed')}>{item.state.dismissed ? 'Restore' : 'Dismiss'}</button>
			{#if extra}{@render extra(item)}{/if}
		</div>
		{#if failed}<p class="err" role="alert">{failed}</p>{/if}
		{#if why}
			<div class="why">
				{#if item.reason}<p>{item.reason}</p>{/if}
				{#if item.curator_names.length}<p class="faint">Found by {item.curator_names.join(', ')}</p>{/if}
			</div>
		{/if}
	</div>
</article>

<style>
	.cc { display: grid; border: 1px solid var(--line); border-radius: var(--r-lg); background: var(--surface); overflow: hidden; align-content: start; min-width: 0; }
	.cc.unread { border-color: var(--line-strong); }
	.cc.dim { opacity: .55; }
	.cc.row { grid-template-columns: 11rem 1fr; }
	.cc.row:not(:has(.thumb)) { grid-template-columns: 1fr; }
	.cc.compact { border-radius: var(--r-md); }
	.thumb { position: relative; display: block; background: var(--bg-2); }
	.cc.row .thumb { height: 100%; }
	.cc img { width: 100%; aspect-ratio: 16/9; object-fit: cover; background: var(--bg-2); }
	.cc.row img { height: 100%; aspect-ratio: auto; min-height: 7rem; }
	.cc[data-type='recipe'] img, .cc[data-type='pattern'] img { aspect-ratio: 4/3; }
	.dur { position: absolute; right: .4rem; bottom: .4rem; background: rgba(0,0,0,.75); color: #fff; font-size: .72rem; font-weight: 700; padding: .1rem .4rem; border-radius: .3rem; font-variant-numeric: tabular-nums; }
	.body { padding: .9rem 1.05rem 1rem; min-width: 0; }
	.compact .body { padding: .6rem .85rem; }
	.meta { font-size: .75rem; color: var(--muted); margin: 0; display: flex; flex-wrap: wrap; gap: .1rem .55rem; align-items: center; }
	.type { text-transform: uppercase; letter-spacing: .1em; color: var(--accent); font-weight: 700; }
	.dot { width: .5rem; height: .5rem; border-radius: 50%; background: var(--accent); }
	h3 { font-size: 1.05rem; margin: .3rem 0; line-height: 1.3; overflow-wrap: anywhere; }
	.compact h3 { font-size: .95rem; margin: .2rem 0; }
	h3 a { color: var(--text); display: inline-block; min-height: 1.5rem; }
	.badges { display: flex; flex-wrap: wrap; gap: .35rem; align-items: center; margin: .2rem 0 .4rem; }
	.badges:empty { display: none; }
	.b { font-size: .74rem; font-weight: 650; padding: .1rem .55rem; border-radius: var(--r-pill); background: var(--surface-2); border: 1px solid var(--line); }
	.b.bad { color: #ffd1d9; border-color: rgba(255,92,122,.5); background: rgba(255,92,122,.14); }
	.b.warn { color: var(--warn); border-color: rgba(255,207,92,.4); background: rgba(255,207,92,.1); }
	.b.ok { color: var(--ok); border-color: rgba(61,220,151,.4); background: rgba(61,220,151,.1); }
	.b.ai { color: #d9d0ff; border-color: rgba(157,134,255,.4); }
	.b.strong { font-weight: 800; }
	.mono { font-family: ui-monospace, monospace; }
	.summary { color: var(--muted); font-size: .88rem; display: -webkit-box; -webkit-line-clamp: 3; line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
	.tags { display: flex; gap: .5rem; flex-wrap: wrap; font-size: .75rem; color: var(--faint); margin-top: .35rem; }
	.actions { display: flex; gap: .15rem .25rem; flex-wrap: wrap; margin-top: .55rem; }
	.actions :global(.btn) { min-height: 2.75rem; padding: .4rem .75rem; font-size: .85rem; }
	.why { margin-top: .5rem; padding: .6rem .8rem; border-radius: var(--r-md); background: var(--bg-2); font-size: .85rem; border-left: 3px solid var(--ai); }
	.err { color: var(--bad); font-size: .8rem; }
	@media (max-width: 760px) {
		.cc.row { grid-template-columns: 1fr; }
		.lbl { display: none; }
	}
</style>
