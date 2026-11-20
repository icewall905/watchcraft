<script lang="ts">
	import { focusable } from '$lib/nav';
	import { errMsg } from '$lib/hub/util';
	import { session } from '$lib/stores/session.svelte';
	import { CAUSE, curator, direction, longDate, sectionTitle, type HistoryEntry, type Version } from './api';
	let { history, current, onrestored, onclose }: {
		history: HistoryEntry[]; current: number | null; onrestored: () => void; onclose: () => void;
	} = $props();

	let selected = $state<Version | null>(null), loading = $state<number | null>(null), error = $state('');
	let confirming = $state(false), busy = $state(false);

	async function pick(h: HistoryEntry) {
		const v = session.version;
		loading = h.id; error = ''; confirming = false;
		try { const r = await curator.version(h.id); if (v === session.version) selected = r; }
		catch (e) { if (v === session.version) error = errMsg(e); }
		finally { loading = null; }
	}
	async function restore() {
		if (!selected || busy) return;
		const v = session.version;
		busy = true;
		try { await curator.restoreVersion(selected.id); if (v === session.version) { selected = null; confirming = false; onrestored(); } }
		catch { /* the request already showed the error */ }
		finally { busy = false; }
	}
	const counts = (h: HistoryEntry) => [h.added && `+${h.added}`, h.changed && `~${h.changed}`, h.removed && `−${h.removed}`, h.merged && `${h.merged} merged`].filter(Boolean).join(' · ') || 'no changes';
</script>

<div class="history stack">
	<div class="hstack">
		<h3>History</h3><span class="spacer"></span>
		<button class="btn ghost" use:focusable onclick={onclose}>Close history</button>
	</div>
	<ol class="versions">
		{#each history as h (h.id)}
			<li data-nav-row={`version-${h.id}`}>
				<button class="version" class:on={selected?.id === h.id} aria-pressed={selected?.id === h.id} disabled={loading !== null} use:focusable onclick={() => pick(h)}>
					<strong>Version {h.version}</strong>
					<span>{CAUSE[h.cause] ?? h.cause}{h.id === current ? ' · current' : ''}</span>
					<span class="small muted">{longDate(h.created_at)} · {counts(h)}</span>
				</button>
			</li>
		{/each}
	</ol>
	{#if error}<p role="alert" class="err small">{error}</p>{/if}
	{#if selected}
		{@const c = selected.changes}
		<div class="diff stack" role="region" aria-label={`Changes in version ${selected.version}`}>
			<h4>Version {selected.version} — {CAUSE[selected.cause] ?? selected.cause}</h4>
			<ul>
				{#each c.added as a (a.sid)}<li class="add">+ <span class="muted">{sectionTitle(a.section)}, {direction(a.polarity)}:</span> {a.text}</li>{/each}
				{#each c.changed as ch (ch.sid)}<li class="edit">~ <span class="muted">{sectionTitle(ch.section)}:</span> <del>{ch.before}</del> → {ch.after}</li>{/each}
				{#each c.removed as r (r.sid)}<li class="remove">− <span class="muted">{sectionTitle(r.section)}:</span> {r.text}</li>{/each}
				{#if c.merged.length}<li class="muted">Folded in {c.merged.length} {c.merged.length === 1 ? 'teaching' : 'teachings'}.</li>{/if}
				{#if !c.added.length && !c.changed.length && !c.removed.length && !c.merged.length}<li class="muted">No changes in this version.</li>{/if}
			</ul>
			{#if selected.id !== current}
				<div class="hstack">
					{#if confirming}
						<button class="btn danger" disabled={busy} use:focusable onclick={restore}>Yes, restore version {selected.version}</button>
						<button class="btn ghost" use:focusable onclick={() => (confirming = false)}>Cancel</button>
					{:else}<button class="btn" use:focusable onclick={() => (confirming = true)}>Restore this version</button>{/if}
				</div>
			{/if}
		</div>
	{/if}
</div>

<style>
	.history { padding: 1rem; border: 1px solid var(--line-strong); border-radius: var(--r-lg); background: var(--surface); gap: 0.75rem; }
	h3, h4 { margin: 0; }
	.versions { list-style: none; margin: 0; padding: 0; display: grid; gap: 0.35rem; max-height: 18rem; overflow-y: auto; }
	.version { width: 100%; display: grid; gap: 0.1rem; text-align: left; padding: 0.5rem 0.75rem; border-radius: var(--r-md); border: 1px solid var(--line); background: var(--surface-2); color: var(--text); cursor: pointer; }
	.version.on { border-color: var(--accent); }
	.diff ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 0.3rem; overflow-wrap: anywhere; }
	.add { color: var(--ok); }
	.edit { color: var(--warn); }
	.remove { color: var(--bad); }
	.err { color: var(--bad); }
</style>
