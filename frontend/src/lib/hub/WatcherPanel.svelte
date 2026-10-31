<script lang="ts">
	import { onMount } from 'svelte';
	import { focusable } from '$lib/nav';
	import TextField from '$lib/components/form/TextField.svelte';
	import Toggle from '$lib/components/form/Toggle.svelte';
	import { toast } from '$lib/stores/toast.svelte';
	import { hub, ago } from './api';
	import { errMsg } from './ui';
	import SampleList from './SampleList.svelte';
	import { describeRules } from './util';
	import type { Source, SourceTest, WatcherConfig, WatcherState } from './types';

	let { source, onchanged }: { source: Source; onchanged: (s: Source) => void } = $props();

	type Kind = 'text' | 'list' | 'bool' | 'number';
	const FIELDS: Record<string, { label: string; kind: Kind; hint: string; placeholder?: string }> = {
		content_selector: { label: 'Content area', kind: 'text', hint: 'CSS selector for the part of the page to watch, e.g. main or #changelog. Empty watches the whole page text.', placeholder: 'main' },
		item_selector: { label: 'Item selector', kind: 'text', hint: 'CSS selector matching each entry in the listing.', placeholder: 'article' },
		link_selector: { label: 'Link selector', kind: 'text', hint: 'Inside each item, the link to follow.', placeholder: 'a[href]' },
		title_selector: { label: 'Title selector', kind: 'text', hint: 'Inside each item; empty uses the link text.', placeholder: 'h2' },
		date_selector: { label: 'Date selector', kind: 'text', hint: 'Inside each item; a time element or text containing a date.', placeholder: 'time' },
		allowed_patterns: { label: 'Only these paths', kind: 'list', hint: 'One path pattern per line, starting with “/”, e.g. /blog/*. Empty allows every path.' },
		ignored_patterns: { label: 'Ignore these paths', kind: 'list', hint: 'One path pattern per line, e.g. /tag/*.' },
		ignore_selectors: { label: 'Ignore page parts', kind: 'list', hint: 'One CSS selector per line, e.g. nav or .cookie-banner. Removed before extracting.' },
		min_change_chars: { label: 'Minimum change (characters)', kind: 'number', hint: 'Smaller text changes are recorded but not treated as a change. 0–10000.', placeholder: '0' },
		strip_query: { label: 'Strip query strings', kind: 'bool', hint: 'Treat /post?ref=a and /post as the same URL.' },
		alert_on_change: { label: 'Alert when the page changes', kind: 'bool', hint: 'Raise an alert in Alerts for each detected change.' },
		auto_repair: { label: 'Repair automatically', kind: 'bool', hint: 'When extraction breaks, apply a proposed fix only if it passes a live test.' }
	};
	const ORDER = Object.keys(FIELDS);
	const REASON: Record<string, string> = { original: 'Original rules', edit: 'Edited', repair: 'Repair applied', 'auto-repair': 'Repaired automatically', rollback: 'Restored' };

	let w = $state<WatcherState | null>(null);
	let loadErr = $state('');
	let text = $state<Record<string, string>>({});
	let flags = $state<Record<string, boolean>>({});
	let busy = $state<'' | 'test' | 'save' | `restore-${number}`>('');
	let result = $state<SourceTest | null>(null);
	let resultLabel = $state('Test');
	let failedSave = $state(false);
	let failedRestore = $state<number | null>(null);
	let error = $state('');

	const fields = $derived(ORDER.filter((k) => w?.fields.includes(k)));

	function fill(config: WatcherConfig) {
		const c = config as Record<string, unknown>;
		const t: Record<string, string> = {}, f: Record<string, boolean> = {};
		for (const k of ORDER) {
			const v = c[k];
			if (FIELDS[k].kind === 'bool') f[k] = !!v;
			else if (FIELDS[k].kind === 'list') t[k] = Array.isArray(v) ? v.join('\n') : '';
			else t[k] = v === undefined || v === null ? '' : String(v);
		}
		text = t; flags = f;
	}

	function draft(): WatcherConfig {
		const out: Record<string, unknown> = {};
		for (const k of fields) {
			const kind = FIELDS[k].kind;
			if (kind === 'bool') { if (flags[k]) out[k] = true; continue; }
			const raw = (text[k] ?? '').trim();
			if (!raw) continue;
			if (kind === 'list') out[k] = raw.split('\n').map((x) => x.trim()).filter(Boolean);
			else if (kind === 'number') out[k] = Number(raw);
			else out[k] = raw;
		}
		return out as WatcherConfig;
	}

	async function load() {
		try { w = await hub.watcher(source.id); fill(w.config); loadErr = ''; } catch (e) { loadErr = errMsg(e); }
	}
	onMount(load);

	async function runTest() {
		busy = 'test'; error = ''; failedSave = false; failedRestore = null;
		try { const r = await hub.testWatcher(source.id, draft()); result = r; resultLabel = 'Dry run'; }
		catch (e) { result = null; error = errMsg(e); } finally { busy = ''; }
	}

	async function save(force = false) {
		busy = 'save'; error = ''; failedRestore = null;
		try {
			const r = await hub.saveWatcher(source.id, draft(), 'edit', force);
			result = r.test;
			resultLabel = 'Live test';
			failedSave = !r.saved;
			if (r.saved) {
				if (r.source) onchanged(r.source);
				if (r.test.ok) toast.success('Watcher rules saved');
				else toast.info('Rules saved without a passing test');
				await load();
			}
		} catch (e) { error = errMsg(e); } finally { busy = ''; }
	}

	async function restore(id: number, force = false) {
		busy = `restore-${id}`; error = ''; failedSave = false;
		try {
			const r = await hub.rollbackWatcher(source.id, id, force);
			result = r.test; resultLabel = 'Restored rules test';
			failedRestore = r.saved ? null : id;
			if (!r.saved) return;
			if (r.source) onchanged(r.source);
			await load();
		} catch (e) { error = errMsg(e); } finally { busy = ''; }
	}
</script>

<div class="panel-in" aria-label="Watcher rules">
	<b>Watcher rules</b>
	{#if loadErr}<div class="err" role="alert">{loadErr}</div>
	{:else if !w}<span class="small muted">Loading rules…</span>
	{:else}
		<p class="small muted">Rules are tested against the live page. Saving runs the test first and only stores rules that extract something, unless you choose to save anyway.</p>
		<div class="fields">
			{#each fields as k (k)}
				{@const f = FIELDS[k]}
				{#if f.kind === 'bool'}
					<Toggle label={f.label} hint={f.hint} bind:checked={flags[k]} />
				{:else if f.kind === 'list'}
					<TextField label={f.label} hint={f.hint} multiline rows={3} bind:value={text[k]} />
				{:else}
					<TextField label={f.label} hint={f.hint} placeholder={f.placeholder} type={f.kind === 'number' ? 'number' : 'text'} inputmode={f.kind === 'number' ? 'numeric' : undefined} bind:value={text[k]} />
				{/if}
			{/each}
		</div>
		<div class="hstack acts">
			<button class="btn" disabled={!!busy} use:focusable={{ group: `rules-acts-${source.id}` }} onclick={runTest}>{busy === 'test' ? 'Testing…' : 'Test rules'}</button>
			<button class="btn primary" disabled={!!busy} use:focusable={{ group: `rules-acts-${source.id}` }} onclick={() => save(false)}>{busy === 'save' ? 'Saving…' : 'Save'}</button>
		</div>
		{#if error}<div class="err" role="alert">{error}</div>{/if}
		{#if result}
			<SampleList {result} label={resultLabel} />
			{#if failedRestore !== null}
				<div class="warnbox" role="alert">
					<span class="small">Not restored: those rules did not pass a live test now. Restore anyway only if you expect the site to match them again.</span>
					<button class="btn" disabled={!!busy} use:focusable onclick={() => restore(failedRestore as number, true)}>Restore anyway</button>
				</div>
			{/if}
			{#if failedSave}
				<div class="warnbox" role="alert">
					<span class="small">Not saved: the rules did not pass a live test. Save anyway only if the site is temporarily down or you will fix it later.</span>
					<button class="btn" disabled={!!busy} use:focusable onclick={() => save(true)}>Save anyway</button>
				</div>
			{/if}
		{/if}

		<details>
			<summary use:focusable>Rule history ({w.revisions.length})</summary>
			{#if !w.revisions.length}<p class="small muted">No saved rule changes yet.</p>{/if}
			<ul class="revs">
				{#each w.revisions as rev, i (rev.id)}
					<li>
						<span class="small"><b>{REASON[rev.reason] ?? rev.reason}</b> · {ago(rev.created_at)} · {rev.item_count} items{#if i === 0} · current{/if}</span>
						<span class="small faint rules">{describeRules(rev.config)}</span>
						{#if i > 0}<button class="btn" disabled={!!busy} use:focusable onclick={() => restore(rev.id)}>{busy === `restore-${rev.id}` ? 'Restoring…' : 'Restore'}</button>{/if}
					</li>
				{/each}
			</ul>
		</details>
	{/if}
</div>

<style>
	.panel-in { display: grid; gap: .6rem; margin-top: .3rem; padding: .7rem .8rem; border-radius: var(--r-md); background: rgba(255,255,255,.04); }
	.panel-in p { margin: 0; }
	.fields { display: grid; gap: .7rem; max-width: 40rem; }
	.acts { gap: .4rem; flex-wrap: wrap; }
	.err { padding: .6rem .9rem; border-radius: var(--r-md); border: 1px solid rgba(255,92,122,.45); background: rgba(255,92,122,.1); color: #ffd1d9; }
	.warnbox { display: flex; gap: .6rem; align-items: center; flex-wrap: wrap; padding: .5rem .7rem; border-radius: var(--r-md); border: 1px solid var(--warn); }
	.revs { list-style: none; margin: .4rem 0 0; padding: 0; display: grid; gap: .45rem; }
	.revs li { display: grid; gap: .1rem; justify-items: start; }
	.rules { overflow-wrap: anywhere; }
</style>
