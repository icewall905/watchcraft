<script lang="ts">
	import { untrack } from 'svelte';
	import { hub, ago, type ShelfStatus as ShelfSnapshot } from './api';
	import { session } from '$lib/stores/session.svelte';
	import { focusable } from '$lib/nav';

	let snapshot = $state<ShelfSnapshot | null>(null);
	let loading = $state(false);
	let failed = $state(false);
	let expanded = $state(false);
	let refreshKey = $state(0);
	let loadToken = 0;
	let lastIdentity = '';
	const allowed = () => session.ready && !!session.profile && !session.profile.pin_locked && !session.needsLogin && !session.choosing && session.hubAllowed;

	$effect(() => {
		const identity = `${session.profileId}:${session.version}:${session.profile?.pin_locked}:${session.needsLogin}:${session.choosing}:${session.hubAllowed}`;
		const canLoad = allowed();
		refreshKey;
		if (identity !== lastIdentity) {
			lastIdentity = identity;
			snapshot = null;
			failed = false;
			loading = false;
			expanded = false;
		}
		const token = ++loadToken;
		const controller = new AbortController();
		let stopped = false;
		let timer: number | undefined;
		let requestInFlight = false;
		const current = () => !stopped && token === loadToken && identity === `${session.profileId}:${session.version}:${session.profile?.pin_locked}:${session.needsLogin}:${session.choosing}:${session.hubAllowed}` && allowed();
		const load = async () => {
			if (!current() || document.visibilityState === 'hidden' || requestInFlight) return;
			requestInFlight = true;
			loading = !untrack(() => snapshot);
			try {
				const result = await hub.shelf(controller.signal);
				if (current() && !controller.signal.aborted) { snapshot = result; failed = false; }
			} catch {
				if (current() && !controller.signal.aborted) failed = true;
			} finally {
				requestInFlight = false;
				if (current()) loading = false;
			}
		};
		const onVisibility = () => { if (document.visibilityState === 'visible') void load(); };
		const onFocus = () => { if (document.visibilityState === 'visible') void load(); };
		if (!canLoad) {
			snapshot = null;
			failed = false;
			loading = false;
		} else {
			void load();
			timer = window.setInterval(() => { if (document.visibilityState === 'visible') void load(); }, 60_000);
			document.addEventListener('visibilitychange', onVisibility);
			window.addEventListener('focus', onFocus);
		}
		return () => { stopped = true; controller.abort(); if (timer) clearInterval(timer); document.removeEventListener('visibilitychange', onVisibility); window.removeEventListener('focus', onFocus); };
	});

	const queued = $derived((snapshot?.maintenance.collectors_queued.length ?? 0) > 0);
	const articlesGap = $derived(snapshot ? Math.max(0, snapshot.articles.target - snapshot.articles.ready) : 0);
	const videosGap = $derived(snapshot ? (snapshot.videos.categories.length
		? snapshot.videos.categories.reduce((sum, category) => sum + Math.max(0, category.target - category.ready), 0)
		: Math.max(0, snapshot.videos.target - snapshot.videos.ready)) : 0);
	const checked = (value: number | null) => value ? ago(value) : 'not yet';
</script>

{#if allowed()}
	<section class="shelf" aria-labelledby="shelf-title" aria-busy={loading}>
		<div class="heading">
			<div><p class="eyebrow">Shelf health</p><h2 id="shelf-title">Your shelf</h2></div>
			{#if queued}<span class="activity" aria-label="Collection jobs are queued"><i aria-hidden="true"></i>Refilling</span>
			{:else if snapshot?.articles.needs_refill || snapshot?.videos.needs_refill || articlesGap || videosGap}<span class="activity quiet">A little more would help</span>
		{:else if snapshot}<span class="activity quiet">Ready for you</span>{/if}
		</div>

		{#if snapshot}
			<div class="shelves">
				<article class="measure">
					<div class="measure-head"><span class="icon" aria-hidden="true">📰</span><strong>Articles</strong><a href="/news" use:focusable>Browse <span aria-hidden="true">↗</span></a></div>
					<p class="counts"><strong>{snapshot.articles.ready}</strong><span>ready to read</span></p>
					<p class="detail">{snapshot.articles.fresh_24h} new in 24 hours <span>·</span> {snapshot.articles.publishers} publishers</p>
					{#if articlesGap}<p class="note">{articlesGap} more would fill your {snapshot.articles.target}-article shelf.</p>{:else if snapshot.articles.needs_refill}<p class="note">More recent coverage would help keep this shelf fresh.</p>{/if}
				</article>
				<article class="measure">
					<div class="measure-head"><span class="icon" aria-hidden="true">▶</span><strong>Videos</strong><a href="/shorts" use:focusable>Browse <span aria-hidden="true">↗</span></a></div>
					<p class="counts"><strong>{snapshot.videos.ready}</strong><span>ready to watch</span></p>
					<p class="detail">{snapshot.videos.fresh_7d} published or found this week <span>·</span> {snapshot.videos.creators} creators</p>
					{#if snapshot.videos.failed}<p class="note">{snapshot.videos.failed} saved files or captions need attention. <a href="/stats" use:focusable>View Stats</a></p>{/if}
					{#if snapshot.videos.pending}<p class="detail">{snapshot.videos.pending} videos awaiting preparation.</p>{/if}
					{#if videosGap}<p class="note">Some categories still need {videosGap} ready videos.</p>{:else if snapshot.videos.needs_refill}<p class="note">More variety would help keep this shelf fresh.</p>{/if}
				</article>
			</div>
			<details class="details" bind:open={expanded}>
				<summary use:focusable>See interests and variety <span aria-hidden="true">⌄</span></summary>
				<div class="detail-grid">
					<div><h3>Article interests</h3>
						{#if snapshot.articles.interests.length}
							{#each snapshot.articles.interests as interest (interest.id)}<p class="interest-row"><span><strong>{interest.name}</strong><small>{interest.fresh_24h} new · {interest.publishers} publishers</small></span><span class="progress-label">{interest.ready}/{interest.target}</span></p>{/each}
						{:else}<p class="muted">Your interests will appear here as they are set up.</p>{/if}
					</div>
					<div><h3>Video categories</h3>
						{#if snapshot.videos.categories.length}
							{#each snapshot.videos.categories as category (category.id)}<p class="interest-row"><span><strong>{category.emoji} {category.name}</strong><small>{category.fresh_7d} new · {category.creators} creators</small></span><span class="progress-label">{category.ready}/{category.target}</span></p>{/each}
						{:else}<p class="muted">Your video categories will appear here.</p>{/if}
					</div>
				</div>
				<p class="updated">Checked {checked(snapshot.maintenance.last_checked_at)}{#if snapshot.maintenance.next_check_at} · Next check {checked(snapshot.maintenance.next_check_at)}{/if}</p>
			</details>
		{:else if failed}
			<div class="unavailable"><span>The shelf overview is unavailable right now.</span><button class="retry" onclick={() => (refreshKey++)}>Try again</button></div>
		{:else}<p class="loading" role="status">Loading shelf overview…</p>{/if}
		<nav class="links" aria-label="Shelf sections"><a href="/team" use:focusable>Collection activity <span aria-hidden="true">↗</span></a><a href="/" use:focusable>Home videos <span aria-hidden="true">↗</span></a></nav>
	</section>
{/if}

<style>
	.shelf { margin: 0 var(--gutter) 1.5rem; padding: 1rem 1.15rem .85rem; border: 1px solid var(--rule); border-radius: var(--r-md); background: color-mix(in srgb, var(--surface) 82%, var(--accent) 3%); }
	.heading, .measure-head, .counts, .interest-row, .links { display: flex; align-items: center; }
	.heading { justify-content: space-between; gap: .75rem; margin-bottom: .8rem; }
	.eyebrow { margin: 0 0 .15rem; color: var(--muted); font-size: .72rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
	h2 { margin: 0; font-size: 1.15rem; }
	.activity { display: inline-flex; align-items: center; gap: .4rem; border-radius: 999px; padding: .35rem .65rem; background: color-mix(in srgb, var(--accent) 12%, var(--surface)); color: var(--accent); font-size: .8rem; font-weight: 700; }
	.activity i { width: .45rem; height: .45rem; border-radius: 50%; background: currentColor; }
	.activity.quiet { color: var(--muted); background: transparent; font-weight: 600; }
	.shelves { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .75rem; }
	.measure { min-width: 0; padding: .85rem; border: 1px solid var(--rule); border-radius: var(--r-sm, .65rem); background: var(--surface); }
	.measure-head { gap: .5rem; }
	.icon { color: var(--accent); }
	.measure-head strong { flex: 1; }
	a { color: var(--accent); text-decoration: none; font-size: .85rem; }
	a:hover { text-decoration: underline; }
	.counts { gap: .45rem; margin: .55rem 0 .15rem; }
	.counts strong { font-size: 1.45rem; line-height: 1; }
	.counts span, .detail, .note, .muted { color: var(--muted); font-size: .84rem; }
	.detail { margin: 0; }
	.detail span { padding: 0 .2rem; }
	.note { margin: .55rem 0 0; color: var(--ink); }
	.details { margin-top: .7rem; border-top: 1px solid var(--rule); padding-top: .6rem; }
	summary { width: fit-content; cursor: pointer; color: var(--accent); font-size: .85rem; font-weight: 650; }
	.detail-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1rem; padding-top: .6rem; }
	h3 { margin: .2rem 0 .35rem; font-size: .86rem; }
	.interest-row { justify-content: space-between; gap: .6rem; margin: 0; padding: .35rem 0; border-bottom: 1px solid var(--rule); font-size: .82rem; }
	.interest-row > span:first-child { min-width: 0; overflow-wrap: anywhere; }
	.interest-row small { display: block; margin-top: .1rem; color: var(--muted); }
	.progress-label { flex: none; color: var(--muted); font-variant-numeric: tabular-nums; }
	.updated { margin: .65rem 0 0; color: var(--muted); font-size: .75rem; }
	.links { gap: 1.1rem; margin-top: .75rem; padding-top: .6rem; border-top: 1px solid var(--rule); }
	.links a { font-size: .8rem; }
	.unavailable { display: flex; justify-content: space-between; align-items: center; gap: .6rem; color: var(--muted); font-size: .85rem; }
	.retry { border: 0; background: none; color: var(--accent); font: inherit; cursor: pointer; }
	.loading { margin: .4rem 0; color: var(--muted); font-size: .85rem; }
	@media (max-width: 560px) { .shelf { padding: .85rem; } .shelves { grid-template-columns: 1fr; } .detail-grid { grid-template-columns: 1fr; gap: .4rem; } .measure { padding: .75rem; } }
</style>
