<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import { toast } from '$lib/stores/toast.svelte';
	import { focusable } from '$lib/nav';
	import type { ParentalFeature } from '$lib/types';
	import Modal from './Modal.svelte';
	import Toggle from './form/Toggle.svelte';
	import TextField from './form/TextField.svelte';

	type Child = { id: number; name: string; controls?: Record<string, boolean>; safety_rails?: string };
	/** `admin`: an account administrator editing any account's child (/admin routes); otherwise this account's own child. */
	let { child, admin = false, onClose, onSaved }: { child: Child; admin?: boolean; onClose: () => void; onSaved?: () => void } = $props();

	let features = $state<ParentalFeature[]>([]);
	// svelte-ignore state_referenced_locally
	let on = $state<Record<string, boolean>>({ ...(child.controls ?? {}) });
	// svelte-ignore state_referenced_locally
	let rails = $state(child.safety_rails ?? '');
	let busy = $state(false);
	const groups = $derived([...new Set(features.map((f) => f.group))].map((g) => ({ name: g, items: features.filter((f) => f.group === g) })));
	const count = $derived(features.filter((f) => on[f.key]).length);

	onMount(async () => {
		features = await api.parentalFeatures();
		for (const f of features) if (on[f.key] === undefined) on[f.key] = f.default;
	});

	function all(value: boolean) {
		for (const f of features) on[f.key] = f.key === 'screen' ? true : value;
	}

	async function save() {
		busy = true;
		try {
			const controls = Object.fromEntries(features.map((f) => [f.key, !!on[f.key]]));
			if (admin) await api.setChildParental(child.id, controls);
			else await api.setParental(child.id, controls);
			if (rails.trim() !== (child.safety_rails ?? '')) {
				if (admin) await api.setChildRails(child.id, rails);
				else await api.setSafetyRails(child.id, rails);
			}
			toast.success(`Saved parental controls for ${child.name}`);
			onSaved?.();
			onClose();
		} catch {
			// The API client shows the error; keep the panel open.
		} finally {
			busy = false;
		}
	}
</script>

<Modal title={`Parental controls: ${child.name}`} subtitle="Choose what this child can use. Anything switched off is hidden and blocked by the server." size="md" {onClose}>
	<div class="stack">
		<div class="hstack quick">
			<span class="small muted">{count} of {features.length} on</span>
			<span class="spacer"></span>
			<button class="btn mini" use:focusable={{ group: 'parental-quick', onSelect: () => all(false) }}>Only The Screen</button>
			<button class="btn mini" use:focusable={{ group: 'parental-quick', onSelect: () => all(true) }}>Everything on</button>
		</div>
		{#if !features.length}<div class="skeleton" style="height:12rem"></div>{/if}
		{#each groups as g (g.name)}
			<section>
				<h3>{g.name}</h3>
				{#each g.items as f (f.key)}
					<Toggle bind:checked={on[f.key]} label={f.label} hint={f.hint} id={`parental-${f.key}`} group="parental" />
				{/each}
			</section>
		{/each}
		<section>
			<h3>Safety rails for the AI</h3>
			<TextField label="Safety rails (optional)" multiline rows={4} bind:value={rails} id="parental-rails"
				placeholder="e.g. She is 8. No battles, monsters or scary stories. Animals, crafts and space are great."
				hint="Your own instructions, added to every kid-safety check the AI makes for this child: Hub items, their curators, video playlists. Stricter or looser than the default (about 10–12 years old). After a change, this child's Hub items stay hidden until they are checked again. Shared checks (Common Room, Mood Boards) must suit every child's rails." />
		</section>
	</div>
	{#snippet footer()}
		<span class="spacer"></span>
		<button class="btn" use:focusable={{ onSelect: onClose, scroll: 'nearest' }}>Cancel</button>
		<button class="btn primary" disabled={busy || !features.length} use:focusable={{ id: 'parental-save', onSelect: save, scroll: 'nearest' }}>Save</button>
	{/snippet}
</Modal>

<style>
	h3 { font-size: 0.95rem; margin: 0.6rem 0 0.2rem; }
	section { display: flex; flex-direction: column; gap: 0.35rem; }
	.quick { align-items: center; gap: 0.5rem; }
</style>
