<script lang="ts">
import { page } from '$app/state';
import { goto } from '$app/navigation';
import { session } from '$lib/stores/session.svelte';
import { focusable } from '$lib/nav';
import Section from './Section.svelte';
import CategoryForm from './CategoryForm.svelte';
const feeds = $derived(session.categories.filter(c => c.kind === 'shorts'));
const editing = $derived(feeds.find(c => c.id === Number(page.url.searchParams.get('edit'))) ?? null);
const isNew = $derived(page.url.searchParams.get('new') === '1');
function done() { goto('/settings?tab=feeds', { replaceState: true }); }
</script>
{#if isNew || editing}
	<Section title={editing ? `Edit ${editing.name}` : 'New feed'} intro="Describe the short videos you want. Your curator finds and downloads them; only completed local files appear in Shorts.">
		{#key editing?.id ?? 'new'}<CategoryForm category={editing} kind="shorts" onDone={done} />{/key}
	</Section>
{:else}
	<Section title="Feeds" intro="Separate collections for Shorts, each with its own brief, sources and ready queue. Choose a feed when opening Shorts.">
		{#snippet actions()}<a class="btn primary" href="/settings?tab=feeds&new=1" use:focusable>＋ New feed</a>{/snippet}
		{#if !feeds.length}<p class="muted">No feeds yet. Create a feed to start collecting local clips.</p>{/if}
		<div class="list">{#each feeds as feed (feed.id)}
			<div class="card"><span>{feed.emoji}</span><div class="grow"><strong>{feed.name}</strong><p class="small muted">{feed.enabled ? 'Collecting clips' : 'Collection paused'} · Keep {feed.target_queue} ready</p></div><a class="btn" href={`/settings?tab=feeds&edit=${feed.id}`} use:focusable>Edit</a></div>
		{/each}</div>
		<a class="btn" href="/shorts" use:focusable>Open Shorts</a>
	</Section>
{/if}
<style>.list { display:grid;gap:.7rem; }.card {display:flex;gap:1rem;align-items:center;padding:1rem;border:1px solid var(--line);border-radius:var(--r-md)}.grow{flex:1}.card p{margin:.2rem 0}</style>
