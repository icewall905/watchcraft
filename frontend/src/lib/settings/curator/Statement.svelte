<script lang="ts">
	import { tick, untrack } from 'svelte';
	import { focusable, nav } from '$lib/nav';
	import { session } from '$lib/stores/session.svelte';
	import { errMsg } from '$lib/hub/util';
	import WhyList from './WhyList.svelte';
	import { CONFIDENCE, curator, direction, type Snapshot, type Statement, type Teaching } from './api';
	let { statement, onchange, onwrong }: {
		statement: Statement; onchange: (s: Snapshot) => void; onwrong: (text: string) => void;
	} = $props();

	let why = $state<Teaching[] | null>(null), whyOpen = $state(false), whyError = $state('');
	let editing = $state(false), draft = $state(''), confirming = $state(false), busy = $state(false), error = $state('');
	let row = $state<HTMLElement>();

	async function loadWhy() {
		const v = session.version, sid = statement.sid;
		whyError = '';
		try { const r = await curator.why(sid); if (v === session.version && sid === statement.sid) why = r.teachings; }
		catch (e) { if (v === session.version) whyError = errMsg(e); }
	}
	// A re-woven statement may rest on different teachings: forget the cached answer.
	$effect(() => {
		void statement.updated; void statement.teaching_ids.join();
		untrack(() => { why = null; if (whyOpen) void loadWhy(); });
	});
	function toggleWhy() {
		whyOpen = !whyOpen;
		if (whyOpen && !why) void loadWhy();
	}
	async function edit(body: Parameters<typeof curator.editStatement>[1]) {
		if (busy) return;
		const v = session.version;
		busy = true; error = '';
		try {
			const r = await curator.editStatement(statement.sid, body);
			if (v !== session.version) return;
			if (body.remove) await removed(r); else onchange(r);
			editing = false; confirming = false;
		} catch (e) { if (v === session.version) error = errMsg(e); }
		finally { busy = false; }
	}
	/** Keep the remote's focus nearby: the next row, else the previous one, else the section heading. */
	async function removed(r: Snapshot) {
		const li = row, card = li?.closest('section');
		const neighbours = [li?.nextElementSibling, li?.previousElementSibling].filter(Boolean) as Element[];
		const ids = neighbours.map((n) => n.getAttribute('data-nav-row'));
		onchange(r);
		await tick();
		for (const id of ids) {
			const target = card?.querySelector<HTMLElement>(`[data-nav-row="${id}"] button`);
			if (target?.isConnected) return nav.focus(target);
		}
		const heading = (card?.isConnected ? card.querySelector('h4') : null) ?? document.getElementById('curator-knowledge');
		heading?.focus();
	}
	function startEdit() { draft = statement.text; editing = true; }
</script>

<li bind:this={row} class="statement" class:avoid={statement.polarity === -1} data-nav-row={`statement-${statement.sid}`}>
	<div class="head">
		<span class="dir">{direction(statement.polarity)}</span>
		<span class="badge {statement.confidence}">{CONFIDENCE[statement.confidence]}</span>
		{#if statement.locked}<span class="lock" role="img" aria-label="Locked" title="Locked: the nightly weave leaves this alone">🔒</span>{/if}
	</div>
	{#if editing}
		<form class="hstack" onsubmit={(e) => { e.preventDefault(); void edit({ text: draft.trim() }); }}>
			<input class="input grow" aria-label="Statement text" maxlength="300" bind:value={draft} use:focusable />
			<button class="btn" disabled={busy || draft.trim().length < 3 || draft.trim() === statement.text} use:focusable>Save</button>
			<button type="button" class="btn ghost" use:focusable onclick={() => (editing = false)}>Cancel</button>
		</form>
	{:else}<p class="text">{statement.text}</p>{/if}
	<div class="hstack actions">
		<button class="btn ghost" aria-expanded={whyOpen} use:focusable onclick={toggleWhy}>Why?</button>
		{#if !editing}<button class="btn ghost" disabled={busy} use:focusable onclick={startEdit}>Edit</button>{/if}
		<button class="btn ghost" use:focusable onclick={() => onwrong(statement.text)}>That's wrong</button>
		<button class="btn ghost" aria-pressed={statement.locked} disabled={busy} use:focusable onclick={() => edit({ locked: !statement.locked })}>{statement.locked ? 'Unlock' : 'Lock'}</button>
		{#if confirming}
			<button class="btn danger" disabled={busy} use:focusable onclick={() => edit({ remove: true })}>Confirm remove</button>
			<button class="btn ghost" use:focusable onclick={() => (confirming = false)}>Keep</button>
		{:else}<button class="btn ghost" disabled={busy} use:focusable onclick={() => (confirming = true)}>Remove</button>{/if}
	</div>
	{#if error}<p role="alert" class="err small">{error}</p>{/if}
	{#if whyOpen}
		<div class="whybox">
			{#if whyError}<p role="alert" class="err small">{whyError}</p><button class="btn ghost" use:focusable onclick={loadWhy}>Retry</button>
			{:else if why === null}<p class="small muted" role="status">Looking up why…</p>
			{:else}<WhyList teachings={why} />{/if}
		</div>
	{/if}
</li>

<style>
	.statement { display: grid; gap: 0.4rem; padding: 0.75rem 0 0.75rem 0.85rem; border-left: 3px solid var(--ok); }
	.statement.avoid { border-left-color: var(--bad); }
	.statement + :global(.statement) { border-top: 1px solid var(--line); }
	.head { display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center; }
	.dir { font-size: 0.75rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: var(--ok); }
	.avoid .dir { color: var(--bad); }
	.text { margin: 0; overflow-wrap: anywhere; }
	.badge { font-size: 0.72rem; padding: 0.05rem 0.5rem; border-radius: var(--r-pill); border: 1px solid var(--line); background: var(--surface-2); color: var(--muted); }
	.badge.explicit { color: var(--accent); border-color: var(--accent); }
	.badge.established { color: var(--text); }
	.actions { gap: 0.25rem; }
	.actions .btn { min-height: 2.1rem; padding: 0.3rem 0.7rem; font-size: 0.85rem; }
	.grow { flex: 1 1 12rem; width: auto; }
	.whybox { padding: 0.6rem 0.75rem; border-radius: var(--r-md); background: var(--surface-2); }
	.err { color: var(--bad); }
</style>
