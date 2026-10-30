<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { api } from '$lib/api';
	import { focusable } from '$lib/nav';
	import { session } from '$lib/stores/session.svelte';
	import { toast } from '$lib/stores/toast.svelte';
	import { fmtBytes, fmtCount, fmtDuration } from '$lib/util';
	import type { StatsCategoryRow, StatsExploreFilter, StatsPageData } from '$lib/types';
	import StatsExplorer from '$lib/components/StatsExplorer.svelte';
	import StatsDonut from '$lib/components/StatsDonut.svelte';
	import StatsActivityChart from '$lib/components/StatsActivityChart.svelte';
	import ContextOverview from '$lib/components/ContextOverview.svelte';

	let data = $state<StatsPageData | null>(null);
	let days = $state(30);
	let loading = $state(false);
	let showQuietDays = $state(false);
	let explorer = $state<{ title: string; filter: StatsExploreFilter } | null>(null);
	let loadSeq = 0;

	async function load() {
		const seq = ++loadSeq;
		loading = true;
		try {
			const result = await api.statsPage(days);
			if (seq === loadSeq) data = result;
		} catch {
			data = data ?? null; // api already toasts
		}
		if (seq === loadSeq) loading = false;
	}

	onMount(load);

	function setDays(d: number) {
		if (days === d) return;
		days = d;
		load();
	}

	const ov = $derived(data?.overview);
	const cats = $derived(data?.categories ?? []);
	const daily = $derived(data?.daily ?? []);
	const visibleDaily = $derived(daily.filter(d => showQuietDays || d.found || d.accepted || d.rejected_ai || d.rejected_family || d.watched || d.feedback || d.downloads || d.yt_requests));
	const enc = $derived(data?.reencode);
	const library = $derived(data?.library);
	function drill(metric: string, title: string, category_id?: number, day?: string, height?: number) {
		explorer = { title, filter: { metric, category_id, day, height } };
	}
	const hours = (seconds: number) => `${(seconds / 3600).toLocaleString(undefined, { maximumFractionDigits: 1 })} h`;
	const overviewTiles = $derived(ov ? [
		{ value: fmtCount(ov.videos), label: 'Videos known', metric: 'videos' },
		{ value: fmtCount(ov.candidates.in_queue), label: 'Ready in queues', metric: 'ready' },
		{ value: fmtCount(ov.candidates.waiting_judge), label: 'Waiting to be judged', metric: 'review' },
		{ value: fmtCount(ov.downloads.waiting ?? 0), label: 'Waiting to download', metric: 'waiting_download' },
		{ value: fmtCount(ov.downloads.done ?? 0), label: 'Offline copies', sub: fmtBytes(ov.storage_bytes), metric: 'offline' },
		{ value: fmtCount(ov.watched), label: 'Videos watched', sub: `${hours(ov.watch_seconds)} watched`, metric: 'watched' },
		{ value: fmtCount(ov.candidates.rejected_ai), label: 'Judge rejected', metric: 'rejected_ai', warn: true },
		{ value: fmtCount(ov.candidates.rejected_family), label: 'Family rejected', metric: 'rejected_family', warn: true },
		{ value: fmtCount(ov.candidates.dismissed), label: 'Dismissed', metric: 'dismissed' },
		{ value: fmtCount(ov.feedback.dislike + ov.feedback.note + ov.feedback.like + ov.feedback.love), label: 'Family feedback', sub: `👍 ${ov.feedback.like + ov.feedback.love} · 👎 ${ov.feedback.dislike}`, metric: 'feedback' },
		{ value: fmtCount(ov.lessons.active), label: 'Active lessons', sub: `${ov.lessons.inactive} off`, metric: 'lessons' },
		{ value: fmtCount(ov.budget.day), label: 'YT requests today', sub: `of ${ov.budget.max_day} daily limit`, metric: 'requests' }
	] : []);
	const decisionSegments = $derived([
		{ key: 'accepted', label: 'Accepted', color: '#5ed9b0' },
		{ key: 'decision_rejected_ai', label: 'Judge rejected', color: '#ee9a76' },
		{ key: 'decision_rejected_family', label: 'Family rejected', color: '#ed83ac' },
		{ key: 'review', label: 'In review', color: '#a996ff' },
		{ key: 'dismissed', label: 'Dismissed', color: '#8294b7' },
		{ key: 'error', label: 'Errors', color: '#f0c56e' }
	].map(s => ({ ...s, value: data?.decisions?.[s.key.replace('decision_', '')] ?? 0 })));
	const palette = ['#a996ff', '#76bbff', '#5ed9b0', '#f0c56e', '#ee9a76', '#ed83ac'];
	const qualitySegments = $derived((library?.quality ?? []).map((q, i) => ({ key: q.label, label: q.label, value: q.count, color: palette[i % palette.length] })));
	const maxDaily = $derived.by(() => {
		let m = 0;
		for (const d of daily) m = Math.max(m, d.found + d.accepted + d.rejected_ai + d.rejected_family + d.watched);
		return m || 1;
	});

	const catOf = (id: string) => cats.find((c) => c.id === Number(id));
	const shortDate = (iso: string) => iso.slice(5).replace('-', '/');
	const watchedBreakdown = (d: Record<string, number>) =>
		Object.entries(d)
			.map(([id, n]) => `${catOf(id)?.emoji ?? '•'} ×${n}`)
			.join(' ');

	/** Stats span every profile; a category only opens under its own profile. */
	async function openCategory(c: StatsCategoryRow, e?: Event) {
		if (c.profile_id === session.profileId) return; // plain link navigation
		e?.preventDefault();
		await session.switchProfile(c.profile_id);
		toast.info(`Switched to ${c.profile_name}'s profile`);
		goto(`/category/${c.id}`);
	}
</script>

{#snippet countLink(value: number | string, metric: string, label: string, category?: StatsCategoryRow, day?: string)}
	<button class="count-link" aria-label="{label}{category ? ` · ${category.name}` : ''}{day ? ` · ${day}` : ''}: {value}" use:focusable={{ group: category ? `stats-cat-${category.id}` : day ? `stats-day-${day}` : 'stats-counts', onSelect: () => drill(metric, category ? `${label} · ${category.name}` : label, category?.id, day) }}>{value}</button>
{/snippet}


<div class="page stack stats" style="gap:2.2rem">
	<header class="head">
		<h1>Statistics</h1>
		<p class="muted">Your family's viewing library, discoveries, and verdicts across all profiles. Click a number to explore the records behind it.</p>
		<div class="hstack">
			{#each [7, 30, 90] as d}
				<button class="chip" class:on={days === d} use:focusable={{ group: 'stats-ctl', id: `d-${d}`, onSelect: () => setDays(d) }}>{d} days</button>
			{/each}
			<button class="btn mini" disabled={loading} use:focusable={{ group: 'stats-ctl', id: 'refresh', onSelect: load }}>↻ Refresh</button>
		</div>
	</header>

	{#if !ov}
		<div class="skeleton" style="height:10rem"></div>
		<div class="skeleton" style="height:14rem"></div>
	{:else}
		<section class="panel">
			<h2>Overview <span class="faint small">all time</span></h2>
			<div class="tiles">
				{#each overviewTiles as t (t.metric)}
					<button class="tile interactive" class:warn={t.warn} aria-label="Explore {t.label}" use:focusable={{ group: 'stats-overview', onSelect: () => drill(t.metric, t.label) }}><span class="v">{t.value}</span><span>{t.label} <span aria-hidden="true">›</span></span>{#if t.sub}<span class="sub">{t.sub}</span>{/if}</button>
				{/each}
			</div>
		</section>

		{#if library}
			<section class="panel stack">
				<h2>Your offline library <span class="faint small">currently saved</span></h2>
				<div class="tiles library-tiles">
					<button class="tile interactive" use:focusable={{ group: 'stats-library', onSelect: () => drill('offline', 'Offline copies') }}><span class="v">{hours(library.duration_s)}</span><span>Total offline viewing time ›</span><span class="sub">{(library.duration_s / 86400).toLocaleString(undefined, { maximumFractionDigits: 1 })} days of nonstop watching</span></button>
					<button class="tile interactive" use:focusable={{ group: 'stats-library', onSelect: () => drill('offline', 'Offline copies') }}><span class="v">{fmtDuration(library.average_duration_s)}</span><span>Average saved video length ›</span></button>
					<button class="tile interactive" use:focusable={{ group: 'stats-library', onSelect: () => drill('offline_channels', 'Offline channels') }}><span class="v">{fmtCount(library.channels)}</span><span>Channels in your offline library ›</span></button>
					<button class="tile interactive" use:focusable={{ group: 'stats-library', onSelect: () => drill('offline', 'Offline copies') }}><span class="v">{fmtBytes(library.average_size_bytes)}</span><span>Average saved file size ›</span></button>
				</div>
				{#if library.longest}<p class="small muted">Longest saved video: <a class="text-link" href="/watch/{library.longest.id}" use:focusable={{ group: 'stats-library' }}>{library.longest.title || library.longest.id}</a> · {hours(library.longest.duration_s)}</p>{/if}
				{#if library.duration_unknown}<p class="small faint">Runtime is known for {library.duration_known.toLocaleString()} copies; {library.duration_unknown.toLocaleString()} have no duration yet.</p>{/if}
			</section>
		{/if}

		<div class="charts-grid">
			<section class="panel stack">
				<h2>Curator decisions</h2>
				<StatsDonut title="Curator decisions" segments={decisionSegments} onSelect={(metric, label) => drill(metric, label)} />
				<p class="small faint">All-time playlist entries. Dismissed entries are grouped separately from their earlier verdict.</p>
			</section>
			<section class="panel stack">
				<h2>Offline video quality</h2>
				<StatsDonut title="Offline video quality" segments={qualitySegments} onSelect={(key, label) => drill('offline', `Offline copies · ${label}`, undefined, undefined, parseInt(key) || 0)} />
				<p class="small faint">Saved copies by resolution. Select a quality to browse its videos.</p>
			</section>
		</div>

		{#if enc}
			<section class="panel stack">
				<h2>GPU re-encoding <span class="faint small">all time</span></h2>
				<div class="tiles">
					{#each [
						{ value: enc.queued, metric: 'encode_queue', label: 'Queued or in progress' },
						{ value: enc.encoded_copies, metric: 'encoded_copies', label: 'Encoded copies currently saved' },
						{ value: enc.kept_not_smaller, metric: 'encode_kept', label: 'Original kept: output was not smaller' },
						{ value: enc.job_errors, metric: 'encode_errors', label: 'Existing videos with encoding errors' }
					] as t}
						<button class="tile interactive" use:focusable={{ group: 'stats-encode', onSelect: () => drill(t.metric, t.label) }}><span class="v">{fmtCount(t.value)}</span><span>{t.label} ›</span></button>
					{/each}
				</div>
				<h3>Measured results</h3>
				<div class="tiles">
					<button class="tile interactive" use:focusable={{ group: 'stats-measured', onSelect: () => drill('encode_saved', 'Successful encoding attempts') }}><span class="v">{fmtBytes(enc.saved_bytes)}</span><span>Space saved by recorded encodes ›</span></button>
					<button class="tile interactive" use:focusable={{ group: 'stats-measured', onSelect: () => drill('encode_saved', 'Successful encoding attempts') }}><span class="v">{enc.original_bytes ? `${Math.round(100 * enc.saved_bytes / enc.original_bytes)}%` : '–'}</span><span>Average reduction on successful encodes ›</span></button>
					{#each [
						{ value: enc.recorded_saved, metric: 'encode_saved', label: 'Successful encodes measured' },
						{ value: enc.recorded_not_smaller, metric: 'encode_not_smaller', label: 'Attempts not smaller' },
						{ value: enc.recorded_failed, metric: 'encode_failed', label: 'Failed attempts' }
					] as t}
						<button class="tile interactive" use:focusable={{ group: 'stats-measured', onSelect: () => drill(t.metric, t.label) }}><span class="v">{fmtCount(t.value)}</span><span>{t.label} ›</span></button>
					{/each}
				</div>
				<p class="small muted">Measured results include new and existing videos from when size tracking was added. Earlier encodes are included in the current saved-copy count, but their original sizes were not recorded. Attempts can include retries; savings are cumulative even if a video is later deleted.</p>
			</section>
		{/if}
		{#if data?.context}
			<section class="panel stack"><h2>AI context per request <span class="faint small">last 24 hours</span></h2><ContextOverview context={data.context} detailed /><a class="text-link small" href="/settings?tab=memory" use:focusable={{ group: 'stats-memory' }}>View core memory and consolidation settings</a></section>
		{/if}

		<section class="panel">
			<h2>Per playlist</h2>
			{#if cats.length === 0}
				<p class="muted">No playlists yet. Create one in Settings and the AI will start filling it.</p>
			{:else}
				<div class="twrap">
					<table class="table">
						<thead>
							<tr>
								<th>Playlist</th>
								<th>Ready</th>
								<th>In review</th>
								<th>Watched</th>
								<th>Time watched</th>
								<th>Judge rejected</th>
								<th>Family rejected</th>
								<th>Dismissed</th>
								<th>Avg score</th>
							</tr>
						</thead>
						<tbody>
							{#each cats as c (c.id)}
								<tr class:off={!c.enabled}>
									<td>
										<a class="cat" href="/category/{c.id}" style:--cat={c.color} use:focusable={{ group: 'stats-cats', id: `cat-${c.id}`, onSelect: (e) => openCategory(c, e) }}>
											<span class="dot"></span>{c.emoji} {c.name}
										</a>
										<span class="profile-name">{c.profile_name}</span>
										{#if !c.enabled}<span class="faint tiny"> off</span>{/if}
									</td>
									<td>
										{@render countLink(`${c.counts.in_queue}/${c.target}`, 'ready', 'Ready in queue', c)}
										<span class="gauge"><span style:width="{Math.min(100, (c.counts.in_queue / Math.max(1, c.target)) * 100)}%"></span></span>
									</td>
									<td class="num-s">{@render countLink(c.counts.found + c.counts.enriched, 'review', 'In review', c)}</td>
									<td class="num-s">{@render countLink(c.counts.watched, 'watched', 'Watched', c)}</td>
									<td class="num-s">{@render countLink(fmtDuration(c.watch_seconds), 'watched', 'Time watched', c)}</td>
									<td class="num-s">{@render countLink(c.counts.rejected_ai, 'rejected_ai', 'Judge rejected', c)}</td>
									<td class="num-s you">{@render countLink(c.counts.rejected_family, 'rejected_family', 'Family rejected', c)}</td>
									<td class="num-s">{@render countLink(c.counts.dismissed, 'dismissed', 'Dismissed', c)}</td>
									<td class="num-s">{@render countLink(c.avg_score ?? '–', 'scored', 'Accepted scores', c)}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		</section>

		<section class="panel">
			<h2>Daily activity <span class="faint small">last {days} days</span></h2>
			<StatsActivityChart {daily} />
			{#if daily.every((d) => !d.found && !d.accepted && !d.rejected_ai && !d.rejected_family && !d.watched && !d.feedback && !d.downloads && !d.yt_requests)}
				<p class="muted">Nothing recorded in this window yet.</p>
			{:else}
				<button class="chip quiet-toggle" aria-pressed={showQuietDays} use:focusable={{ group: 'stats-daily', onSelect: () => showQuietDays = !showQuietDays }}>{showQuietDays ? 'Hide quiet days' : 'Show quiet days'}</button>
				<div class="twrap">
					<table class="table">
						<thead>
							<tr>
								<th>Date</th>
								<th>Activity</th>
								<th>Watched</th>
								<th>Found</th>
								<th>Accepted</th>
								<th>Judge rejected</th>
								<th>Family rejected</th>
								<th>Feedback</th>
								<th>Downloads</th>
								<th>YT reqs</th>
							</tr>
						</thead>
						<tbody>
							{#each [...visibleDaily].reverse() as d (d.date)}
								{@const activity = d.found + d.accepted + d.rejected_ai + d.rejected_family + d.watched}
								<tr class:dim={activity === 0 && !d.feedback && !d.downloads}>
									<td class="num-s">{shortDate(d.date)}</td>
									<td><span class="gauge"><span class="max" style:width="{(activity / maxDaily) * 100}%"></span></span></td>
									<td>
										{@render countLink(d.watched, 'watched', 'Watched', undefined, d.date)}
										{#if Object.keys(d.watched_by_category).length}<span class="tiny faint">{watchedBreakdown(d.watched_by_category)}</span>{/if}
									</td>
									<td class="num-s">{@render countLink(d.found || '·', 'found', 'Found', undefined, d.date)}</td>
									<td class="num-s ok">{@render countLink(d.accepted || '·', 'accepted', 'Accepted', undefined, d.date)}</td>
									<td class="num-s">{@render countLink(d.rejected_ai || '·', 'rejected_ai', 'Judge rejected', undefined, d.date)}</td>
									<td class="num-s you">{@render countLink(d.rejected_family || '·', 'rejected_family', 'Family rejected', undefined, d.date)}</td>
									<td class="num-s">{@render countLink(d.feedback || '·', 'feedback', 'Family feedback', undefined, d.date)}</td>
									<td class="num-s">{@render countLink(d.downloads || '·', 'downloads', 'Download results', undefined, d.date)}</td>
									<td class="num-s">{@render countLink(d.yt_requests || '·', 'requests', 'YouTube requests', undefined, d.date)}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		</section>
	{/if}
</div>

{#if explorer}
	{#key JSON.stringify(explorer)}<StatsExplorer title={explorer.title} filter={explorer.filter} onClose={() => explorer = null} />{/key}
{/if}

<style>
    .stats, .stats > *, .twrap {
        min-width: 0;
        max-width: 100%;
    }
    .profile-name {
        display: block;
        color: var(--faint);
        font-size: 0.8rem;
        margin-top: 0.2rem;
    }
	.head {
		display: grid;
		gap: 0.8rem;
	}
	.btn.mini {
		min-height: 2rem;
		padding: 0.2rem 0.8rem;
		font-size: 0.8rem;
	}
	.tiles {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(min(100%, 9.5rem), 1fr));
		gap: 0.8rem;
	}
	.tile {
		display: grid;
		gap: 0.15rem;
		padding: 0.9rem 1rem;
		border-radius: var(--r-md);
		border: 1px solid var(--line);
		background: rgba(255, 255, 255, 0.04);
		font-size: 0.82rem;
		color: var(--muted);
		text-align: left;
	}
	.interactive { cursor: pointer; font-family: inherit; transition: background 0.15s, border-color 0.15s; }
	.interactive:hover { background: rgba(157, 134, 255, 0.13); border-color: var(--accent); }
	.count-link { font: inherit; font-variant-numeric: tabular-nums; color: inherit; background: none; border: 0; padding: 0.15rem 0; cursor: pointer; text-decoration: underline; text-decoration-color: rgba(157,134,255,0.4); text-underline-offset: 0.25rem; border-radius: 0.2rem; }
	.count-link:hover, .text-link:hover { color: var(--accent); }
	.text-link { color: var(--text); text-decoration: underline; text-underline-offset: 0.2rem; }
	.charts-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1.25rem; }
	.charts-grid > * { min-width: 0; }
	.library-tiles { grid-template-columns: repeat(auto-fit, minmax(min(100%, 12rem), 1fr)); }
	.quiet-toggle { margin-bottom: 0.8rem; }
	@media (max-width: 950px) { .charts-grid { grid-template-columns: 1fr; } }
	.tile .v {
		font-size: 1.6rem;
		font-weight: 800;
		color: var(--text);
		font-variant-numeric: tabular-nums;
		letter-spacing: -0.02em;
	}
	.tile .sub {
		color: var(--faint);
	}
	.tile.warn .v {
		color: var(--warn);
	}
	.twrap {
		overflow-x: auto;
	}
	.table {
		width: 100%;
        min-width: 40rem;
        table-layout: fixed;
		border-collapse: collapse;
		font-size: 0.92rem;
	}
    .table th:first-child { width: 20%; }
	.table th {
		text-align: left;
		font-size: 0.78rem;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--faint);
		font-weight: 700;
		padding: 0.4rem 0.45rem 0.6rem;
		white-space: normal;
        overflow-wrap: anywhere;
	}
	.table td {
		padding: 0.55rem 0.45rem;
		border-top: 1px solid var(--line);
		white-space: normal;
        overflow-wrap: anywhere;
	}
	.table tr.off td {
		opacity: 0.55;
	}
	.table tr.dim td {
		opacity: 0.45;
	}
	.dot {
		width: 0.55rem;
		height: 0.55rem;
		border-radius: 50%;
		background: var(--cat, var(--accent));
		display: inline-block;
		margin-right: 0.5rem;
		box-shadow: 0 0 8px var(--cat, transparent);
	}
	.cat:hover {
		text-decoration: underline;
		text-decoration-color: var(--cat, var(--accent));
		text-underline-offset: 0.3rem;
	}
	.num-s {
		font-variant-numeric: tabular-nums;
	}
	.you {
		color: var(--warn);
		font-weight: 650;
	}
	.ok {
		color: var(--ok);
	}
	.gauge {
		display: inline-block;
		vertical-align: middle;
		width: min(100%, 4rem);
		height: 0.4rem;
		border-radius: 1rem;
		background: rgba(255, 255, 255, 0.1);
		overflow: hidden;
	}
	.gauge span {
		display: block;
		height: 100%;
		border-radius: inherit;
		background: var(--cat, var(--ok));
	}
	.gauge span.max {
		background: linear-gradient(90deg, var(--accent), var(--ai));
	}
</style>
