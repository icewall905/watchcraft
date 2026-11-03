<script lang="ts">
	// One muted sentence (alerts, status), with an optional link at the end.
	import { focusable } from '$lib/nav';
	let { text, href, linkLabel, tone = 'muted' }: {
		text: string; href?: string; linkLabel?: string; tone?: 'muted' | 'warn';
	} = $props();
</script>

<p class="quiet-line" class:warn={tone === 'warn'}>
	<span>{text}</span>
	{#if href}<a href={href} use:focusable>{linkLabel ?? 'View'}</a>{/if}
</p>

<style>
	.quiet-line { display: flex; flex-wrap: wrap; align-items: baseline; gap: var(--sp-2); font-size: var(--fs-sm); color: var(--ink-soft); line-height: var(--lh-snug); }
	.quiet-line.warn span { color: var(--warn); }
	a { color: var(--ink); text-decoration: underline; text-decoration-color: var(--rule-strong); text-underline-offset: .2em; border-radius: var(--r-sm); transition: text-decoration-color var(--dur-calm) var(--ease-calm); }
	a:hover { text-decoration-color: var(--accent); }
</style>
