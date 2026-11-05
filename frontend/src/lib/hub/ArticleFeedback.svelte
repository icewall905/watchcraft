<script lang="ts">
	import { hub } from './api';
	import type { ItemState } from './types';
	import { session } from '$lib/stores/session.svelte';
	import { focusable } from '$lib/nav';

	let { itemId, title, rating = 0, onchanged }: {
		itemId: number; title: string; rating?: number; onchanged?: (state: ItemState) => void;
	} = $props();
	let busy = $state(false);
	let failed = $state('');
	let open = $state(false);
	let chips = $state<string[]>([]);
	let text = $state('');
	let localRating = $state(0);
	let observedIdentity = '';
	const choices = ['Useful', 'Not relevant', 'Too repetitive', 'Too shallow', 'Sensational', 'Well sourced', 'More like this'];
	const identity = () => `${itemId}:${session.profileId}:${session.version}:${session.profile?.pin_locked}:${session.needsLogin}:${session.choosing}`;
	$effect(() => {
		const next = identity();
		if (observedIdentity && observedIdentity !== next) { busy = false; failed = ''; chips = []; text = ''; open = false; localRating = rating; }
		observedIdentity = next;
	});
	$effect(() => { if (!busy) localRating = rating; });
	const allowed = () => session.ready && !!session.profile && !session.profile.pin_locked && !session.needsLogin && !session.choosing;
	async function save(next: -1 | 0 | 1 | 2, details = false) {
		if (busy || !allowed()) return;
		const profileId = session.profileId, version = session.version, target = identity();
		busy = true; failed = '';
		try {
			const result = await hub.articleFeedback(itemId, { rating: next, chips: details ? chips : [], text: details ? text.trim() : '' });
			if (profileId !== session.profileId || version !== session.version || target !== identity() || !allowed()) return;
			localRating = result.state.rating;
			onchanged?.(result.state);
			if (details) { open = false; chips = []; text = ''; }
		} catch (e) {
			if (profileId === session.profileId && version === session.version && target === identity() && allowed()) failed = e instanceof Error ? e.message : 'Could not save feedback. Try again.';
		} finally { if (profileId === session.profileId && version === session.version && target === identity()) busy = false; }
	}
	function toggleChip(c: string) { chips = chips.includes(c) ? chips.filter((x) => x !== c) : [...chips, c]; }
</script>

<div class="article-feedback" aria-label={`Feedback for ${title}`}>
	<button class="vote" class:active={localRating > 0} aria-label="Like article" aria-pressed={localRating > 0} disabled={busy || !allowed()} use:focusable onclick={() => save(localRating > 0 ? 0 : 1)}>{busy ? '…' : '👍'}<span>Like</span></button>
	<button class="vote" class:active={localRating < 0} aria-label="Dislike article" aria-pressed={localRating < 0} disabled={busy || !allowed()} use:focusable onclick={() => save(localRating < 0 ? 0 : -1)}>👎<span>Not for me</span></button>
	<button class="why" aria-expanded={open} disabled={busy || !allowed()} use:focusable onclick={() => (open = !open)}>{open ? 'Close' : 'Why?'}</button>
	{#if open}
		<div class="details">
			<p>Help the curator learn what worked for you.</p>
			<div class="chips">
				{#each choices as choice}
					<button class="chip" class:selected={chips.includes(choice)} aria-pressed={chips.includes(choice)} disabled={busy} use:focusable onclick={() => toggleChip(choice)}>{choice}</button>
				{/each}
			</div>
			<label>Optional note<textarea bind:value={text} maxlength="1000" rows="2" disabled={busy} placeholder="What should the curator know?"></textarea></label>
			<button class="save" disabled={busy || (!chips.length && !text.trim())} use:focusable onclick={() => save(localRating as -1 | 0 | 1 | 2, true)}>{busy ? 'Saving…' : 'Send feedback'}</button>
		</div>
	{/if}
	{#if failed}<span class="error" role="alert">{failed}</span>{/if}
</div>

<style>
	.article-feedback { position: relative; z-index: 2; display: inline-flex; align-items: center; gap: .35rem; flex-wrap: wrap; font: var(--fs-xs)/1.3 var(--font-body); }
	.vote,.why,.save { min-height: 2.25rem; border: 1px solid var(--line); border-radius: var(--r-pill); padding: .3rem .65rem; background: var(--surface); color: var(--ink-soft); cursor: pointer; }
	.vote { display: inline-flex; gap: .35rem; align-items: center; }
	.vote.active,.chip.selected { border-color: var(--accent); color: var(--accent); background: color-mix(in srgb, var(--accent) 12%, transparent); }
	button:disabled { opacity: .55; cursor: wait; }
	.details { flex-basis: 100%; display: grid; gap: .55rem; padding: .75rem; border: 1px solid var(--line); border-radius: var(--r-md); background: var(--bg-2); color: var(--ink-soft); }
	.details p { margin: 0; }
	.chips { display: flex; flex-wrap: wrap; gap: .35rem; }
	.chip { min-height: 2rem; border: 1px solid var(--line); border-radius: var(--r-pill); padding: .25rem .55rem; background: transparent; color: var(--ink-soft); cursor: pointer; }
	label { display: grid; gap: .3rem; }
	textarea { resize: vertical; width: 100%; border: 1px solid var(--line); border-radius: var(--r-sm); background: var(--surface); color: var(--ink); padding: .5rem; font: inherit; }
	.save { justify-self: start; }
	.error { color: var(--bad); }
</style>
