<script lang="ts">
	import { hub } from './api';
	import { focusable } from '$lib/nav';
	import { session } from '$lib/stores/session.svelte';

	// "Seen it" records that the reader has read the story; "Dismiss" says it is not interesting.
	// Both take it out of the feed, and the difference is the signal the curators learn from.
	let { itemIds, title, onchanged }: {
		itemIds: number[]; title: string; onchanged?: (action: 'seen' | 'dismissed') => void;
	} = $props();
	let busy = $state(false);
	let message = $state('');
	const allowed = () => session.ready && !!session.profile && !session.profile.pin_locked && !session.needsLogin && !session.choosing;

	async function set(action: 'seen' | 'dismissed') {
		if (busy || !allowed() || !itemIds.length) return;
		const profileId = session.profileId, version = session.version;
		busy = true; message = '';
		try {
			await hub.setStateMany(itemIds, action === 'seen' ? { read: true } : { dismissed: true });
			if (profileId !== session.profileId || version !== session.version) return;
			onchanged?.(action);
		} catch (e) {
			if (profileId === session.profileId && version === session.version)
				message = e instanceof Error ? `Could not save: ${e.message}` : 'Could not save';
		} finally {
			if (profileId === session.profileId && version === session.version) busy = false;
		}
	}
</script>

<span class="actions" aria-label={`Mark ${title}`}>
	<button class="action" disabled={busy || !allowed()} aria-label={`Seen it: ${title}`} use:focusable onclick={() => set('seen')}>Seen it</button>
	<button class="action" disabled={busy || !allowed()} aria-label={`Dismiss ${title}`} use:focusable onclick={() => set('dismissed')}>Dismiss</button>
	{#if message}<span class="message" role="status">{message}</span>{/if}
</span>

<style>
	.actions { display: inline-flex; flex-wrap: wrap; align-items: center; gap: .15rem; }
	.action { border: 0; border-radius: var(--r-sm); padding: .2rem .4rem; background: transparent; color: var(--ink-soft); font-size: var(--fs-xs); cursor: pointer; }
	.action:hover { background: var(--bg-2); color: var(--ink); }
	.action:disabled { opacity: .55; cursor: wait; }
	.message { color: var(--faint); font-size: var(--fs-xs); }
</style>
