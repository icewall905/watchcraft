<script lang="ts">
	import { onMount } from 'svelte';
	import { focusable } from '$lib/nav';
	import SourceScout from '$lib/hub/SourceScout.svelte';
	import Modal from '$lib/components/Modal.svelte';
	import Select from '$lib/components/form/Select.svelte';
	import TextField from '$lib/components/form/TextField.svelte';
	import { toast } from '$lib/stores/toast.svelte';
	import { hub, ago } from '$lib/hub/api';
	import { errMsg } from '$lib/hub/ui';
	import ErrorBox from '$lib/hub/ErrorBox.svelte';
	import type { Registry, Source } from '$lib/hub/types';

	let registry = $state<Registry | null>(null);
	let sources = $state<Source[]>([]);
	let loading = $state(true);
	let error = $state('');
	let provider = $state('rss');
	let url = $state('');
	let name = $state('');
	let adding = $state(false);
	let formErr = $state('');
	let confirm = $state<Source | null>(null);
	let testing = $state<number | null>(null);
	let results = $state<Record<number, { ok: boolean; count: number; sample: { title: string; url: string }[]; error: string }>>({});
	let revisions = $state<Record<number, Awaited<ReturnType<typeof hub.sourceRevisions>>>>({});
	let loadingHistory = $state<number | null>(null);
	let checks = $state<Record<number, Awaited<ReturnType<typeof hub.sourceChecks>>>>({});
	let loadingChecks = $state<number | null>(null);

	const providers = $derived(registry?.providers ?? []);
	const chosen = $derived(providers.find((p) => p.key === provider));
	const addable = $derived(providers.filter((p) => p.available).map((p) => ({ value: p.key, label: p.label, hint: p.description })));
	const label = (k: string) => providers.find((p) => p.key === k)?.label ?? k;

	// Show the smallest span containing all changes, without implying that every word in it changed.
	function changedSpan(before: string, after: string) {
		const a = before.split(' '), b = after.split(' ');
		let start = 0, endA = a.length, endB = b.length;
		while (start < endA && start < endB && a[start] === b[start]) start++;
		while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) { endA--; endB--; }
		const excerpt = (words: string[]) => {
			const text = words.join(' ');
			return text.length > 700 ? text.slice(0, 700) + '…' : text;
		};
		return { before: excerpt(a.slice(start, endA)), after: excerpt(b.slice(start, endB)) };
	}

	async function load() {
		try { [registry, sources] = await Promise.all([hub.registry(), hub.sources()]); error = ''; if (!providers.find((p) => p.key === provider && p.available)) provider = providers.find((p) => p.available)?.key ?? provider; }
		catch (e) { error = errMsg(e); } finally { loading = false; }
	}
	onMount(load);

	async function add(e: SubmitEvent) {
		e.preventDefault(); formErr = '';
		if (chosen?.needs_url && !url.trim()) { formErr = 'This provider needs a URL.'; return; }
		adding = true;
		try {
			const s = await hub.createSource({ provider, url: chosen?.needs_url ? url.trim() : '', name: name.trim() });
			sources = [...sources, s]; url = ''; name = ''; toast.success('Source added');
		} catch (err) { formErr = errMsg(err); } finally { adding = false; }
	}
	async function toggle(s: Source) {
		try { const u = await hub.updateSource(s.id, { enabled: !s.enabled }); sources = sources.map((x) => (x.id === s.id ? { ...x, ...u } : x)); } catch (e) { error = errMsg(e); }
	}
	async function test(s: Source) {
		testing = s.id;
		try { results[s.id] = await hub.testSource(s.id); await load(); if (checks[s.id]) await checkHistory(s); } catch (e) { results[s.id] = { ok: false, count: 0, sample: [], error: errMsg(e) }; } finally { testing = null; }
	}
	async function history(s: Source) {
		loadingHistory = s.id;
		try { revisions[s.id] = await hub.sourceRevisions(s.id); } catch (e) { error = errMsg(e); } finally { loadingHistory = null; }
	}
	async function checkHistory(s: Source) {
		loadingChecks = s.id;
		try { checks[s.id] = await hub.sourceChecks(s.id); } catch (e) { error = errMsg(e); } finally { loadingChecks = null; }
	}
	async function remove() {
		if (!confirm) return;
		const s = confirm;
		try { await hub.deleteSource(s.id); sources = sources.filter((x) => x.id !== s.id); toast.success('Source deleted'); } catch (e) { error = errMsg(e); } finally { confirm = null; }
	}
	const health = (s: Source) => (s.last_error ? 'bad' : s.last_success ? 'ok' : 'new');
</script>

<svelte:head><title>Sources · WatchCraft</title></svelte:head>

<div class="page">
	<header>
		<p class="eyebrow">AUTOMATION</p>
		<h1>Where WatchCraft is allowed to look</h1>
		<p class="muted">Curators can only visit what is listed here. Public sources only. Nothing is fetched outside these providers.</p>
	</header>

	<SourceScout onAdded={load} />

	{#if error}<ErrorBox message={error} onretry={load} />{/if}

	{#if loading}
		<div class="skeleton" style="height:12rem;margin-bottom:1rem"></div><div class="skeleton" style="height:14rem"></div>
	{:else if registry}
		<section>
			<h2>Providers</h2>
			<div class="provs">
				{#each providers as p (p.key)}
					<div class="prov" class:off={!p.available}>
						<div class="hstack"><b>{p.label}</b><span class="spacer"></span><span class="av {p.available ? 'on' : ''}">{p.available ? 'Available' : 'Unavailable'}</span></div>
						<p class="small muted">{p.description}</p>
						<div class="caps">{#each p.capabilities as cap}<span class="cap">{cap}</span>{/each}<span class="cap mode">{p.mode}</span></div>
						{#if !p.available}<p class="small reason">{p.reason}{#if p.key === 'web_search'} <a href="/settings?tab=search" use:focusable>Configure web search</a>{/if}</p>{/if}
					</div>
				{/each}
			</div>
		</section>

		<section>
			<h2>Add a source</h2>
			<form class="add panel" onsubmit={add}>
				<Select label="Provider" bind:value={provider} options={addable} group="src-add" />
				{#if chosen?.needs_url}<TextField type="url" label="URL" bind:value={url} placeholder={provider === 'github_releases' ? 'https://github.com/owner/repository' : 'https://example.org/feed.xml'} group="src-add" />{/if}
				{#if provider === 'page_watch'}<p class="small muted">Attach this source to a curator to check it on that curator’s schedule. The first successful run establishes a baseline; later text changes appear in the source history.</p>{/if}
				<TextField label="Name (optional)" bind:value={name} placeholder="Shown in lists" group="src-add" />
				{#if formErr}<div class="err" role="alert">{formErr}</div>{/if}
				<div><button class="btn primary" type="submit" disabled={adding} use:focusable>{adding ? 'Adding…' : 'Add source'}</button></div>
			</form>
		</section>

		<section>
			<h2>Configured sources ({sources.length})</h2>
			{#if !sources.length}
				<div class="empty"><p>No sources yet. Add a feed above, or create a curator, which adds providers it needs.</p></div>
			{:else}
				<ul class="list">
					{#each sources as s (s.id)}
						<li class="row">
							<span class="dot {health(s)}" title={health(s)}></span>
							<div class="info">
								<b>{s.name || label(s.provider)}</b>
								<span class="small muted">{label(s.provider)}{#if s.url} · <a href={s.url} target="_blank" rel="noopener noreferrer" use:focusable>{s.url}</a>{/if}</span>
								<span class="small faint">
									{#if s.last_success}ok {ago(s.last_success)}{:else}never succeeded{/if}
									{#if s.last_checked} · checked {ago(s.last_checked)}{/if}
									· {s.item_count} items · {s.curator_ids.length} curator{s.curator_ids.length === 1 ? '' : 's'}
								</span>
								{#if s.last_error}<span class="small bad" title={s.last_error}>⚠ {s.last_error}</span>{/if}
								{#if results[s.id]}
									{@const r = results[s.id]}
									<div class="test" role="status">
										{#if r.ok}<b class="okc">Works. {r.count} entries.</b>{#each r.sample.slice(0, 3) as it}<span class="small">· {it.title}</span>{/each}
										{:else}<b class="bad">Test failed: {r.error || 'unknown error'}</b>{/if}
									</div>
								{/if}
								{#if s.provider === 'page_watch' && revisions[s.id]}
									<div class="test"><b>Page history</b>
										{#if !revisions[s.id].length}<span class="small muted">No observations yet. Run a curator that follows this source.</span>{/if}
										{#each revisions[s.id] as revision, i (revision.id)}
											<details>
												<summary use:focusable>{i === revisions[s.id].length - 1 ? 'Earliest retained observation' : 'Page changed'} · {ago(revision.observed_at)}</summary>
												{#if i < revisions[s.id].length - 1}
													{@const previous = revisions[s.id][i + 1]}
													{#if previous.title !== revision.title}<p class="small"><b>Title before:</b> {previous.title}<br /><b>Title after:</b> {revision.title}</p>{/if}
													{#if previous.body !== revision.body}
														{@const change = changedSpan(previous.body, revision.body)}
														<p class="small muted">Text between the first and last change; excerpts may include unchanged words and are limited to 700 characters.</p>
														<p class="small"><b>Before:</b> {change.before || '(nothing)'}</p><p class="small"><b>After:</b> {change.after || '(nothing)'}</p>
													{/if}
												{/if}
												<details><summary use:focusable>Full observed text</summary><b>{revision.title}</b><p class="small">{revision.body}</p></details>
											</details>
										{/each}
									</div>
								{/if}
								{#if checks[s.id]}
									<div class="test"><b>Recent checks</b>
										{#if !checks[s.id].length}<span class="small muted">No recorded checks yet.</span>{/if}
										{#each checks[s.id] as check (check.id)}
											<span class="small" class:bad={check.status !== 'ok'}>{check.status === 'ok' ? 'Succeeded' : check.status === 'partial' ? 'Partially succeeded' : 'Failed'} · {check.trigger === 'test' ? 'connection test' : check.trigger === 'validation' ? 'Scout validation' : check.trigger === 'shared' ? 'reused source fetch' : 'curator run'} · {ago(check.checked_at)} · {check.item_count} items{check.error ? ` · ${check.error}` : ''}</span>
										{/each}
									</div>
								{/if}
							</div>
							<div class="acts">
								<button class="btn" role="switch" aria-checked={s.enabled} use:focusable onclick={() => toggle(s)}>{s.enabled ? 'Enabled' : 'Disabled'}</button>
								<button class="btn" disabled={testing === s.id} use:focusable onclick={() => test(s)}>{testing === s.id ? 'Testing…' : 'Test connection'}</button>
								<button class="btn" disabled={loadingChecks === s.id} use:focusable onclick={() => checkHistory(s)}>{loadingChecks === s.id ? 'Loading…' : 'Check history'}</button>
								{#if s.provider === 'page_watch'}<button class="btn" disabled={loadingHistory === s.id} use:focusable onclick={() => history(s)}>{loadingHistory === s.id ? 'Loading…' : 'View history'}</button>{/if}
								<button class="btn ghost danger" use:focusable onclick={() => (confirm = s)}>Delete</button>
							</div>
						</li>
					{/each}
				</ul>
			{/if}
		</section>
	{/if}
</div>

{#if confirm}
	<Modal title="Delete source?" size="sm" onClose={() => (confirm = null)}>
		<p><b>{confirm.name || confirm.url || label(confirm.provider)}</b> will be removed{#if confirm.curator_ids.length} and detached from {confirm.curator_ids.length} curator(s){/if}.</p>
		{#snippet footer()}
			<button class="btn ghost" use:focusable={{ autofocus: true }} onclick={() => (confirm = null)}>Cancel</button>
			<button class="btn danger" use:focusable onclick={remove}>Delete</button>
		{/snippet}
	</Modal>
{/if}

<style>
	.eyebrow { font-size: .7rem; letter-spacing: .15em; color: var(--accent); font-weight: 700; margin: 0; }
	h1 { font-size: clamp(1.7rem, 3vw, 2.6rem); margin: .2rem 0; }
	section { margin-top: 2rem; }
	h2 { font-size: 1.2rem; }
	.provs { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 17rem), 1fr)); gap: .8rem; }
	.prov { padding: 1rem; border: 1px solid var(--line); border-radius: var(--r-lg); background: var(--surface); display: grid; gap: .5rem; align-content: start; }
	.prov.off { opacity: .75; }
	.prov p { margin: 0; }
	.av { font-size: .7rem; font-weight: 700; color: var(--faint); }
	.av.on { color: var(--ok); }
	.caps { display: flex; flex-wrap: wrap; gap: .3rem; }
	.cap { font-size: .7rem; padding: .1rem .5rem; border-radius: var(--r-pill); background: var(--surface-2); color: var(--muted); }
	.cap.mode { border: 1px solid var(--line-strong); background: none; }
	.reason { color: var(--warn); }
	.add { display: grid; gap: .9rem; max-width: 40rem; }
	.err { padding: .7rem 1rem; border-radius: var(--r-md); border: 1px solid rgba(255,92,122,.45); background: rgba(255,92,122,.1); color: #ffd1d9; }
	.list { list-style: none; margin: 0; padding: 0; display: grid; gap: .7rem; }
	.row { display: grid; grid-template-columns: auto 1fr auto; gap: .9rem; align-items: start; padding: 1rem; border: 1px solid var(--line); border-radius: var(--r-lg); background: var(--surface); }
	.dot { width: .8rem; height: .8rem; border-radius: 50%; margin-top: .45rem; background: var(--faint); }
	.dot.ok { background: var(--ok); } .dot.bad { background: var(--bad); }
	.info { display: grid; gap: .2rem; min-width: 0; overflow-wrap: anywhere; }
	.bad { color: var(--bad); } .okc { color: var(--ok); }
	.test { display: grid; gap: .1rem; margin-top: .3rem; padding: .5rem .7rem; border-radius: var(--r-md); background: rgba(255,255,255,.04); }
	.acts { display: flex; gap: .4rem; flex-wrap: wrap; justify-content: flex-end; }
	@media (max-width: 760px) { .row { grid-template-columns: auto 1fr; } .acts { grid-column: 1 / -1; justify-content: flex-start; } }
</style>
