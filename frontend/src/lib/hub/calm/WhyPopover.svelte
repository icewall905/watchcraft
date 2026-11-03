<script lang="ts">
	// "Why am I seeing this?" — a quiet button that opens the curator's reason tags.
	// The open popover is its own nav scope: Back/Escape closes and focus returns to the button.
	import { focusable, navScope } from '$lib/nav';
	let { reasons, label = 'Why?', title = 'Why this is here' }: { reasons: string[]; label?: string; title?: string } = $props();
	let open = $state(false);
	let root = $state<HTMLElement>();
	const uid = $props.id();
	const close = () => (open = false);
	function onkey(e: KeyboardEvent) { if (open && e.key === 'Escape') { e.stopPropagation(); close(); } }
	function onpointer(e: PointerEvent) { if (open && root && !root.contains(e.target as Node)) close(); }
</script>

<svelte:window onpointerdown={onpointer} />

{#if reasons.length}
	<span class="why" bind:this={root}>
		<button class="trigger" type="button" aria-expanded={open} aria-controls={`why-${uid}`} use:focusable={{ onSelect: () => (open = !open) }}>{label}</button>
		{#if open}
			<div class="pop" id={`why-${uid}`} role="dialog" aria-label={title} tabindex="-1" onkeydown={onkey} use:navScope={{ onBack: close }}>
				<p class="head">{title}</p>
				<ul>{#each reasons as r}<li>{r}</li>{/each}</ul>
				<button class="close" type="button" use:focusable={{ onSelect: close }}>Close</button>
			</div>
		{/if}
	</span>
{/if}

<style>
	.why { position: relative; display: inline-block; }
	.trigger { background: none; border: 1px solid transparent; border-radius: var(--r-pill); padding: .15rem .55rem; font-size: var(--fs-xs); font-weight: 600; color: var(--ink-soft); cursor: pointer; transition: color var(--dur-calm) var(--ease-calm), border-color var(--dur-calm) var(--ease-calm); }
	.trigger:hover, .trigger[aria-expanded='true'] { color: var(--ink); border-color: var(--rule-strong); }
	.pop { position: absolute; z-index: 40; top: calc(100% + var(--sp-2)); left: 0; width: max-content; max-width: min(22rem, 80vw); padding: var(--sp-3) var(--sp-4); display: grid; gap: var(--sp-2); background: var(--overlay-surface); color: var(--ink); border: 1px solid var(--rule-strong); border-radius: var(--r-md); box-shadow: var(--shadow-calm); animation: fade var(--dur-calm) var(--ease-calm); }
	.head { font: 650 var(--fs-xs) / 1 var(--font-body); letter-spacing: .1em; text-transform: uppercase; color: var(--ink-soft); }
	ul { margin: 0; padding: 0; list-style: none; display: flex; flex-wrap: wrap; gap: var(--sp-1) var(--sp-2); }
	li { font-size: var(--fs-sm); padding: .1rem .55rem; border-radius: var(--r-pill); background: color-mix(in srgb, var(--ai) 12%, transparent); color: var(--ink); }
	.close { justify-self: end; background: none; border: 0; padding: var(--sp-1) var(--sp-2); border-radius: var(--r-sm); font-size: var(--fs-xs); font-weight: 600; color: var(--ink-soft); cursor: pointer; }
	.close:hover { color: var(--ink); }
	@keyframes fade { from { opacity: 0; transform: translateY(-2px); } }
</style>
