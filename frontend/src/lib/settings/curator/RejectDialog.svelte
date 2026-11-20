<script lang="ts">
	import { focusable } from '$lib/nav';
	let { busy = false, onforget, oncancel }: {
		busy?: boolean; onforget: (correction: string) => void; oncancel: () => void;
	} = $props();
	let correction = $state('');
	const meant = $derived(correction.trim());
</script>

<form class="reject stack" onsubmit={(e) => { e.preventDefault(); if (meant) onforget(meant); }}>
	<label class="stack">
		<span class="small">What did you actually mean? (optional)</span>
		<textarea class="input" rows="2" maxlength="600" bind:value={correction} use:focusable></textarea>
	</label>
	<div class="hstack" data-nav-row="reject-actions">
		<button type="button" class="btn danger" disabled={busy} use:focusable onclick={() => onforget('')}>Forget</button>
		<button class="btn" disabled={busy || !meant} use:focusable>Forget &amp; correct</button>
		<button type="button" class="btn ghost" disabled={busy} use:focusable onclick={oncancel}>Cancel</button>
	</div>
</form>

<style>
	.reject { gap: 0.6rem; padding: 0.75rem; border-radius: var(--r-md); background: var(--surface-2); }
	label { gap: 0.35rem; }
	textarea.input { min-height: 4.5rem; }
</style>
