<script lang="ts">
	import { tick } from 'svelte';
	import { focusable } from '$lib/nav';
	import { ApiError } from '$lib/api';
	import { session } from '$lib/stores/session.svelte';
	import { errMsg } from '$lib/hub/util';
	import { curator, direction, sectionTitle, type ChatMessage, type Snapshot } from './api';
	let { messages = $bindable([]), draft = $bindable(''), onapplied }: {
		messages?: ChatMessage[]; draft?: string; onapplied: (s: Snapshot) => void;
	} = $props();

	let box = $state<HTMLTextAreaElement>(), thread = $state<HTMLElement>();
	let thinking = $state(false), busy = $state<number | null>(null), error = $state('');
	const STATUS = { accepted: 'Applied', rejected: 'Discarded', stale: 'What I know changed since this suggestion — ask again.' } as const;

	export async function focus() { await tick(); box?.focus(); box?.setSelectionRange(draft.length, draft.length); }
	async function scrollDown() { await tick(); thread?.scrollTo({ top: thread.scrollHeight }); }
	$effect(() => { void messages.length; void scrollDown(); });

	function replace(m: ChatMessage) { messages = messages.map((x) => (x.id === m.id ? m : x)); }
	async function sendMessage() {
		const text = draft.trim();
		if (!text || thinking) return;
		const v = session.version;
		thinking = true; error = '';
		try {
			const r = await curator.say(text);
			if (v !== session.version) return;
			messages = [...messages, ...r.messages.filter((m): m is ChatMessage => m !== null)]; draft = '';
		} catch (e) { if (v === session.version) error = errMsg(e); }
		finally { if (v === session.version) thinking = false; }
	}
	async function apply(m: ChatMessage) {
		const v = session.version;
		busy = m.id; error = '';
		try { const r = await curator.accept(m.id); if (v === session.version) { replace(r.message); onapplied(r); } }
		catch (e) {
			if (v !== session.version) return;
			if (e instanceof ApiError && e.status === 409) replace({ ...m, proposal_status: 'stale' });
			else error = errMsg(e);
		} finally { busy = null; }
	}
	async function discard(m: ChatMessage) {
		const v = session.version;
		busy = m.id;
		try { const r = await curator.discard(m.id); if (v === session.version) replace(r); } catch { /* the request already showed the error */ }
		finally { busy = null; }
	}
</script>

<div class="chat">
	<h3>Tell me what I got wrong</h3>
	<ol class="thread" bind:this={thread} aria-live="polite">
		{#each messages as m (m.id)}
			<li class="msg {m.role}">
				<p>{m.text}</p>
				{#if m.proposal_status !== 'none' && m.proposal.ops?.length}
					<div class="proposal" data-nav-row={`proposal-${m.id}`}>
						<ul class="ops">
							{#each m.proposal.ops as op, i (i)}
								{#if op.op === 'add'}<li class="add">+ add <span class="muted">({sectionTitle(op.section)}, {direction(op.polarity)})</span> {op.text}</li>
								{:else if op.op === 'edit'}<li class="edit">~ edit <span class="muted">({sectionTitle(op.section)})</span> <del>{op.before}</del> → {op.text}</li>
								{:else}<li class="remove">− remove <span class="muted">({sectionTitle(op.section)})</span> {op.before ?? op.text}</li>{/if}
							{/each}
						</ul>
						{#if m.proposal_status === 'pending'}
							<div class="hstack">
								<button class="btn primary" disabled={busy !== null} use:focusable onclick={() => apply(m)}>Apply</button>
								<button class="btn ghost" disabled={busy !== null} use:focusable onclick={() => discard(m)}>Discard</button>
							</div>
						{:else}<p class="status small {m.proposal_status}">{STATUS[m.proposal_status]}</p>{/if}
					</div>
				{/if}
			</li>
		{:else}<li class="small muted">Say it plainly — "less true crime", "I only like cats in memes" — and I'll suggest a change to what I know.</li>{/each}
		{#if thinking}<li class="msg curator small muted" role="status">Thinking… this can take a minute or two.</li>{/if}
	</ol>
	{#if error}<p role="alert" class="err small">{error}</p>{/if}
	<form class="stack" onsubmit={(e) => { e.preventDefault(); void sendMessage(); }}>
		<textarea class="input" aria-label="Message to the curator" placeholder="Tell me what I got wrong…" maxlength="2000" rows="3" bind:this={box} bind:value={draft} use:focusable
			onkeydown={(e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); void sendMessage(); } }}></textarea>
		<div><button class="btn primary" disabled={thinking || !draft.trim()} use:focusable>{thinking ? 'Thinking…' : 'Send'}</button></div>
	</form>
</div>

<style>
	.chat { display: grid; gap: 0.75rem; padding: 1rem; border: 1px solid var(--line); border-radius: var(--r-lg); background: var(--surface); }
	h3 { margin: 0; }
	.thread { list-style: none; margin: 0; padding: 0; display: grid; gap: 0.6rem; max-height: min(55vh, 34rem); overflow-y: auto; align-content: start; }
	.msg { max-width: 92%; padding: 0.55rem 0.8rem; border-radius: var(--r-md); background: var(--surface-2); overflow-wrap: anywhere; }
	.msg p { margin: 0; white-space: pre-line; }
	.msg.user { justify-self: end; background: var(--surface-hi); }
	.proposal { display: grid; gap: 0.5rem; margin-top: 0.5rem; padding: 0.6rem; border-radius: var(--r-sm); border: 1px solid var(--line-strong); }
	.ops { list-style: none; margin: 0; padding: 0; display: grid; gap: 0.3rem; font-size: 0.9rem; }
	.add { color: var(--ok); }
	.edit { color: var(--warn); }
	.remove { color: var(--bad); }
	del { opacity: 0.75; }
	.status { margin: 0; font-weight: 600; }
	.status.accepted { color: var(--ok); }
	.status.stale { color: var(--warn); }
	textarea.input { min-height: 5rem; }
	.err { color: var(--bad); }
</style>
