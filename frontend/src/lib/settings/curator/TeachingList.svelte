<script lang="ts">
	import { focusable } from '$lib/nav';
	import { session } from '$lib/stores/session.svelte';
	import { errMsg } from '$lib/hub/util';
	import WhyList from './WhyList.svelte';
	import RejectDialog from './RejectDialog.svelte';
	import { because, curator, direction, sectionTitle, shortDate, surfaceIcon, type ChatMessage, type Teaching } from './api';
	let { status, surface = '', refresh = 0, onmessage, onchanged }: {
		status: 'pending' | 'merged' | 'rejected'; surface?: string; refresh?: number;
		onmessage?: (m: ChatMessage) => void; onchanged?: () => void;
	} = $props();

	let teachings = $state<Teaching[] | null>(null), error = $state(''), generation = 0;
	let open = $state<Record<number, boolean>>({}), rejecting = $state<number | null>(null), busy = $state(false);

	async function load() {
		const mine = ++generation; error = '';
		try { const r = await curator.teachings(status, surface); if (mine === generation) teachings = r.teachings; }
		catch (e) { if (mine === generation) error = errMsg(e); }
	}
	$effect(() => { void session.version; void refresh; void surface; teachings = null; rejecting = null; void load(); });

	async function act(run: () => Promise<void>) {
		if (busy) return;
		const v = session.version;
		busy = true;
		try { await run(); if (v !== session.version) return; rejecting = null; onchanged?.(); await load(); }
		catch { /* the request already showed the error */ }
		finally { busy = false; }
	}
	const forget = (t: Teaching, correction: string) => act(async () => {
		const v = session.version, r = await curator.reject(t.id, correction);
		if (r.message && v === session.version) onmessage?.(r.message);
	});
	const restore = (t: Teaching) => act(async () => { await curator.restoreTeaching(t.id); });
</script>

{#if error}<p role="alert" class="err small">{error}</p><button class="btn ghost" use:focusable onclick={load}>Retry</button>
{:else if teachings === null}<p class="small muted" role="status">Loading…</p>
{:else}
	<ul class="teachings">
		{#each teachings as t (t.id)}
			<li class="teaching" class:avoid={t.polarity === -1} data-nav-row={`teaching-${t.id}`}>
				<div class="head small">
					<span class="dir">{direction(t.polarity)}</span>
					<span class="muted">{surfaceIcon(t.surface)} {sectionTitle(t.section)} · {shortDate(t.created_at)}</span>
					{#if t.user_authored}<span class="badge">You told me</span>{/if}
				</div>
				<p class="text">{t.text}</p>
				{#if t.signals.length}<p class="small">{because(t.signals)}</p>{/if}
				{#if t.reason}<p class="small muted">{t.reason}</p>{/if}
				{#if status === 'rejected' && t.correction}<p class="small"><strong>You meant:</strong> {t.correction}</p>{/if}
				<div class="hstack actions">
					{#if t.signals.length}<button class="btn ghost" aria-expanded={!!open[t.id]} use:focusable onclick={() => (open[t.id] = !open[t.id])}>{open[t.id] ? 'Hide details' : 'Details'}</button>{/if}
					{#if status === 'pending' && rejecting !== t.id}<button class="btn ghost" disabled={busy} use:focusable onclick={() => (rejecting = t.id)}>Forget this</button>{/if}
					{#if status === 'rejected'}<button class="btn ghost" disabled={busy} use:focusable onclick={() => restore(t)}>Restore</button>{/if}
				</div>
				{#if open[t.id]}<WhyList teachings={[t]} showText={false} />{/if}
				{#if rejecting === t.id}<RejectDialog {busy} onforget={(c) => forget(t, c)} oncancel={() => (rejecting = null)} />{/if}
			</li>
		{:else}
			<li class="small muted">{status === 'pending' ? 'Nothing new since last night.' : status === 'merged' ? 'Nothing merged yet.' : 'Nothing rejected.'}</li>
		{/each}
	</ul>
{/if}

<style>
	.teachings { list-style: none; margin: 0; padding: 0; display: grid; gap: 0.75rem; }
	.teaching { display: grid; gap: 0.35rem; padding: 0.75rem 0.9rem; border: 1px solid var(--line); border-left: 3px solid var(--ok); border-radius: var(--r-md); background: var(--surface); }
	.teaching.avoid { border-left-color: var(--bad); }
	.teaching p { margin: 0; overflow-wrap: anywhere; }
	.head { display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center; }
	.dir { font-size: 0.75rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: var(--ok); }
	.avoid .dir { color: var(--bad); }
	.badge { font-size: 0.72rem; padding: 0.05rem 0.5rem; border-radius: var(--r-pill); color: var(--accent); border: 1px solid var(--accent); }
	.actions { gap: 0.25rem; }
	.actions .btn { min-height: 2.1rem; padding: 0.3rem 0.7rem; font-size: 0.85rem; }
	.err { color: var(--bad); }
</style>
