<script lang="ts">
	import { api } from '$lib/api';
	import { dialogs } from '$lib/stores/dialogs.svelte';
	import { session } from '$lib/stores/session.svelte';
	import { toast } from '$lib/stores/toast.svelte';
	import { focusable } from '$lib/nav';
	import Modal from './Modal.svelte';

	const t = $derived(dialogs.addTo!);
	const close = () => (dialogs.addTo = null);

	async function add(cid: number) {
		const c = session.categoryById(cid);
		await api.addCandidate(t.videoId, cid);
		toast.success(`Added to ${c?.emoji ?? ''} ${c?.name ?? 'playlist'}`);
		close();
	}
</script>

<Modal title="Add to playlist" subtitle={t.title} size="sm" onClose={close}>
	{#if session.categories.filter(c => c.kind !== 'shorts').length}
		<div class="list">
			{#each session.categories.filter(c => c.kind !== 'shorts') as c, i (c.id)}
				<button class="opt" style:--cat={c.color} use:focusable={{ autofocus: i === 0, onSelect: () => add(c.id) }}>
					<span class="e">{c.emoji}</span>
					<span>{c.name}</span>
				</button>
			{/each}
		</div>
	{:else}
		<p class="muted">You have no playlists yet. Create one under Settings, Playlists.</p>
	{/if}
</Modal>

<style>
	.list {
		display: grid;
		gap: 0.45rem;
	}
	.opt {
		--focus-scale: 1.03;
		display: flex;
		align-items: center;
		gap: 0.8rem;
		padding: 0.75rem 1rem;
		border-radius: var(--r-md);
		border: 1px solid var(--line);
		border-left: 4px solid var(--cat);
		background: rgba(255, 255, 255, 0.04);
		cursor: pointer;
		font-weight: 600;
		text-align: left;
	}
	.e {
		font-size: 1.3rem;
	}
</style>
