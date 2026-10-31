<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { api } from '$lib/api';
	import { agent } from '$lib/stores/agent.svelte';
	import { session } from '$lib/stores/session.svelte';
	import { dialogs } from '$lib/stores/dialogs.svelte';
	import { focusable } from '$lib/nav';
	import { fmtDuration, isVideo } from '$lib/util';
	import { toast } from '$lib/stores/toast.svelte';
	import { warmVideo } from '$lib/prefetch';
	import { preview } from '$lib/preview';
	import type { HomeRow, Video } from '$lib/types';
	import Row from '$lib/components/Row.svelte';
	import VideoCard from '$lib/components/VideoCard.svelte';
	import OfflineButton from '$lib/components/OfflineButton.svelte';
	import ScoutReportPreview from '$lib/hub/ScoutReportPreview.svelte';
	import ScoutsShelf from '$lib/hub/ScoutsShelf.svelte';
	import LookingPlaceholder from '$lib/components/LookingPlaceholder.svelte';
	import AgentGlyph from '$lib/components/AgentGlyph.svelte';

	let rows = $state<HomeRow[] | null>(null);
	let failed = $state(false);
	let heroEl = $state<HTMLElement | null>(null);
	// The hero buttons sit low in the hero, so a plain scroll-into-view would leave the top pick itself hidden.
	const showHero = () => window.scrollTo({ top: 0, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
	let backdropEl = $state<HTMLElement | null>(null);
	let heroPlayEl = $state<HTMLElement | null>(null);
	let feedVersion = 0;
	let loadSequence = 0;

	async function load() {
		const version = feedVersion;
		const sequence = ++loadSequence;
		try {
			const result = await api.home(24);
			if (version !== feedVersion || sequence !== loadSequence) return;
			rows = result;
			failed = false;
			warmLikely();
		} catch {
			if (version !== feedVersion || sequence !== loadSequence) return;
			failed = true;
			rows = rows ?? [];
		}
	}

	const WARM_GAP_MS = 3500;
	let warmTimers: ReturnType<typeof setTimeout>[] = [];

	/** Warm the likely first plays — the hero pick and each row's first card — spaced out in the
	 * background. A click then lands on ready streams instead of a 3s extraction plus format checks.
	 * Already-warm videos are no-ops; offline copies are skipped (they play instantly anyway). */
	function warmLikely() {
		for (const t of warmTimers) clearTimeout(t);
		warmTimers = [];
		if (!rows) return;
		const seen = new Set<string>();
		const targets: { id: string; offline?: boolean }[] = [];
		if (hero) targets.push({ id: hero.video.id, offline: hero.video.offline });
		for (const r of catRows) {
			const it = r.items.find((x) => x.video && isVideo(x.video));
			if (it && isVideo(it.video)) targets.push({ id: it.video.id, offline: it.video.offline });
		}
		for (const t of targets.slice(0, 10)) {
			if (seen.has(t.id)) continue;
			seen.add(t.id);
			warmTimers.push(setTimeout(() => warmVideo(t.id, { offline: t.offline, manifest: true, force: true }), (seen.size - 1) * WARM_GAP_MS));
		}
	}

	function dropFromRows(pred: (v: Video) => boolean) {
		feedVersion++;
		rows = (rows ?? []).map((r) =>
			({ ...r, items: r.items.filter((it) => !it.video || !pred(it.video)) }));
	}

	async function markSeen(v: Video) {
		try {
			await api.markSeen(v.id);
			dropFromRows((x) => x.id === v.id);
			toast.success("Marked as seen. The AI won't suggest it again.");
		} catch {
			toast.error('Could not mark it as seen.');
		}
	}

	async function dislikeChannel(v: Video) {
		if (!v.channel_id) return;
		try {
			await api.blockChannel(v.channel_id, v.channel_name);
			dropFromRows((x) => x.channel_id === v.channel_id);
			toast.success(`No more from ${v.channel_name ?? 'this channel'}. The AI will skip that channel.`);
		} catch {
			toast.error(`Could not block ${v.channel_name ?? 'this channel'}.`);
		}
	}

	function rateVideo(v: Video, categoryId?: number) {
		dialogs.openFeedback({
			videoId: v.id, categoryId: categoryId ?? null, title: v.title,
			onDone: (rating) => { if (rating < 0) dropFromRows((x) => x.id === v.id); }
		});
	}

	onMount(() => {
		load();
		let t: ReturnType<typeof setTimeout> | null = null;
		const off = agent.onEvent((e) => {
			if (e.kind !== 'accept') return;
			if (t) clearTimeout(t);
			t = setTimeout(load, 1500);
		});
		return () => {
			off();
			if (t) clearTimeout(t);
			for (const w of warmTimers) clearTimeout(w);
		};
	});

	const hero = $derived.by(() => {
		for (const r of rows ?? []) {
			if (r.kind !== 'category') continue;
			const it = r.items[0];
			if (it && isVideo(it.video)) return { row: r, item: it, video: it.video as Video };
		}
		return null;
	});
	const catRows = $derived((rows ?? []).filter((r) => r.kind === 'category'));
	const noCategories = $derived(rows !== null && !failed && catRows.length === 0 && session.categories.filter((c) => c.enabled).length === 0);
	const greeting = $derived.by(() => {
		const h = new Date().getHours();
		const part = h < 5 ? 'Late night' : h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening';
		return session.profile ? `${part}, ${session.profile.name}` : part;
	});
</script>


{#if rows === null}
	<div class="hero skeleton-hero">
		<div class="skeleton" style="height:2.5rem;width:40%"></div>
		<div class="skeleton" style="height:1.2rem;width:60%"></div>
		<div class="skeleton" style="height:3rem;width:12rem;border-radius:999px"></div>
	</div>
	{#each [0, 1] as _}
		<div class="sk-row">
			{#each [0, 1, 2, 3, 4] as __}<div class="skeleton sk-card"></div>{/each}
		</div>
	{/each}
{:else}
	{#if hero}
		{@const cat = session.categoryById(hero.row.category_id)}
		<section class="hero" style:--cat={hero.row.color ?? 'var(--ai)'} bind:this={heroEl}>
			<div class="backdrop" aria-hidden="true" bind:this={backdropEl} use:preview={{ id: hero.video.id, offline: !!hero.video.offline, listenOn: heroEl, focusOn: heroPlayEl, host: backdropEl }}>
				<img src={api.thumb(hero.video.id, 'maxres')} alt="" onerror={(e) => ((e.currentTarget as HTMLImageElement).src = api.thumb(hero.video.id, 'hq'))} />
			</div>
			<div class="hero-body">
				<p class="eyebrow"><span class="dot"></span>{hero.row.emoji} {hero.row.title}: top pick</p>
				<h1>{hero.video.title || 'Untitled video'}</h1>
				<p class="meta muted">
					{hero.video.channel_name}
					{#if hero.video.duration_s}<span class="sep"></span>{fmtDuration(hero.video.duration_s)}{/if}
					{#if hero.item.score != null}<span class="sep"></span><span class="score">✦ {hero.item.score} fit</span>{/if}
				</p>
				{#if hero.item.summary}
					<blockquote class="why">{hero.item.summary}</blockquote>
				{/if}
				<div class="hstack">
					<a class="btn primary big" bind:this={heroPlayEl} href="/watch/{hero.video.id}?c={hero.row.category_id}" use:focusable={{ group: 'hero', id: 'hero-play', autofocus: true, scroll: 'none', onFocus: showHero }}>▶ Play</a>
					<a class="btn big" href="/category/{hero.row.category_id}" use:focusable={{ group: 'hero', id: 'hero-cat', scroll: 'none', onFocus: showHero }}>All of {cat?.name ?? hero.row.title}</a>
				</div>
			</div>
		</section>
	{:else}
		<section class="hero welcome">
			<div class="hero-body">
				<p class="eyebrow"><span class="dot"></span>{greeting}</p>
				{#if noCategories}
					<h1>Tell the AI what you like to watch</h1>
					<p class="muted lead">Create a playlist like "Science for kids" or "Woodworking". The curator searches YouTube slowly in the background, reads transcripts and comments, and only keeps videos that fit.</p>
					<div class="hstack">
						<a class="btn primary big" href="/settings?tab=categories&new=1" use:focusable={{ group: 'hero', autofocus: true }}>Create a playlist</a>
						<a class="btn big" href="/search" use:focusable={{ group: 'hero' }}>Search instead</a>
					</div>
				{:else}
					<h1>Your curator is on it</h1>
					<p class="muted lead">No picks yet. The AI checks each video before it shows up here, so the first ones take a few minutes.</p>
					<div class="hstack">
						<a class="btn primary big" href="/agent" use:focusable={{ group: 'hero', autofocus: true }}>Watch the AI work</a>
						<a class="btn big" href="/search" use:focusable={{ group: 'hero' }}>Search</a>
					</div>
				{/if}
			</div>
			<ol class="how" aria-label="How the curator works">
				<li><span class="g"><AgentGlyph state="searching" size="1.6rem" /></span><div><strong>Searches slowly</strong><span>A few YouTube requests at a time, so the server never gets blocked.</span></div></li>
				<li><span class="g"><AgentGlyph state="transcript" size="1.6rem" /></span><div><strong>Reads before it picks</strong><span>Transcript, top comments and the thumbnail of every candidate.</span></div></li>
				<li><span class="g"><AgentGlyph state="judging" size="1.6rem" /></span><div><strong>Keeps only what fits</strong><span>Scores each video against your brief and explains why.</span></div></li>
			</ol>
		</section>
	{/if}

	<ScoutReportPreview />
	{#if session.profile?.prefs?.scouts_home_position !== 'bottom'}<ScoutsShelf />{/if}

	{#if failed}
		<p class="page muted">Could not load your rows. Check that the server is running, then press OK to retry.</p>
		<div class="page"><button class="btn" use:focusable={{ onSelect: load }}>Retry</button></div>
	{/if}

	{#each rows as row, ri (row.kind + (row.category_id ?? '') + ri)}
		{#if row.kind === 'continue'}
			<Row title={row.title} emoji={row.emoji} color="var(--accent)">
				{#each row.items as it, i}
					{#if it.video}
						{@const v = it.video}
						<VideoCard
							video={v}
							group="row-continue"
							id="cw-{v.id}"
							href="/watch/{v.id}{it.category_id ? `?c=${it.category_id}` : ''}"
							progress={v.duration_s ? (it.position_s ?? 0) / v.duration_s : 0}
						>
							{#snippet actions()}
								<OfflineButton videoId={v.id} offline={!!v.offline} group="cw-acts-{v.id}" />
								<button class="btn why" use:focusable={{ group: `cw-acts-${v.id}`, onSelect: () => markSeen(v) }}>
									Seen it
								</button>
								<button class="btn why" aria-label="Rate video" use:focusable={{ group: `cw-acts-${v.id}`, onSelect: () => rateVideo(v, it.category_id ?? undefined) }}>
									👍 👎
								</button>
								{#if v.channel_id}
									<button class="btn why" use:focusable={{ group: `cw-acts-${v.id}`, onSelect: () => dislikeChannel(v) }}>
										Don't like channel
									</button>
								{/if}
							{/snippet}
						</VideoCard>
					{/if}
				{/each}
			</Row>
		{:else if row.kind === 'category'}
			<Row title={row.title} emoji={row.emoji} color={row.color} href="/category/{row.category_id}" badge={row.items.length ? `AI found ${row.items.length}` : undefined}>
				{#if row.items.length === 0}
					<LookingPlaceholder categoryId={row.category_id} color={row.color} />
					<a class="more" href="/category/{row.category_id}" style:--cat={row.color} use:focusable={{ group: `row-${row.category_id}`, id: `more-${row.category_id}` }}>
						<span class="e">{row.emoji}</span><span>Open playlist</span>
					</a>
				{:else}
					{#each row.items as it}
						{#if it.video && isVideo(it.video)}
							{@const v = it.video}
							<VideoCard
								video={v}
								group="row-{row.category_id}"
								href="/watch/{v.id}?c={row.category_id}"
								score={it.score}
								summary={it.summary}
								color={row.color}
							>
								{#snippet actions()}
									<OfflineButton videoId={v.id} offline={!!v.offline} group="row-acts-{v.id}" />
									<button class="btn why" use:focusable={{ group: `row-acts-${v.id}`, onSelect: () => markSeen(v) }}>
										Seen it
									</button>
									<button class="btn why" aria-label="Rate video" use:focusable={{ group: `row-acts-${v.id}`, onSelect: () => rateVideo(v, it.category_id ?? undefined) }}>
										👍 👎
									</button>
									{#if v.channel_id}
										<button class="btn why" use:focusable={{ group: `row-acts-${v.id}`, onSelect: () => dislikeChannel(v) }}>
											Don't like channel
										</button>
									{/if}
								{/snippet}
							</VideoCard>
						{/if}
					{/each}
					<a class="more" href="/category/{row.category_id}" style:--cat={row.color} use:focusable={{ group: `row-${row.category_id}`, id: `more-${row.category_id}` }}>
						<span class="e">{row.emoji}</span><span>See all, plus why each was picked</span>
					</a>
				{/if}
			</Row>
		{:else}
			<Row title={row.title} emoji={row.emoji} color="var(--info)">
				{#each row.items as it}
					{#if it.video}<VideoCard video={it.video} group="row-subs" />{/if}
				{/each}
				{#if row.items.length === 0}<p class="muted">No new uploads from your channels yet.</p>{/if}
			</Row>
		{/if}
	{/each}
	{#if session.profile?.prefs?.scouts_home_position === 'bottom'}<ScoutsShelf />{/if}
	<div class="end"></div>
{/if}

<style>
	.hero {
		position: relative;
		min-height: clamp(20rem, 46vh, 34rem);
		display: flex;
		align-items: flex-end;
		padding: clamp(1.5rem, 3vw, 3.5rem) var(--gutter) clamp(1rem, 2vw, 2rem);
		overflow: hidden;
		isolation: isolate;
	}
	.backdrop {
		position: absolute;
		inset: 0;
		z-index: -1;
	}
	.backdrop img {
		position: absolute;
		right: 0;
		top: 0;
		width: min(100%, 72rem);
		height: 100%;
		object-fit: cover;
		mask-image: linear-gradient(90deg, transparent 0%, #000 45%), linear-gradient(0deg, transparent 0%, #000 40%);
		mask-composite: intersect;
		-webkit-mask-composite: source-in;
		opacity: 0.75;
		animation: drift 30s ease-in-out infinite alternate;
	}
	/* the hover preview keeps the same masked, dimmed backdrop look as the still */
	.backdrop :global(.bt-hover-pv) {
		mask-image: linear-gradient(90deg, transparent 0%, #000 45%), linear-gradient(0deg, transparent 0%, #000 40%);
		mask-composite: intersect;
		-webkit-mask-composite: source-in;
	}
	.backdrop :global(.bt-hover-pv.ready) {
		opacity: 0.75;
	}
	.backdrop :global(.bt-hover-pv.bye) {
		opacity: 0;
	}
	.backdrop::after {
		content: '';
		position: absolute;
		inset: 0;
		background: radial-gradient(60% 80% at 15% 90%, color-mix(in srgb, var(--cat) 30%, transparent), transparent 70%);
	}
	@keyframes drift {
		to {
			transform: scale(1.06) translateX(-1%);
		}
	}
	@media (max-width: 760px) {
		.backdrop img {
			width: 100%;
			mask-image: linear-gradient(0deg, transparent 15%, #000 75%);
			-webkit-mask-image: linear-gradient(0deg, transparent 15%, #000 75%);
			mask-composite: add;
			opacity: 0.55;
		}
	}
	.hero-body {
		display: grid;
		gap: 1rem;
		max-width: min(46rem, 100%);
	}
	.eyebrow {
		display: flex;
		align-items: center;
		gap: 0.55rem;
		font-weight: 650;
		color: var(--muted);
	}
	.eyebrow .dot {
		width: 0.55rem;
		height: 0.55rem;
		border-radius: 50%;
		background: var(--cat, var(--accent));
		box-shadow: 0 0 12px var(--cat, var(--accent));
	}
	.hero h1 {
		text-shadow: 0 4px 30px rgba(0, 0, 0, 0.6);
		display: -webkit-box;
		-webkit-line-clamp: 3;
		line-clamp: 3;
		-webkit-box-orient: vertical;
		overflow: hidden;
	}
	.meta {
		display: flex;
		align-items: center;
		gap: 0.7rem;
		flex-wrap: wrap;
	}
	.sep {
		width: 0.3rem;
		height: 0.3rem;
		border-radius: 50%;
		background: var(--faint);
	}
	.why {
		margin: 0;
		padding: 0.2rem 0 0.2rem 1rem;
		border-left: 3px solid var(--accent);
		font-size: 1.08rem;
		line-height: 1.5;
		color: #e4dfff;
		max-width: 40rem;
	}
	.lead {
		font-size: 1.1rem;
		max-width: 40rem;
	}
	.welcome {
		gap: clamp(2rem, 5vw, 6rem);
		align-items: center;
		justify-content: space-between;
		flex-wrap: wrap;
		min-height: clamp(24rem, 62vh, 44rem);
	}
	/* A projector beam falling from the top-right corner. */
	.welcome::before {
		content: '';
		position: absolute;
		z-index: -1;
		right: -5%;
		top: -40%;
		width: 75%;
		height: 180%;
		background: conic-gradient(from 205deg at 100% 0%, transparent 0deg, rgba(255, 181, 71, 0.12) 14deg, rgba(157, 134, 255, 0.1) 30deg, transparent 48deg);
		filter: blur(24px);
		pointer-events: none;
	}
	.how {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		gap: 1rem;
		width: min(100%, 30rem);
	}
	.how li {
		display: flex;
		gap: 1rem;
		align-items: flex-start;
		padding: 1rem 1.2rem;
		border-radius: var(--r-lg);
		background: rgba(28, 24, 62, 0.55);
		border: 1px solid var(--line);
		backdrop-filter: var(--blur);
	}
	.how .g {
		display: grid;
		place-items: center;
		width: 2.8rem;
		height: 2.8rem;
		flex: none;
		border-radius: 50%;
		color: var(--accent);
		background: rgba(255, 181, 71, 0.12);
	}
	.how div {
		display: grid;
		gap: 0.2rem;
	}
	.how span {
		color: var(--muted);
		font-size: 0.9rem;
	}
	.more {
		--focus-scale: 1.06;
		flex: none;
		width: calc(var(--card-w) * 0.62);
		aspect-ratio: 16 / 9;
		align-self: start;
		display: grid;
		place-content: center;
		justify-items: center;
		gap: 0.4rem;
		text-align: center;
		padding: 0.8rem;
		border-radius: var(--r-md);
		border: 1px solid color-mix(in srgb, var(--cat) 45%, transparent);
		background: color-mix(in srgb, var(--cat) 12%, rgba(255, 255, 255, 0.02));
		font-weight: 650;
		font-size: 0.9rem;
	}
	.more .e {
		font-size: 1.6rem;
	}
	.more:hover {
		background: color-mix(in srgb, var(--cat) 22%, transparent);
	}
	.skeleton-hero {
		display: grid;
		gap: 1rem;
		align-content: end;
	}
	.sk-row {
		display: flex;
		gap: 1.2rem;
		padding: 2rem var(--gutter) 0;
		overflow: hidden;
	}
	.sk-card {
		flex: none;
		width: var(--card-w);
		aspect-ratio: 16/9;
	}
	.end {
		height: 3rem;
	}
</style>
