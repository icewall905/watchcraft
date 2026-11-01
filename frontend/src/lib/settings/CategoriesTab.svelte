<script lang="ts">
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { api } from '$lib/api';
	import { session } from '$lib/stores/session.svelte';
	import { focusable } from '$lib/nav';
	import { timeAgo } from '$lib/util';
	import type { Category, CategoryInput } from '$lib/types';
	import Section from './Section.svelte';
	import CategoryForm from './CategoryForm.svelte';

	const playlists = $derived(session.categories.filter((c) => c.kind !== 'shorts'));
	const editParam = $derived(page.url.searchParams.get('edit'));
	const isNew = $derived(page.url.searchParams.get('new') === '1');
	let preset = $state<Partial<CategoryInput> | null>(null);
	const editing = $derived(editParam ? (playlists.find((c) => c.id === Number(editParam)) ?? null) : null);

	function open(q: string) {
		goto(`/settings?tab=categories${q}`, { noScroll: false });
	}
	function done() {
		preset = null;
		goto('/settings?tab=categories', { replaceState: true });
	}

	async function move(c: Category, d: -1 | 1) {
		const ids = playlists.map((x) => x.id);
		const i = ids.indexOf(c.id);
		const j = i + d;
		if (j < 0 || j >= ids.length) return;
		[ids[i], ids[j]] = [ids[j], ids[i]];
		const allIds = session.categories.map((x) => x.kind === 'shorts' ? x.id : ids.shift()!);
		await api.reorderCategories(allIds);
		await session.loadCategories();
	}
	async function toggle(c: Category) {
		await api.updateCategory(c.id, { enabled: !c.enabled });
		await session.loadCategories();
	}

	const PRESETS: { label: string; p: Partial<CategoryInput> }[] = [
		{
			label: '🔬 Science for kids',
			p: { name: 'Science for kids', emoji: '🔬', color: '#4cc3ff', kid_safe: true, brief: 'Fun, simple science for kids aged 5–9: experiments, animals, space, how things work. Upbeat presenters like Mark Rober, SciShow Kids, Nat Geo Kids. Nothing scary, mean or gross-out; no pranks or clickbait.', seed_queries: ['science experiments for kids', 'how things work for kids'], seed_channels: ['@MarkRober', '@scishowkids'], max_duration_s: 1200 }
		},
		{
			label: '🪚 Woodworking',
			p: { name: 'Woodworking', emoji: '🪚', color: '#ff7a45', brief: 'Calm, skilled woodworking and furniture builds with real technique explained. Hand tools welcome. No sponsored tool reviews, no rage-bait, no "10 hacks" lists.', seed_queries: ['woodworking build', 'hand tool joinery'] }
		},
		{
			label: '🍳 Weeknight cooking',
			p: { name: 'Weeknight cooking', emoji: '🍳', color: '#ffb84d', brief: 'Practical home cooking under 45 minutes, clear steps, real kitchens. Vegetarian-friendly is a plus. No mukbang, no extreme food challenges.', seed_queries: ['easy weeknight dinner recipe'] }
		},
		{
			label: '🌍 Documentaries',
			p: { name: 'Documentaries', emoji: '🌍', color: '#3ddc97', brief: 'Thoughtful mini-documentaries about history, geography, engineering and nature. Calm narration, well researched, 15–60 minutes. No conspiracy content.', min_duration_s: 900, max_duration_s: 3600 }
		}
	];
</script>

{#if isNew || editing}
	<Section title={editing ? `Edit ${editing.emoji} ${editing.name}` : 'New playlist'} intro="A playlist is a standing request to the AI curator. It searches slowly in the background, reads each video's transcript and top comments, and keeps only what fits your brief.">
		{#if isNew && !preset}
			<div class="presets">
				<span class="small faint">Start from an example</span>
				<div class="hstack">
					{#each PRESETS as pr}
						<button class="chip" use:focusable={{ group: 'presets', onSelect: () => (preset = pr.p) }}>{pr.label}</button>
					{/each}
				</div>
			</div>
		{/if}
		{#key preset}
			<CategoryForm category={editing} {preset} onDone={done} />
		{/key}
	</Section>
{:else}
	<Section title="Playlists" intro="Each playlist becomes a row on Home. Order here is the order on Home.">
		{#snippet actions()}
			<button class="btn primary" use:focusable={{ group: 'cat-top', id: 'cat-new', onSelect: () => open('&new=1') }}>＋ New playlist</button>
		{/snippet}
		{#if playlists.length === 0}
			<div class="empty">
				<span class="big-emoji">🗂️</span>
				<p>No playlists yet. Create one to give the AI something to look for.</p>
			</div>
		{/if}
		<div class="list">
			{#each playlists as c, i (c.id)}
				<div class="item" class:off={!c.enabled} style:--cat={c.color}>
					<span class="e">{c.emoji}</span>
					<div class="info">
						<strong><a class="catname" href="/category/{c.id}" use:focusable={{ group: `cat-${c.id}`, id: `cat-view-${c.id}` }}>{c.name}</a></strong>
						<span class="small muted">{c.stats?.accepted ?? 0} ready, {(c.stats?.found ?? 0) + (c.stats?.enriched ?? 0)} in review, {c.stats?.rejected ?? 0} rejected{c.kid_safe ? ', kid-safe' : ''}{c.created_at ? `, created ${timeAgo(c.created_at)}` : ''}</span>
					</div>
					<button class="btn mini" aria-label="Move up" disabled={i === 0} use:focusable={{ group: `cat-${c.id}`, disabled: i === 0, onSelect: () => move(c, -1) }}>▲</button>
					<button class="btn mini" aria-label="Move down" disabled={i === playlists.length - 1} use:focusable={{ group: `cat-${c.id}`, disabled: i === playlists.length - 1, onSelect: () => move(c, 1) }}>▼</button>
					<button class="btn mini" use:focusable={{ group: `cat-${c.id}`, onSelect: () => toggle(c) }}>{c.enabled ? 'On' : 'Off'}</button>
					<button class="btn mini primary" use:focusable={{ group: `cat-${c.id}`, id: `cat-edit-${c.id}`, onSelect: () => open(`&edit=${c.id}`) }}>Edit</button>
				</div>
			{/each}
		</div>
	</Section>
{/if}

<style>
	.presets {
		display: grid;
		gap: 0.5rem;
		padding: 1rem;
		border-radius: var(--r-md);
		background: rgba(157, 134, 255, 0.08);
		border: 1px dashed rgba(157, 134, 255, 0.35);
	}
	.list {
		display: grid;
		gap: 0.5rem;
	}
	.item {
		display: flex;
		align-items: center;
		gap: 0.8rem;
		padding: 0.7rem 0.8rem;
		border-radius: var(--r-md);
		background: rgba(255, 255, 255, 0.035);
		border: 1px solid var(--line);
		border-left: 4px solid var(--cat);
	}
	.item.off {
		opacity: 0.6;
	}
	.e {
		font-size: 1.6rem;
		width: 2.2rem;
		text-align: center;
	}
	.info {
		flex: 1;
		display: grid;
		min-width: 0;
	}
	.info .catname:hover {
		text-decoration: underline;
		text-decoration-color: var(--cat, var(--accent));
		text-underline-offset: 0.3rem;
	}
	.btn.mini {
		min-height: 2.2rem;
		padding: 0.2rem 0.85rem;
		font-size: 0.85rem;
	}
</style>
