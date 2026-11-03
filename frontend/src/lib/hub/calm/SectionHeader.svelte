<script lang="ts">
	// Editorial section heading: small eyebrow, serif title, optional quiet "see all" link.
	import { focusable } from '$lib/nav';
	let { title, eyebrow, action, level = 2, id }: {
		title: string; eyebrow?: string; action?: { href: string; label: string }; level?: 2 | 3; id?: string;
	} = $props();
</script>

<header class="section-header">
	<div class="titles">
		{#if eyebrow}<p class="eyebrow">{eyebrow}</p>{/if}
		<svelte:element this={`h${level}`} class="title" {id}>{title}</svelte:element>
	</div>
	{#if action}<a class="action" href={action.href} use:focusable>{action.label} <span aria-hidden="true">→</span></a>{/if}
</header>

<style>
	.section-header { display: flex; align-items: flex-end; justify-content: space-between; gap: var(--sp-4); padding-bottom: var(--sp-3); margin-bottom: var(--sp-4); border-bottom: 1px solid var(--rule); }
	.titles { display: grid; gap: var(--sp-1); min-width: 0; }
	.eyebrow { font: 650 var(--fs-xs) / 1 var(--font-body); letter-spacing: .12em; text-transform: uppercase; color: var(--ink-soft); }
	.title { font-family: var(--font-display); font-weight: 560; font-size: var(--fs-xl); line-height: var(--lh-tight); letter-spacing: -0.01em; color: var(--ink); }
	h3.title { font-size: var(--fs-lg); }
	.action { flex: none; font-size: var(--fs-sm); font-weight: 600; color: var(--ink-soft); padding: var(--sp-1) var(--sp-2); border-radius: var(--r-sm); transition: color var(--dur-calm) var(--ease-calm); }
	.action:hover { color: var(--accent); }
</style>
