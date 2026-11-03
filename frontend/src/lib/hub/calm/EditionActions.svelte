<script lang="ts">
	import { hub } from '$lib/hub/api';
	import { focusable } from '$lib/nav';
	import type { EditionEntry } from '$lib/hub/types';
	let { entry, onchanged }: { entry: EditionEntry; onchanged: (action: 'promote' | 'demote') => void } = $props();
	let busy = $state(false);
	let message = $state('');
	async function set(action: 'promote' | 'demote') {
		busy = true; message = '';
		try { await hub.overrule(entry.item_id, action); message = action === 'promote' ? 'Kept' : 'Set aside'; onchanged(action); }
		catch (e) { message = e instanceof Error ? `Could not save: ${e.message}` : 'Could not save'; }
		finally { busy = false; }
	}
</script>

<span class="actions" aria-label="Adjust this recommendation">
	<button class="action" disabled={busy} aria-label={`Keep ${entry.item.title}`} use:focusable onclick={() => set('promote')}>Keep</button>
	<button class="action" disabled={busy} aria-label={`Set aside ${entry.item.title}`} use:focusable onclick={() => set('demote')}>Set aside</button>
	{#if message}<span class="message" role="status">{message}</span>{/if}
</span>

<style>
	.actions { display: inline-flex; flex-wrap: wrap; align-items: center; gap: .15rem; }
	.action { border: 0; border-radius: var(--r-sm); padding: .2rem .4rem; background: transparent; color: var(--ink-soft); font-size: var(--fs-xs); cursor: pointer; }
	.action:hover { background: var(--bg-2); color: var(--ink); }
	.action:disabled { opacity: .55; cursor: wait; }
	.message { color: var(--faint); font-size: var(--fs-xs); }
</style>
