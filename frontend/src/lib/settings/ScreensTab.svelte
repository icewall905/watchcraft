<script lang="ts">
	import { onMount } from 'svelte';
	import { session } from '$lib/stores/session.svelte';
	import { playback } from '$lib/stores/playback.svelte';
	import { focusable } from '$lib/nav';
	import Section from './Section.svelte';

	let busy = $state('');
	let error = $state('');
	let confirmRemove = $state<string | null>(null);
	const name = (id: number) => session.profiles.find((p) => p.id === id)?.name ?? `Profile ${id}`;
	async function run(key: string, action: () => Promise<void>) {
		if (busy) return;
		busy = key;
		error = '';
		confirmRemove = null;
		try { await action(); } catch (e) { error = (e as Error).message; }
		finally { busy = ''; }
	}
	function remove(id: string) {
		if (confirmRemove !== id) return (confirmRemove = id);
		void run(`rm-${id}`, () => playback.remove(id));
	}
	onMount(() => { void playback.refresh().catch(() => {}); });
</script>

<Section
	title="Screens"
	intro="Screens are browsers registered to receive Play on videos. Removing one here unregisters it from this side; the screen must register again to come back. A new pairing code replaces the old one, so anyone who saw it can no longer pair. Revoking a profile stops that profile from sending to the screen."
>
	{#snippet actions()}
		<button class="btn" disabled={!!busy} use:focusable={{ group: 'scr-top', onSelect: () => run('refresh', () => playback.refresh()) }}>Refresh</button>
	{/snippet}
	{#if !playback.targets.length}
		<p class="muted">No screens are registered yet. Open WatchCraft on a TV or media center, choose Play on and register it there.</p>
	{:else}
		<div class="list">
			{#each playback.targets as t (t.id)}
				<div class="item">
					<div class="grow">
						<strong>{t.name}</strong>
						<span class="small muted">
							{t.online ? 'Online' : 'Offline'} · {t.paired_profiles?.length ? `${t.paired_profiles.length} paired` : 'No profiles paired'}
						</span>
						{#if t.id === playback.receiver?.id}<span class="small" style="color:var(--accent)">This device</span>{/if}
					</div>
					<button class="btn mini" disabled={!!busy} use:focusable={{ group: `scr-${t.id}`, onSelect: () => run(`code-${t.id}`, () => playback.rotateCode(t.id)) }}>New pairing code</button>
					<button class="btn mini danger" disabled={!!busy} use:focusable={{ group: `scr-${t.id}`, onSelect: () => remove(t.id) }}>
						{confirmRemove === t.id ? 'Press again to remove' : 'Remove screen'}
					</button>
				</div>
				{#if t.paired_profiles?.length}
					<div class="pairs">
						{#each t.paired_profiles as id (id)}
							<button class="btn mini" disabled={!!busy} aria-label={`Unpair ${name(id)} from ${t.name}`}
								use:focusable={{ group: `scr-pairs-${t.id}`, onSelect: () => run(`un-${t.id}-${id}`, () => playback.revoke(t.id, id)) }}>
								{name(id)} <span aria-hidden="true">×</span>
							</button>
						{/each}
					</div>
				{/if}
			{/each}
		</div>
	{/if}
	{#if error}<p role="status">{error}</p>{/if}
	<p class="small muted">A screen shows its pairing code in Play on. Senders enter it once; after that they can send videos until you revoke them here.</p>
</Section>

<style>
	.list {
		display: grid;
		gap: 0.5rem;
	}
	.item {
		display: flex;
		align-items: center;
		gap: 1rem;
		padding: 0.7rem 0.9rem;
		border-radius: var(--r-md);
		background: rgba(255, 255, 255, 0.035);
		border: 1px solid var(--line);
	}
	.grow {
		flex: 1;
		display: grid;
	}
	.pairs {
		display: flex;
		gap: 0.5rem;
		flex-wrap: wrap;
		padding: 0 0.9rem;
	}
	.btn.mini {
		min-height: 2.2rem;
		padding: 0.2rem 0.85rem;
		font-size: 0.85rem;
	}
</style>
