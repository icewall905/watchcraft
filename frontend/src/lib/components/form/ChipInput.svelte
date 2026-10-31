<script lang="ts">
	import { focusable } from '$lib/nav';

	interface Props {
		values: string[];
		label: string;
		hint?: string;
		placeholder?: string;
		group?: string;
		splitCommas?: boolean;
	}
	let { values = $bindable([]), label, hint, placeholder = 'Add…', group, splitCommas = true }: Props = $props();
	let draft = $state('');
	const g = $derived(group ?? `chips-${label}`);

	function add() {
		const parts = draft
			.split(splitCommas ? /[,\n]/ : /\n/)
			.map((s) => s.trim())
			.filter((s) => s && !values.includes(s));
		if (parts.length) values = [...values, ...parts];
		draft = '';
	}
	function remove(v: string) {
		values = values.filter((x) => x !== v);
	}
</script>

<div class="field">
	<span class="label">{label}</span>
	<div class="box">
		{#each values as v (v)}
			<button class="chip" aria-label="Remove {v}" use:focusable={{ group: g, onSelect: () => remove(v) }}>{v} <span class="x">✕</span></button>
		{/each}
		<input
			class="draft"
			{placeholder}
			aria-label={label}
			bind:value={draft}
			onkeydown={(e) => {
				if (e.key === 'Enter' || (splitCommas && e.key === ',')) {
					e.preventDefault();
					add();
				}
			}}
			onosksubmit={add}
			onblur={() => draft.trim() && add()}
			use:focusable={{ group: g, label, onSelect: add }}
		/>
	</div>
	{#if hint}<span class="hint">{hint}</span>{/if}
</div>

<style>
	.box {
		display: flex;
		flex-wrap: wrap;
		gap: 0.45rem;
		padding: 0.5rem;
		border-radius: var(--r-md);
		border: 1px solid var(--line-strong);
		background: rgba(10, 8, 25, 0.55);
		min-height: 2.8rem;
		align-items: center;
	}
	.x {
		opacity: 0.6;
		font-size: 0.75em;
	}
	.draft {
		--focus-scale: 1.02;
		flex: 1;
		min-width: 8rem;
		border: 0;
		background: transparent;
		padding: 0.35rem 0.5rem;
		border-radius: var(--r-sm);
		font-size: 1rem;
	}
</style>
