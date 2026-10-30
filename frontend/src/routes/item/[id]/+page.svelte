<script lang="ts">
	import { page } from '$app/state';
	import { focusable } from '$lib/nav';
	import { hub, ago, host } from '$lib/hub/api';
	import { str, num, list, bool, fmtDate, label, safeUrl, sevClass, duration, errMsg } from '$lib/hub/util';
	import type { ItemDetail, Collection, Topic } from '$lib/hub/types';
	import ContentCard from '$lib/hub/ContentCard.svelte';

	let item = $state<ItemDetail | null>(null);
	let loading = $state(true);
	let error = $state('');
	let collections = $state<Collection[]>([]);
	let topics = $state<Topic[]>([]);
	let notes = $state('');
	let notesStatus = $state('');
	let tagInput = $state('');
	let pickCol = $state('');
	let pickTopic = $state('');
	let msg = $state('');
	let seq = 0;

	const id = $derived(Number(page.params.id));

	async function load() {
		const my = ++seq;
		loading = true; error = '';
		try {
			const r = await hub.item(id);
			if (my !== seq) return;
			item = r; notes = r.state.notes ?? '';
			hub.setState(id, { read: true }).catch(() => {});
			hub.collections().then((c) => (collections = c)).catch(() => {});
			hub.topics('knowledge').then((t) => (topics = t)).catch(() => {});
		} catch (e) { if (my === seq) error = errMsg(e); }
		finally { if (my === seq) loading = false; }
	}
	$effect(() => { void id; load(); });

	const m = $derived(item?.meta ?? {});
	const ai = $derived(item && typeof item.enrichment?.summary === 'string' ? (item.enrichment.summary as string) : '');
	const HANDLED = new Set(['ingredients', 'references', 'releases', 'instructions', 'steps', 'affected', 'materials', 'video_id']);
	const rows = $derived(
		Object.entries(m).filter(([k, v]) => !HANDLED.has(k) && v !== null && v !== '' && v !== undefined && (typeof v !== 'object' || (Array.isArray(v) && v.every((x) => typeof x !== 'object'))))
			.map(([k, v]) => [k, Array.isArray(v) ? v.join(', ') : typeof v === 'boolean' ? (v ? 'Yes' : 'No') : String(v)] as [string, string])
			.filter(([, v]) => v !== '')
	);
	const itemCols = $derived(collections.filter((c) => item?.collections.includes(c.id)));

	function textOf(x: unknown): string {
		if (x === null || x === undefined) return '';
		if (typeof x === 'object') { const o = x as Record<string, unknown>; return String(o.text ?? o.name ?? o.title ?? o.product ?? o.url ?? ''); }
		return String(x);
	}
	function linkOf(x: unknown): string { return typeof x === 'string' ? safeUrl(x) : x && typeof x === 'object' ? safeUrl((x as Record<string, unknown>).url) : ''; }

	async function patch(p: Parameters<typeof hub.setState>[1]) {
		if (!item) return;
		try { const s = await hub.setState(item.id, p); item = { ...item, state: s }; msg = ''; return s; }
		catch (e) { msg = errMsg(e); }
	}
	async function saveNotes() {
		if (!item || notes === item.state.notes) return;
		notesStatus = 'Saving…';
		const s = await patch({ notes });
		notesStatus = s ? 'Saved' : 'Not saved';
	}
	async function addTag(e: SubmitEvent) {
		e.preventDefault();
		const t = tagInput.trim().replace(/^#/, '');
		if (!t || !item || item.state.tags.includes(t)) { tagInput = ''; return; }
		tagInput = '';
		await patch({ tags: [...item.state.tags, t] });
	}
	const rmTag = (t: string) => item && patch({ tags: item.state.tags.filter((x) => x !== t) });

	async function addCol() {
		const c = Number(pickCol);
		if (!c || !item) return;
		try { await hub.addToCollection(c, item.id); item = { ...item, collections: [...item.collections, c] }; pickCol = ''; msg = ''; }
		catch (e) { msg = errMsg(e); }
	}
	async function rmCol(c: number) {
		if (!item) return;
		try { await hub.removeFromCollection(c, item.id); item = { ...item, collections: item.collections.filter((x) => x !== c) }; }
		catch (e) { msg = errMsg(e); }
	}
	async function addTopic() {
		const t = Number(pickTopic);
		if (!t || !item) return;
		try { await hub.linkTopic(t, item.id); msg = `Added to knowledge topic "${topics.find((x) => x.id === t)?.name}".`; pickTopic = ''; }
		catch (e) { msg = errMsg(e); }
	}
</script>

<svelte:head><title>{item ? `${item.title} · ` : ''}WatchCraft</title></svelte:head>

<div class="page">
	<a class="back small muted" href="/discover" use:focusable>← Back to Discover</a>

	{#if loading}
		<div class="stack" aria-busy="true" aria-label="Loading"><div class="skeleton" style="height:3rem;width:70%"></div><div class="skeleton" style="height:14rem"></div><div class="skeleton" style="height:8rem"></div></div>
	{:else if error || !item}
		<div class="empty" role="alert"><p>Couldn't load this item: {error || 'not found'}</p><button class="btn primary" use:focusable onclick={load}>Retry</button></div>
	{:else}
		<div class="layout">
			<article class="read">
				<p class="eyebrow"><span class="type">{item.type}</span>
					{#if item.publisher || item.author} · {[item.publisher, item.author].filter(Boolean).join(' · ')}{/if}</p>
				<h1>{item.title}</h1>
				<p class="dates muted small">
					{#if item.published_at}Published {fmtDate(item.published_at)} ({ago(item.published_at)}) · {/if}Discovered {fmtDate(item.first_seen)} ({ago(item.first_seen)})
				</p>
				<div class="hstack top">
					<a class="btn primary" href={safeUrl(item.url) || undefined} target="_blank" rel="noopener noreferrer" use:focusable>Open original ↗ <span class="faint host">{host(item.url)}</span></a>
					{#if item.type === 'video' && str(m, 'video_id')}<a class="btn" href={`/watch/${encodeURIComponent(str(m, 'video_id'))}`} use:focusable>Watch in WatchCraft</a>{/if}
					<button class="btn" aria-pressed={item.state.saved} use:focusable onclick={() => patch({ saved: !item!.state.saved })}>{item.state.saved ? '✓ Saved' : 'Save'}</button>
					<button class="btn" aria-pressed={item.state.favorite} use:focusable onclick={() => patch({ favorite: !item!.state.favorite })}>{item.state.favorite ? '★ Favorite' : '☆ Favorite'}</button>
					<button class="btn" aria-pressed={item.state.read_later} use:focusable onclick={() => patch({ read_later: !item!.state.read_later })}>{item.state.read_later ? 'Queued' : 'Read later'}</button>
				</div>
				{#if msg}<p class="err small" role="alert">{msg}</p>{/if}

				{#if item.image}<img class="hero" src={item.image} alt="" referrerpolicy="no-referrer" />{/if}

				{#if item.summary}
					<section><h2>Summary <span class="tag">From source</span></h2><p class="prose">{item.summary}</p></section>
				{/if}
				{#if ai}
					<section class="aib"><h2>AI summary <span class="tag ai">AI-generated</span></h2><p class="prose">{ai}</p></section>
				{/if}

				{#if item.type === 'advisory'}
					<section>
						<h2>Advisory</h2>
						<p class="hstack">
							{#if str(m, 'cve_id')}<span class="b mono">{str(m, 'cve_id')}</span>{/if}
							{#if num(m, 'cvss_score') !== null}<span class="b {sevClass(str(m, 'severity'))}">CVSS {num(m, 'cvss_score')}{str(m, 'severity') ? ` · ${str(m, 'severity')}` : ''}</span>{/if}
							{#if bool(m, 'known_exploited')}<span class="b bad">Known exploited (CISA KEV)</span>{/if}
						</p>
						{#if list(m, 'affected').length}<h3>Affected</h3><ul class="prose">{#each list(m, 'affected') as a}<li>{textOf(a)}</li>{/each}</ul>{/if}
						{#if list(m, 'references').length}<h3>References</h3><ul class="prose">{#each list(m, 'references') as r}<li>{#if linkOf(r)}<a href={linkOf(r)} target="_blank" rel="noopener noreferrer">{linkOf(r)}</a>{:else}{textOf(r)}{/if}</li>{/each}</ul>{/if}
					</section>
				{/if}
				{#if list(m, 'ingredients').length}
					<section><h2>Ingredients</h2><ul class="prose">{#each list(m, 'ingredients') as g}<li>{textOf(g)}</li>{/each}</ul></section>
				{/if}
				{#if list(m, 'materials').length}
					<section><h2>Materials</h2><ul class="prose">{#each list(m, 'materials') as g}<li>{textOf(g)}</li>{/each}</ul></section>
				{/if}
				{#if list(m, 'instructions').length || list(m, 'steps').length}
					<section><h2>Steps</h2><ol class="prose">{#each (list(m, 'instructions').length ? list(m, 'instructions') : list(m, 'steps')) as g}<li>{textOf(g)}</li>{/each}</ol></section>
				{/if}
				{#if list(m, 'releases').length}
					<section><h2>Releases</h2><ul class="prose">{#each list(m, 'releases') as r}<li>{#if linkOf(r)}<a href={linkOf(r)} target="_blank" rel="noopener noreferrer">{textOf((r as Record<string, unknown>).tag ?? (r as Record<string, unknown>).name ?? r) || linkOf(r)}</a>{:else}{textOf(r)}{/if}</li>{/each}</ul></section>
				{/if}

				{#if rows.length}
					<section>
						<h2>Details</h2>
						<table class="meta"><tbody>
							{#each rows as [k, v]}
								<tr><th scope="row">{label(k)}</th><td>{#if safeUrl(v) && /^https?:/.test(v)}<a href={safeUrl(v)} target="_blank" rel="noopener noreferrer">{v}</a>{:else if k === 'duration' && num(m, k) !== null}{duration(num(m, k))}{:else}{v}{/if}</td></tr>
							{/each}
						</tbody></table>
					</section>
				{/if}

				{#if item.related.length}
					<section><h2>Related</h2><div class="stack">
						{#each item.related as _, i (item.related[i].id)}<ContentCard bind:item={item.related[i]} layout="compact" />{/each}
					</div></section>
				{/if}
			</article>

			<aside>
				<section class="box">
					<h2>Found by</h2>
					{#if item.curators.length}
						<ul class="prov">
							{#each item.curators as c}
								<li><a href={`/curators/${c.id}`} use:focusable>{c.name}</a>
									<span class="faint small"> · {ago(c.found_at)}{#if c.score !== null} · score {Math.round(c.score <= 1 ? c.score * 100 : c.score)}{/if}</span>
									{#if c.reason}<p class="small muted">{c.reason}</p>{/if}</li>
							{/each}
						</ul>
					{:else}<p class="muted small">Not found by a curator (added manually).</p>{/if}
				</section>

				<section class="box">
					<h2>Notes</h2>
					<textarea class="input" bind:value={notes} onblur={saveNotes} placeholder="Your private notes…" aria-label="Notes"></textarea>
					<p class="faint tiny" role="status">{notesStatus}</p>
				</section>

				<section class="box">
					<h2>Tags</h2>
					<div class="hstack">
						{#each item.state.tags as t}<span class="chip">#{t} <button class="x" aria-label={`Remove tag ${t}`} use:focusable onclick={() => rmTag(t)}>×</button></span>{/each}
					</div>
					<form class="hstack" onsubmit={addTag}>
						<input class="input" bind:value={tagInput} placeholder="Add tag" aria-label="Add tag" maxlength="40" />
						<button class="btn" use:focusable disabled={!tagInput.trim()}>Add</button>
					</form>
				</section>

				<section class="box">
					<h2>Collections</h2>
					<div class="hstack">{#each itemCols as c}<span class="chip">{c.name} <button class="x" aria-label={`Remove from ${c.name}`} use:focusable onclick={() => rmCol(c.id)}>×</button></span>{/each}</div>
					<div class="hstack">
						<select class="input" bind:value={pickCol} aria-label="Collection">
							<option value="">Add to collection…</option>
							{#each collections.filter((c) => !item!.collections.includes(c.id)) as c}<option value={c.id}>{c.name}</option>{/each}
						</select>
						<button class="btn" disabled={!pickCol} use:focusable onclick={addCol}>Add</button>
					</div>
					{#if !collections.length}<p class="faint tiny">No collections yet. Create one in Library.</p>{/if}
				</section>

				<section class="box">
					<h2>Knowledge topic</h2>
					<div class="hstack">
						<select class="input" bind:value={pickTopic} aria-label="Knowledge topic">
							<option value="">Add to topic…</option>
							{#each topics as t}<option value={t.id}>{t.name}</option>{/each}
						</select>
						<button class="btn" disabled={!pickTopic} use:focusable onclick={addTopic}>Add</button>
					</div>
					{#if !topics.length}<p class="faint tiny">No knowledge topics yet. <a href="/knowledge" class="u">Create one</a>.</p>{/if}
				</section>
			</aside>
		</div>
	{/if}
</div>

<style>
	.page { display: grid; gap: 1rem; align-content: start; }
	.back { display: inline-block; min-height: 2.75rem; line-height: 2.75rem; }
	.layout { display: grid; grid-template-columns: minmax(0, 1fr) 22rem; gap: 2rem; align-items: start; }
	.read { display: grid; gap: 1.1rem; max-width: 46rem; }
	.eyebrow { font-size: .78rem; color: var(--muted); }
	.type { text-transform: uppercase; letter-spacing: .1em; color: var(--accent); font-weight: 700; }
	h1 { font-size: clamp(1.7rem, 2vw + 1rem, 2.6rem); line-height: 1.15; overflow-wrap: anywhere; }
	.host { font-weight: 400; font-size: .8rem; }
	.top :global(.btn) { min-height: 2.75rem; }
	.hero { width: 100%; max-height: 26rem; object-fit: cover; border-radius: var(--r-lg); background: var(--bg-2); }
	section h2 { font-size: 1.15rem; margin-bottom: .5rem; display: flex; align-items: center; gap: .6rem; flex-wrap: wrap; }
	section h3 { font-size: .95rem; margin: .8rem 0 .3rem; color: var(--muted); }
	.prose { font-size: 1.05rem; line-height: 1.7; color: var(--text); max-width: 42rem; white-space: pre-line; }
	ul.prose, ol.prose { padding-left: 1.3rem; white-space: normal; }
	ul.prose li, ol.prose li { margin: .25rem 0; overflow-wrap: anywhere; }
	.prose a, .u { color: var(--accent-2); text-decoration: underline; }
	.tag { font-size: .68rem; font-weight: 700; text-transform: uppercase; letter-spacing: .08em; padding: .1rem .5rem; border-radius: var(--r-pill); background: var(--surface-2); color: var(--muted); }
	.tag.ai { background: rgba(157, 134, 255, .2); color: #d9d0ff; }
	.aib { border-left: 3px solid var(--ai); padding-left: 1rem; }
	.b { font-size: .8rem; font-weight: 650; padding: .15rem .6rem; border-radius: var(--r-pill); background: var(--surface-2); border: 1px solid var(--line); }
	.b.bad { color: #ffd1d9; border-color: rgba(255, 92, 122, .5); background: rgba(255, 92, 122, .14); }
	.b.warn { color: var(--warn); } .b.ok { color: var(--ok); }
	.mono { font-family: ui-monospace, monospace; }
	table.meta { width: 100%; border-collapse: collapse; font-size: .92rem; }
	.meta th, .meta td { text-align: left; padding: .5rem .6rem; border-bottom: 1px solid var(--line); vertical-align: top; overflow-wrap: anywhere; }
	.meta th { width: 10rem; color: var(--muted); font-weight: 600; }
	.meta a { color: var(--accent-2); text-decoration: underline; }
	aside { display: grid; gap: 1rem; position: sticky; top: calc(var(--topbar-h) + 1rem); }
	.box { border: 1px solid var(--line); background: var(--surface); border-radius: var(--r-lg); padding: 1rem 1.1rem; display: grid; gap: .6rem; }
	.box h2 { font-size: 1rem; margin: 0; }
	.box select.input { background: rgba(10, 8, 25, .85); flex: 1; min-width: 0; }
	.box textarea.input { min-height: 7rem; }
	.prov { list-style: none; padding: 0; margin: 0; display: grid; gap: .6rem; }
	.prov a { font-weight: 650; text-decoration: underline; }
	.x { background: none; border: 0; cursor: pointer; font-size: 1.1rem; min-width: 2rem; min-height: 2rem; color: var(--muted); }
	.err { color: var(--bad); }
	@media (max-width: 960px) { .layout { grid-template-columns: minmax(0, 1fr); } aside { position: static; } }
</style>
