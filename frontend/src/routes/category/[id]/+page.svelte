<script lang="ts">
	import { page } from '$app/state';
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import { agent, STATE_LABEL } from '$lib/stores/agent.svelte';
	import { dialogs } from '$lib/stores/dialogs.svelte';
	import { session } from '$lib/stores/session.svelte';
	import { toast } from '$lib/stores/toast.svelte';
	import { focusable } from '$lib/nav';
	import { isVideo, timeAgo } from '$lib/util';
	import { warmVideo } from '$lib/prefetch';
	import type { Candidate, Category } from '$lib/types';
	import VideoCard from '$lib/components/VideoCard.svelte';
	import AgentGlyph from '$lib/components/AgentGlyph.svelte';
	import OfflineButton from '$lib/components/OfflineButton.svelte';

	type Tab = 'accepted' | 'pending' | 'rejected';
	const id = $derived(Number(page.params.id));
	let cat = $state<Category | null>(null);
	let tab = $state<Tab>((page.url.searchParams.get('tab') as Tab) || 'accepted');
	let items = $state<Candidate[] | null>(null);
	let notFound = $state(false);

	async function loadCat() {
		try {
			cat = await api.category(id);
		} catch {
			notFound = true;
		}
	}
	async function loadItems() {
		items = null;
		try {
			items = await api.categoryFeed(id, tab, { limit: 80 });
		} catch {
			items = [];
		}
		// Warm the first picks of the grid: the likeliest next plays.
		if (tab === 'accepted')
			items.slice(0, 3).forEach((c, i) => setTimeout(() => warmVideo(c.video.id, { offline: isVideo(c.video) && !!c.video.offline, manifest: true, force: true }), i * 3500));
	}
	onMount(() => {
		loadCat();
		const off = agent.onEvent((e) => {
			if (e.category_id === id && (e.kind === 'accept' || e.kind === 'reject')) {
				loadCat();
				loadItems();
			}
		});
		return off;
	});
	$effect(() => {
		void tab;
		loadItems();
	});

	async function findMore() {
		await api.agentRun(id);
		toast.success(agent.control.paused ? 'Queued. Resume the AI to start searching.' : `The AI will search for ${cat?.name ?? 'this playlist'} next.`);
	}

	async function markSeen(c: Candidate) {
		try {
			await api.markSeen(c.video.id);
			items = (items ?? []).filter((x) => x.id !== c.id);
			toast.success("Marked as seen. The AI won't suggest it again.");
		} catch {
			toast.error("Could not mark it as seen.");
		}
	}

	async function dislikeChannel(c: Candidate) {
		if (!isVideo(c.video)) return;
		const chId = c.video.channel_id;
		const name = c.video.channel_name ?? 'this channel';
		if (!chId) return;
		try {
			await api.blockChannel(chId, name);
			items = (items ?? []).filter((x) => !isVideo(x.video) || x.video.channel_id !== chId);
			toast.success(`No more from ${name}. The AI will skip that channel.`);
		} catch {
			toast.error(`Could not block ${name}.`);
		}
	}

	function rateVideo(c: Candidate) {
		dialogs.openFeedback({
			videoId: c.video.id, categoryId: id, title: isVideo(c.video) ? c.video.title : undefined,
			onDone: (rating) => { if (rating < 0) items = (items ?? []).filter((x) => x.video.id !== c.video.id); }
		});
	}

	const working = $derived(agent.state.category_id === id && agent.effective !== 'paused' && agent.effective !== 'offline');
	const TABS: { key: Tab; label: string; hint: string }[] = [
		{ key: 'accepted', label: 'Picked', hint: 'accepted' },
		{ key: 'pending', label: 'In review', hint: 'pending' },
		{ key: 'rejected', label: 'Rejected', hint: 'rejected' }
	];
	const count = (k: Tab) => {
		const st = cat?.stats ?? {};
		if (k === 'pending') return (st.found ?? 0) + (st.enriched ?? 0);
		return st[k] ?? 0;
	};
</script>


<div class="page" style:--cat={cat?.color ?? 'var(--ai)'}>
	{#if notFound}
		<div class="empty"><span class="big-emoji">🫥</span><p>This playlist does not exist for the current profile.</p><a class="btn" href="/" use:focusable={{ autofocus: true }}>Go home</a></div>
	{:else}
		<header class="head">
			<div class="emo">{cat?.emoji ?? '✨'}</div>
			<div class="ht">
				<h1>{cat?.name ?? '…'}</h1>
				{#if cat?.brief}<p class="brief muted">{cat.brief}</p>{/if}
				<div class="hstack small muted">
					<span>Target {cat?.target_queue ?? '–'} videos</span>
					<span>Accept at {cat?.threshold ?? '–'}+</span>
					{#if cat?.kid_safe}<span class="kid">Kid-safe</span>{/if}
					{#if cat?.created_at}<span>Created {timeAgo(cat.created_at)}</span>{/if}
					{#if cat?.last_worked_at}<span>Last searched {timeAgo(cat.last_worked_at)}</span>{/if}
				</div>
			</div>
			<div class="acts">
				<button class="btn primary" use:focusable={{ group: 'cat-acts', id: 'find-more', onSelect: findMore }}>✦ Find more now</button>
				<a class="btn" href="/settings?tab=categories&edit={id}" use:focusable={{ group: 'cat-acts', id: 'edit-cat' }}>Edit playlist</a>
			</div>
		</header>

		{#if working}
			<div class="live">
				<AgentGlyph state={agent.effective} />
				<span><strong>{STATE_LABEL[agent.effective] ?? agent.effective}</strong> {agent.state.detail ?? ''}</span>
			</div>
		{/if}

		<div class="tabs" role="tablist">
			{#each TABS as t}
				<button
					class="tab"
					role="tab"
					aria-selected={tab === t.key}
					class:on={tab === t.key}
					use:focusable={{ group: 'cat-tabs', id: `tab-${t.key}`, autofocus: tab === t.key, onSelect: () => (tab = t.key) }}
				>
					{t.label}<span class="n">{count(t.key)}</span>
				</button>
			{/each}
		</div>

		{#if items === null}
			<div class="grid-cards">{#each Array(8) as _}<div class="skeleton" style="aspect-ratio:16/10"></div>{/each}</div>
		{:else if items.length === 0}
			<div class="empty">
				<span class="big-emoji">{tab === 'accepted' ? '🔭' : tab === 'pending' ? '📭' : '🧹'}</span>
				<p>
					{#if tab === 'accepted'}Nothing picked yet. Press "Find more now" to send the AI looking.
					{:else if tab === 'pending'}Nothing waiting for review.
					{:else}Nothing rejected so far.{/if}
				</p>
			</div>
		{:else}
			<div class="grid-cards" data-nav-center>
				{#each items as c (c.id)}
					<div class="cand">
						<VideoCard
							video={c.video}
							size="grid"
							group="cands"
							href="/watch/{c.video.id}?c={id}"
							score={c.score}
							summary={c.summary}
							color={cat?.color}
						>
							{#snippet actions()}
								<button class="btn why" use:focusable={{ group: `cand-acts-${c.id}`, onSelect: () => dialogs.openCandidate(c.id) }}>
									{c.status === 'rejected' ? 'Why rejected?' : c.status === 'accepted' ? 'Why picked?' : 'Details'}
								</button>
								{#if c.status === 'accepted'}<OfflineButton videoId={c.video.id} offline={isVideo(c.video) && !!c.video.offline} status={isVideo(c.video) ? (c.video.download_status ?? null) : null} group="cand-acts-{c.id}" />{/if}
									{#if c.status === 'accepted' || c.status === 'found' || c.status === 'enriched'}
										<button class="btn why" use:focusable={{ group: `cand-acts-${c.id}`, onSelect: () => markSeen(c) }}>
											{c.status === 'accepted' ? 'Seen it' : 'Seen it, skip'}
										</button>
									{/if}
									{#if c.status === 'accepted'}
										<button class="btn why" aria-label="Rate video" use:focusable={{ group: `cand-acts-${c.id}`, onSelect: () => rateVideo(c) }}>
											👍 👎
										</button>
									{/if}
									{#if c.status === 'accepted' && isVideo(c.video) && c.video.channel_id}
										<button class="btn why" use:focusable={{ group: `cand-acts-${c.id}`, onSelect: () => dislikeChannel(c) }}>
											Don't like channel
										</button>
									{/if}
								{#if c.source === 'manual'}<span class="chip small-chip">Added by you</span>{/if}
							{/snippet}
						</VideoCard>
						{#if c.status === 'rejected' && (c.red_flags?.length || c.reasons?.length)}
							<ul class="flags">
								{#each (c.red_flags?.length ? c.red_flags : c.reasons).slice(0, 2) as f}<li>{f}</li>{/each}
							</ul>
						{:else if c.status !== 'accepted' && !isVideo(c.video)}
							<p class="small faint">Waiting for details…</p>
						{/if}
					</div>
				{/each}
			</div>
		{/if}
	{/if}
</div>

<style>
	.head {
		display: flex;
		gap: clamp(1rem, 2vw, 2rem);
		align-items: center;
		flex-wrap: wrap;
		padding: 1rem 0 1.5rem;
	}
	.emo {
		display: grid;
		place-items: center;
		width: clamp(4rem, 6vw, 6.5rem);
		height: clamp(4rem, 6vw, 6.5rem);
		border-radius: var(--r-lg);
		font-size: clamp(2.2rem, 3vw, 3.5rem);
		background: color-mix(in srgb, var(--cat) 22%, transparent);
		border: 1px solid color-mix(in srgb, var(--cat) 50%, transparent);
		box-shadow: 0 0 40px -6px color-mix(in srgb, var(--cat) 60%, transparent);
	}
	.ht {
		flex: 1;
		min-width: min(100%, 20rem);
		display: grid;
		gap: 0.5rem;
	}
	.brief {
		max-width: 60rem;
		display: -webkit-box;
		-webkit-line-clamp: 2;
		line-clamp: 2;
		-webkit-box-orient: vertical;
		overflow: hidden;
	}
	.kid {
		color: var(--ok);
		font-weight: 650;
	}
	.acts {
		display: flex;
		gap: 0.6rem;
		flex-wrap: wrap;
	}
	.live {
		display: flex;
		align-items: center;
		gap: 0.7rem;
		padding: 0.7rem 1rem;
		margin-bottom: 1rem;
		border-radius: var(--r-md);
		background: rgba(255, 181, 71, 0.08);
		border: 1px solid rgba(255, 181, 71, 0.3);
		color: var(--accent-2);
	}
	.tabs {
		display: flex;
		gap: 0.4rem;
		margin: 0.5rem 0 1.6rem;
		padding: 0.3rem;
		border-radius: var(--r-pill);
		background: rgba(255, 255, 255, 0.04);
		border: 1px solid var(--line);
		width: fit-content;
		max-width: 100%;
		overflow-x: auto;
	}
	.tab {
		--focus-scale: 1.06;
		display: flex;
		align-items: center;
		gap: 0.5rem;
		padding: 0.55rem 1.1rem;
		border-radius: var(--r-pill);
		border: 0;
		background: transparent;
		color: var(--muted);
		font-weight: 650;
		cursor: pointer;
	}
	.tab.on {
		background: var(--surface-hi);
		color: var(--text);
	}
	.n {
		font-size: 0.78rem;
		padding: 0.05rem 0.5rem;
		border-radius: 1rem;
		background: rgba(255, 255, 255, 0.08);
		font-variant-numeric: tabular-nums;
	}
	.cand {
		display: grid;
		gap: 0.5rem;
		align-content: start;
	}
	.btn.why {
		min-height: 2.1rem;
		padding: 0.3rem 0.85rem;
		font-size: 0.82rem;
	}
	.small-chip {
		font-size: 0.75rem;
		cursor: default;
	}
	.flags {
		margin: 0;
		padding: 0.6rem 0.8rem 0.6rem 2rem;
		border-radius: var(--r-md);
		background: rgba(255, 92, 122, 0.08);
		border: 1px solid rgba(255, 92, 122, 0.25);
		font-size: 0.82rem;
		color: #ffd1d9;
		display: grid;
		gap: 0.2rem;
	}
</style>
