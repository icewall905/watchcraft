<script lang="ts">
	import { focusable } from '$lib/nav';
	let { message, onretry }: { message: string; onretry?: () => void } = $props();
</script>

<div class="err" role="alert">
	<span>{message}</span>
	{#if onretry}<button class="btn" use:focusable onclick={onretry}>Retry</button>{/if}
</div>

<style>
	.err { display: flex; align-items: center; justify-content: space-between; gap: 1rem; flex-wrap: wrap; padding: .9rem 1.1rem; border-radius: var(--r-md); border: 1px solid rgba(255, 92, 122, .45); background: rgba(255, 92, 122, .1); color: #ffd1d9; }
</style>
