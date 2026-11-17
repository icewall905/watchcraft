<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { focusable } from '$lib/nav';
	import Row from '$lib/components/Row.svelte';
	import Card from '$lib/jellyfin/Card.svelte';
	import { fmtRuntime, fmtTime, img, jellyfin, subtitleLine, type Item } from '$lib/jellyfin/api';
	import { playback } from '$lib/stores/playback.svelte';

	const id = $derived(page.params.id ?? '');
	let it = $state<Item | null>(null);
	let season = $state<string | null>(null);
	let episodes = $state<Item[]>([]);
	let failed = $state('');

	$effect(() => {
		const v = id;
		it = null; failed = ''; episodes = []; season = null;
		jellyfin.item(v).then((r) => {
			if (v !== id) return;
			it = r;
			if (r.type === 'Series' && r.seasons?.length) {
				const want = r.next_up?.season_id ?? r.seasons.find((s) => !s.played)?.id ?? r.seasons[0].id;
				pickSeason(want);
			} else if (r.type === 'Season') {
				episodes = r.episodes ?? [];
			}
		}).catch((e) => (failed = (e as Error).message));
	});

	function pickSeason(sid: string) {
		if (!it) return;
		season = sid;
		const series = it.id;
		jellyfin.episodes(series, sid).then((r) => { if (season === sid) episodes = r.items; }).catch(() => {});
	}

	const playTarget = $derived(it?.type === 'Series' ? it.next_up ?? episodes[0] ?? null : it?.playable ? it : null);
	const resumeAt = $derived(playTarget && playTarget.progress > 0 && !playTarget.played ? playTarget.position_s : 0);

	function play(target: Item, fromStart = false) {
		// With a screen chosen in Play on, playing sends there, like tapping a video elsewhere.
		if (playback.selectedId) return void playback.castJellyfin(target.id, fromStart ? 0 : undefined);
		goto(`/jellyfin/play/${target.id}${fromStart ? '?start=0' : ''}`);
	}
	async function toggleWatched() {
		if (!it) return;
		await jellyfin.played(it.id, !it.played);
		it.played = !it.played;
	}
	async function toggleFav() {
		if (!it) return;
		await jellyfin.favorite(it.id, !it.favorite);
		it.favorite = !it.favorite;
	}
</script>

<svelte:head><title>{it?.name ?? 'Jellyfin'} · The Marquee</title></svelte:head>

<div class="page detail">
	{#if failed}
		<p class="err pad">{failed}</p>
	{:else if it}
		{#if it.backdrop}<div class="bd" style:background-image="url({img(it.backdrop, 1600)})" aria-hidden="true"></div>{/if}
		<section class="hero">
			{#if it.image}<img class="poster" src={img(it.image, 400)} alt="" />{/if}
			<div class="meta">
				{#if it.series_name && it.series_id}<a class="series" href="/jellyfin/item/{it.series_id}">{it.series_name}</a>{/if}
				<h1>{it.name}</h1>
				<p class="facts small">
					{subtitleLine(it)}
					{#if it.official_rating} · <span class="rating">{it.official_rating}</span>{/if}
					{#if it.community_rating} · ★ {it.community_rating.toFixed(1)}{/if}
					{#if it.genres.length} · {it.genres.slice(0, 3).join(', ')}{/if}
				</p>
				<div class="acts">
					{#if playTarget}
						<button class="btn primary" use:focusable={{ group: 'd-acts', autofocus: true, onSelect: () => play(playTarget) }}>
							▶ {resumeAt ? `Resume from ${fmtTime(resumeAt)}` : it.type === 'Series' && playTarget.episode != null ? `Play S${playTarget.season}E${playTarget.episode}` : 'Play'}
						</button>
						{#if resumeAt}<button class="btn" use:focusable={{ group: 'd-acts', onSelect: () => play(playTarget, true) }}>Start over</button>{/if}
						<button class="btn" use:focusable={{ group: 'd-acts', onSelect: () => { if (playTarget) void playback.castJellyfin(playTarget.id); } }}>
							📺 {playback.selected ? `Play on ${playback.selected.name}` : 'Play on…'}
						</button>
					{/if}
					<button class="btn" use:focusable={{ group: 'd-acts', onSelect: toggleWatched }}>{it.played ? '✓ Watched' : 'Mark watched'}</button>
					<button class="btn" aria-pressed={it.favorite} use:focusable={{ group: 'd-acts', onSelect: toggleFav }}>{it.favorite ? '❤️' : '🤍'} Favourite</button>
				</div>
				{#if it.overview}<p class="ov">{it.overview}</p>{/if}
				{#if it.audio?.length || it.subtitles?.length}
					<p class="small muted">
						{#if it.audio?.length}Audio: {it.audio.map((a) => a.label).join(', ')}{/if}
						{#if it.subtitles?.length}<br />Subtitles: {it.subtitles.map((s) => s.label).join(', ')}{/if}
					</p>
				{/if}
				{#if it.people?.length}<p class="small muted">With {it.people.filter((p) => p.role && p.role !== 'Director').slice(0, 6).map((p) => p.name).join(', ')}</p>{/if}
			</div>
		</section>

		{#if it.type === 'Series' && it.seasons?.length}
			<div class="seasons" role="tablist" aria-label="Seasons">
				{#each it.seasons as s (s.id)}
					<button class="btn mini" role="tab" class:primary={season === s.id} aria-selected={season === s.id}
						use:focusable={{ group: 'd-seasons', onSelect: () => pickSeason(s.id) }}>{s.name}{s.played ? ' ✓' : ''}</button>
				{/each}
			</div>
		{/if}
		{#if episodes.length}
			<ol class="eps">
				{#each episodes as ep (ep.id)}
					<li>
						<button class="ep" use:focusable={{ group: 'd-eps', onSelect: () => play(ep) }} aria-label={`Play episode ${ep.episode}: ${ep.name}`}>
							<span class="still">
								{#if ep.thumb ?? ep.image}<img src={img(ep.thumb ?? ep.image, 360)} alt="" loading="lazy" />{/if}
								{#if ep.progress > 0 && !ep.played}<span class="bar"><i style:width="{Math.round(ep.progress * 100)}%"></i></span>{/if}
							</span>
							<span class="txt">
								<strong>{ep.episode != null ? `${ep.episode}. ` : ''}{ep.name}{ep.played ? ' ✓' : ''}</strong>
								<span class="small muted">{[ep.premiere, fmtRuntime(ep.runtime_s)].filter(Boolean).join(' · ')}</span>
								{#if ep.overview}<span class="small clamp">{ep.overview}</span>{/if}
							</span>
						</button>
					</li>
				{/each}
			</ol>
		{/if}
		{#if it.type === 'BoxSet' || (it.is_folder && it.type !== 'Series' && it.type !== 'Season')}
			{#await jellyfin.items({ parent_id: it.id, sort: 'PremiereDate' }) then r}
				<Row title="In this collection">{#each r.items as c (c.id)}<Card item={c} />{/each}</Row>
			{/await}
		{/if}
	{:else}
		<p class="muted pad">Loading…</p>
	{/if}
</div>

<style>
	.detail { position: relative; padding-bottom: 3rem; }
	.bd {
		position: absolute; inset: 0 0 auto 0; height: 70vh; background-size: cover; background-position: center top;
		mask-image: linear-gradient(to bottom, rgba(0, 0, 0, 0.45), transparent); pointer-events: none;
	}
	.hero { position: relative; display: flex; gap: 2rem; padding: 2rem var(--gutter) 1rem; align-items: flex-start; }
	.poster { width: clamp(9rem, 18vw, 16rem); border-radius: var(--r-md); box-shadow: var(--shadow-lg); flex: none; }
	.meta { display: grid; gap: 0.7rem; max-width: 52rem; }
	h1 { margin: 0; font-family: var(--font-display); font-size: var(--fs-xl); }
	.series { color: var(--accent); text-decoration: none; }
	.facts { margin: 0; color: var(--muted); }
	.rating { border: 1px solid var(--line); padding: 0 0.3rem; border-radius: 0.25rem; }
	.acts { display: flex; flex-wrap: wrap; gap: 0.5rem; }
	.ov { margin: 0; line-height: var(--lh-body); }
	.seasons { position: relative; display: flex; flex-wrap: wrap; gap: 0.5rem; padding: 1rem var(--gutter); }
	.eps { position: relative; list-style: none; margin: 0; padding: 0 var(--gutter); display: grid; gap: 0.6rem; }
	.ep {
		display: flex; gap: 1rem; width: 100%; text-align: left; background: var(--surface-solid); border: 1px solid transparent;
		border-radius: var(--r-md); padding: 0.6rem; color: inherit; cursor: pointer;
	}
	.ep:focus-visible, .ep:hover { border-color: var(--accent); }
	.still { position: relative; flex: none; width: clamp(9rem, 16vw, 14rem); aspect-ratio: 16 / 9; border-radius: 0.5rem; overflow: hidden; background: #0004; }
	.still img { width: 100%; height: 100%; object-fit: cover; }
	.bar { position: absolute; left: 0; right: 0; bottom: 0; height: 4px; background: rgba(0, 0, 0, 0.5); }
	.bar i { display: block; height: 100%; background: var(--accent); }
	.txt { display: grid; gap: 0.25rem; align-content: start; }
	.clamp { display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
	.err { color: var(--bad); }
	.pad { padding: 1rem var(--gutter); }
	@media (max-width: 640px) {
		.hero { flex-direction: column; }
		.ep { flex-direction: column; }
		.still { width: 100%; }
	}
</style>
