<script lang="ts">
	// Quiet footer link to what the curator held back: "Set aside (N)". Hidden when nothing was set aside.
	import { focusable } from '$lib/nav';
	let { count, href, label = 'Set aside' }: { count: number; href: string; label?: string } = $props();
</script>

{#if count > 0}
	<a class="set-aside" href={href} use:focusable aria-label={`${label}: ${count} ${count === 1 ? 'item' : 'items'}`}>{label} ({count})</a>
{/if}

<style>
	.set-aside { display: inline-block; font-size: var(--fs-sm); color: var(--faint); padding: var(--sp-1) var(--sp-2); border-radius: var(--r-sm); transition: color var(--dur-calm) var(--ease-calm); }
	.set-aside:hover { color: var(--ink-soft); }
</style>
