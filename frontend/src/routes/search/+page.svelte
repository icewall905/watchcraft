<script lang="ts">
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { onMount, untrack } from 'svelte';
	import { api, ApiError } from '$lib/api';
	import { dialogs } from '$lib/stores/dialogs.svelte';
	import { session } from '$lib/stores/session.svelte';
	import { focusable } from '$lib/nav';
	import type { Video } from '$lib/types';
	import Keyboard from '$lib/components/Keyboard.svelte';
	import VideoCard from '$lib/components/VideoCard.svelte';
	import OfflineButton from '$lib/components/OfflineButton.svelte';
	import VoiceSearch from '$lib/components/VoiceSearch.svelte';

	const RECENT_KEY = 'bt.recentSearches';
	let q = $state(page.url.searchParams.get('q') ?? '');
	let results = $state<Video[] | null>(null);
	let busy = $state(false);
	let error = $state('');
	let recent = $state<string[]>([]);
	let ctrl: AbortController | null = null;

	onMount(() => {
		try {
			recent = JSON.parse(localStorage.getItem(RECENT_KEY) ?? '[]');
		} catch {
			recent = [];
		}
		if (q) run(false);
	});

	$effect(() => {
		const term = page.url.searchParams.get('q') ?? '';
		untrack(() => { if (term !== q) { q = term; if (term) run(false); else results = null; } });
	});

	async function run(push = true) {
		const query = q.trim();
		if (!query) return;
		ctrl?.abort();
		ctrl = new AbortController();
		busy = true;
		error = '';
		if (push) goto(`/search?q=${encodeURIComponent(query)}`, { replaceState: true, keepFocus: true, noScroll: true });
		recent = [query, ...recent.filter((r) => r !== query)].slice(0, 8);
		try {
			localStorage.setItem(RECENT_KEY, JSON.stringify(recent));
		} catch {
			/* ignore */
		}
		try {
			let r = await api.search(query, 30, ctrl.signal);
			if (session.settings?.extras.hide_shorts) r = r.filter((v) => !v.is_short);
			results = r;
		} catch (e) {
			if ((e as Error).name === 'AbortError') return;
			error = e instanceof ApiError && e.status === 503 ? 'YouTube is blocking searches from the server right now. Try again in a while.' : 'Search failed. Try again.';
			results = [];
		} finally {
			busy = false;
		}
	}
</script>


<div class="page search">
	<aside class="left">
		<form class="bar" onsubmit={(e) => (e.preventDefault(), run())}>
			<span class="mag" aria-hidden="true">⌕</span>
			<input
				class="input q"
				type="search"
				placeholder="Search YouTube"
				aria-label="Search YouTube"
				bind:value={q}
				use:focusable={{ group: 'search-input', id: 'search-input', label: 'Search YouTube', onSelect: () => run() }}
				onosksubmit={() => run()}
			/>
			<VoiceSearch group="search-input" onresult={(text) => { q = text; void run(); }} />
		</form>
		<div class="osk">
			<Keyboard name="search-kb" value={q} onchange={(v) => (q = v)} onsubmit={() => run()} submitLabel="Search" autofocus={!q} />
		</div>
		{#if recent.length}
			<div class="recent">
				<span class="small faint">Recent</span>
				<div class="hstack">
					{#each recent as r}
						<button class="chip" use:focusable={{ group: 'recent', onSelect: () => ((q = r), run()) }}>{r}</button>
					{/each}
				</div>
			</div>
		{/if}
		<p class="small faint">Searches go straight to YouTube through your server. The AI is not involved, and nothing here is filtered for kids.</p>
	</aside>

	<section class="results">
		{#if busy}
			<div class="grid-cards">{#each Array(9) as _}<div class="skeleton" style="aspect-ratio:16/11"></div>{/each}</div>
		{:else if error}
			<div class="empty"><span class="big-emoji">🚧</span><p>{error}</p></div>
		{:else if results === null}
			<div class="empty hello">
				<span class="big-emoji">🔎</span>
				<h2>Find anything</h2>
				<p>Type with the keyboard on the left, then press Search. Add good finds to a playlist so the AI learns what you like.</p>
			</div>
		{:else if results.length === 0}
			<div class="empty"><span class="big-emoji">🫙</span><p>No results for "{q}".</p></div>
		{:else}
			<div class="grid-cards" data-nav-center>
				{#each results as v, i (v.id)}
					<VideoCard video={v} size="grid" group="results" autofocus={i === 0}>
						{#snippet actions()}
							<button class="btn add" use:focusable={{ group: `res-acts-${v.id}`, onSelect: () => dialogs.openAddTo(v.id, v.title) }}>＋ Add to playlist…</button>
							<OfflineButton videoId={v.id} offline={!!v.offline} group="res-acts-{v.id}" />
						{/snippet}
					</VideoCard>
				{/each}
			</div>
		{/if}
	</section>
</div>

<style>
	.search {
		display: grid;
		grid-template-columns: minmax(22rem, 34rem) minmax(0, 1fr);
		gap: clamp(1.5rem, 3vw, 3.5rem);
		align-items: start;
	}
	.left {
		position: sticky;
		top: calc(var(--topbar-h) + 1.5rem);
		display: grid;
		gap: 1.1rem;
	}
	.bar {
		display: flex; align-items: center; gap: .5rem;
		position: relative;
	}
	.mag {
		position: absolute;
		left: 1rem;
		top: 50%;
		transform: translateY(-50%);
		font-size: 1.4rem;
		color: var(--muted);
		pointer-events: none;
		z-index: 6;
	}
	.q {
		flex: 1; min-width: 0;
		min-height: 3.4rem;
		padding-left: 2.8rem;
		font-size: 1.2rem;
		border-radius: var(--r-pill);
	}
	.recent {
		display: grid;
		gap: 0.5rem;
	}
	.btn.add {
		min-height: 2.1rem;
		padding: 0.3rem 0.85rem;
		font-size: 0.82rem;
	}
	.hello h2 {
		color: var(--text);
	}
	.hello p {
		max-width: 30rem;
	}
	@media (max-width: 1000px) {
		.search {
			grid-template-columns: 1fr;
		}
		.left {
			position: static;
		}
	}
	@media (hover: none) {
		.osk {
			display: none;
		}
	}
</style>
