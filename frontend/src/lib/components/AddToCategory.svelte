<script lang="ts">
	import { api } from '$lib/api';
	import { dialogs } from '$lib/stores/dialogs.svelte';
	import { session } from '$lib/stores/session.svelte';
	import { toast } from '$lib/stores/toast.svelte';
	import { focusable } from '$lib/nav';
	import Modal from './Modal.svelte';
	import TextField from './form/TextField.svelte';

	const t = $derived(dialogs.addTo!);
	const close = () => (dialogs.addTo = null);
	const lists = $derived(session.categories.filter(c => c.kind === 'manual'));
	const curated = $derived(session.categories.filter(c => (c.kind ?? 'playlist') === 'playlist'));
	const canEdit = $derived(session.allows('playlists_edit'));
	let newName = $state('');
	let busy = $state(false);

	async function add(cid: number) {
		if (busy) return;
		busy = true;
		try {
			const c = session.categoryById(cid);
			await api.addCandidate(t.videoId, cid);
			toast.success(`Saved to ${c?.emoji ?? ''} ${c?.name ?? 'playlist'}`);
			close();
		} finally {
			busy = false;
		}
	}

	async function createAndAdd() {
		const name = newName.trim();
		if (!name) return toast.warn('Give the list a name.');
		if (busy) return;
		busy = true;
		try {
			const c = await api.createCategory({ kind: 'manual', name, emoji: '📌', brief: '' });
			await session.loadCategories();
			await api.addCandidate(t.videoId, c.id);
			toast.success(`Saved to ${c.emoji} ${c.name}`);
			close();
		} finally {
			busy = false;
		}
	}
</script>

<Modal title="Save to playlist" subtitle={t.title} size="sm" onClose={close}>
	<div class="list">
		{#each lists as c, i (c.id)}
			<button class="opt" style:--cat={c.color} disabled={busy} use:focusable={{ autofocus: i === 0, onSelect: () => add(c.id) }}>
				<span class="e">{c.emoji}</span>
				<span>{c.name}</span>
			</button>
		{/each}
		{#if canEdit}
			<div class="new">
				<div class="grow"><TextField label="New list" placeholder="e.g. Watch later" bind:value={newName} autofocus={!lists.length} onsubmit={createAndAdd} id="save-new-list" /></div>
				<button class="btn primary" disabled={busy} use:focusable={{ onSelect: createAndAdd }}>Create &amp; save</button>
			</div>
		{:else if !lists.length}
			<p class="muted">You have no lists yet.</p>
		{/if}
	</div>
	{#if curated.length}
		<p class="small faint sect">Or add to a curated playlist (the AI learns from it)</p>
		<div class="list">
			{#each curated as c (c.id)}
				<button class="opt" style:--cat={c.color} disabled={busy} use:focusable={{ onSelect: () => add(c.id) }}>
					<span class="e">{c.emoji}</span>
					<span>{c.name}</span>
				</button>
			{/each}
		</div>
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
	.new {
		display: flex;
		align-items: flex-end;
		gap: 0.5rem;
	}
	.grow {
		flex: 1;
	}
	.sect {
		margin: 1rem 0 0.45rem;
	}
	.e {
		font-size: 1.3rem;
	}
</style>
