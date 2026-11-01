<script lang="ts">
	import { focusable } from '$lib/nav';
	// `stack` lifts a dirty bar above the ones below it: several sections of one tab can be dirty
	// at once, and sticky bars all pin to the same offset and overlap otherwise.
	let { dirty, busy = false, onsave, onreset, label = 'Save changes', stack = 0 }: { dirty: boolean; busy?: boolean; onsave: () => void; onreset?: () => void; label?: string; stack?: number } = $props();
</script>

<div class="savebar" class:dirty style:--stack={stack}>
	<span class="small">{dirty ? 'You have unsaved changes.' : 'All changes saved.'}</span>
	<span class="spacer"></span>
	{#if onreset && dirty}<button class="btn ghost" use:focusable={{ group: 'savebar', scroll: 'nearest', onSelect: onreset }}>Undo</button>{/if}
	<button class="btn primary" disabled={!dirty || busy} use:focusable={{ group: 'savebar', scroll: 'nearest', disabled: !dirty || busy, onSelect: onsave }}>{busy ? 'Saving…' : label}</button>
</div>

<style>
	.savebar {
		position: relative;
		z-index: 20;
		display: flex;
		align-items: center;
		gap: 0.75rem;
		margin-top: 0.5rem;
		padding: 0.7rem 0.8rem 0.7rem 1.2rem;
		border-radius: var(--r-pill);
		background: var(--savebar-surface);
		border: 1px solid var(--line);
		backdrop-filter: var(--blur);
		color: var(--faint);
		transition: border-color 0.2s;
	}
	/* Only unsaved sections stick to the bottom, so idle bars never cover controls. */
	.savebar.dirty {
		position: sticky;
		bottom: calc(1rem + var(--stack, 0) * 3.6rem);
		border-color: var(--accent);
		color: var(--accent-2);
	}
	@media (max-width: 760px) {
		.savebar.dirty {
			bottom: calc(5rem + var(--stack, 0) * 3.6rem);
		}
	}
</style>
