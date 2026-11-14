<script lang="ts">
	import { api, request } from '$lib/api';
	import { focusable } from '$lib/nav';
	import { session } from '$lib/stores/session.svelte';
	import type { Mix, MusicHome } from '$lib/music/api';
	import { boardsApi, type BoardPin, type MoodBoard } from '$lib/hub/boards';
	import SectionHeader from '$lib/hub/calm/SectionHeader.svelte';
	import type { HomeRow, Video } from '$lib/types';

	// The rest of what was curated today, outside the master edition: only items from today.
	const startOfDay = () => { const d = new Date(); d.setHours(0, 0, 0, 0); return d.getTime(); };
	const isToday = (ms: number | null | undefined) => !!ms && ms >= startOfDay();

	let videos = $state<Video[]>([]);
	let mixes = $state<Mix[]>([]);
	let boards = $state<{ board: MoodBoard; pins: BoardPin[] }[]>([]);

	$effect(() => {
		const version = session.version;
		const hubAllowed = session.hubAllowed;
		videos = []; mixes = []; boards = [];
		if (!session.ready || session.needsLogin || session.choosing) return;
		const controller = new AbortController();
		const current = () => !controller.signal.aborted && version === session.version;
		void request<HomeRow[]>('/feed/home?per_row=12', { signal: controller.signal, toast: false }).then((rows) => {
			if (!current()) return;
			const seen = new Set<string>();
			videos = rows.filter((r) => r.kind !== 'continue').flatMap((r) => r.items)
				.map((i) => i.video).filter((v): v is Video => !!v && !v.is_short && isToday(v.published_at ? Date.parse(v.published_at) : null))
				.filter((v) => !seen.has(v.id) && !!seen.add(v.id)).slice(0, 6);
		}).catch(() => {});
		if (!hubAllowed) return () => controller.abort();
		void request<MusicHome>('/music/home', { signal: controller.signal, toast: false }).then((home) => {
			if (!current()) return;
			mixes = [...home.mixes, ...home.recent_mixes]
				.filter((m, i, all) => m.status === 'ready' && all.findIndex((x) => x.id === m.id) === i && isToday(Math.max(m.created_at, m.refreshed_at) * 1000))
				.slice(0, 4);
		}).catch(() => {});
		void boardsApi.list(controller.signal).then(async ({ boards: list }) => {
			const fresh = list.filter((b) => b.pin_count > 0 && isToday((b.last_run ?? 0) * 1000))
				.sort((a, b) => (b.last_run ?? 0) - (a.last_run ?? 0)).slice(0, 2);
			const pages = await Promise.all(fresh.map((b) => boardsApi.detail(b.id, {}, controller.signal).then((p) => ({ board: b, pins: p.pins.slice(0, 4) })).catch(() => null)));
			if (current()) boards = pages.filter((p): p is { board: MoodBoard; pins: BoardPin[] } => !!p && p.pins.length > 0);
		}).catch(() => {});
		return () => controller.abort();
	});
</script>

{#if videos.length}
	<section class="section" aria-label="New videos">
		<SectionHeader title="New videos" action={{ href: '/', label: 'TV' }} />
		<div class="tiles" data-nav-row>
			{#each videos as v (v.id)}
				<a class="tile" href={`/watch/${encodeURIComponent(v.id)}`} use:focusable>
					<img src={api.thumb(v.id, 'mq')} alt="" loading="lazy" />
					<strong>{v.title}</strong><span>{v.channel_name}</span>
				</a>
			{/each}
		</div>
	</section>
{/if}

{#if mixes.length}
	<section class="section" aria-label="Music">
		<SectionHeader title="Music" action={{ href: '/music', label: 'Music Videos' }} />
		<div class="tiles" data-nav-row>
			{#each mixes as m (m.id)}
				<a class="tile" href="/music" use:focusable>
					{#if m.covers[0]}<img src={m.covers[0]} alt="" loading="lazy" />{:else}<span class="cover" style:background={m.color}>{m.emoji}</span>{/if}
					<strong>{m.emoji} {m.name}</strong><span>{m.count} songs{m.blurb ? ` · ${m.blurb}` : ''}</span>
				</a>
			{/each}
		</div>
	</section>
{/if}

{#if boards.length}
	<section class="section" aria-label="Mood Board">
		<SectionHeader title="Mood Board" action={{ href: '/boards', label: 'All boards' }} />
		{#each boards as { board, pins } (board.id)}
			<h3 class="board-name">{board.name}</h3>
			<div class="pins" data-nav-row>
				{#each pins as pin (pin.id)}
					<a class="pin" href="/boards" use:focusable aria-label={pin.title || board.name}>
						<img src={`/api/hub/boards/${board.id}/pins/${pin.id}/media`} alt="" loading="lazy" />
					</a>
				{/each}
			</div>
		{/each}
	</section>
{/if}

<style>
	.section { margin-top: clamp(1.7rem, 4vw, 3rem); }
	.tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 13rem), 1fr)); gap: clamp(.8rem, 2vw, 1.5rem); }
	.tile { display: grid; gap: .3rem; min-width: 0; color: var(--ink); text-decoration: none; }
	.tile img, .cover { width: 100%; aspect-ratio: 16 / 9; object-fit: cover; border-radius: var(--r-md); background: var(--bg-2); }
	.cover { display: grid; place-items: center; font-size: 2rem; }
	.tile strong { overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; font: 520 var(--fs-md) / var(--lh-snug) var(--font-display); overflow-wrap: anywhere; }
	.tile span { overflow: hidden; color: var(--faint); font-size: var(--fs-sm); text-overflow: ellipsis; white-space: nowrap; }
	.tile:hover strong, .tile:focus-visible strong { color: var(--accent); }
	.board-name { margin: .8rem 0 .5rem; color: var(--ink-soft); font: 500 var(--fs-md) / 1.4 var(--font-display); overflow-wrap: anywhere; }
	.pins { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .6rem; }
	.pin img { width: 100%; aspect-ratio: 1; object-fit: cover; border-radius: var(--r-md); background: var(--bg-2); display: block; }
	@media (max-width: 440px) { .tiles { grid-template-columns: repeat(2, minmax(0, 1fr)); } .pins { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
</style>
