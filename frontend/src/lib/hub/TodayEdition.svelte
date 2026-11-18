<script lang="ts">
	import { onDestroy, onMount, untrack } from 'svelte';
	import { hub } from '$lib/hub/api';
	import type { EditionEntry, TodayEdition } from '$lib/hub/types';
	import { session } from '$lib/stores/session.svelte';
	import { focusable } from '$lib/nav';
	import Cited from '$lib/hub/Cited.svelte';
	import LocalImage from '$lib/hub/LocalImage.svelte';
	import StoryCard from '$lib/hub/calm/StoryCard.svelte';
	import SetAsideLink from '$lib/hub/calm/SetAsideLink.svelte';
	import WhyPopover from '$lib/hub/calm/WhyPopover.svelte';
	import EditionActions from '$lib/hub/calm/EditionActions.svelte';
	import ArticleFeedback from '$lib/hub/ArticleFeedback.svelte';
	import SeenDismiss from '$lib/hub/SeenDismiss.svelte';
	import ShelfStatus from '$lib/hub/ShelfStatus.svelte';
	import Overview from '$lib/hub/overview/Overview.svelte';

	let { onloadingchange }: { onloadingchange?: (loading: boolean) => void } = $props();
	let edition = $state<TodayEdition | null>(null);
	let loading = $state(true);
	let error = $state('');
	let revision = $state(0);
	let notice = $state('');
	let controller: AbortController | null = null;
	let loadToken = 0;
	let editionVersion = -1;
	let editionProfile: number | null = null;
	let clock = $state(Date.now());
	let lastRefresh = 0;
	let rerunning = $state(false);
	// Today shows the lead; the pages themselves are summarised by <Overview>.
	// Only rendered entries count as delivered (the server's preview limits).
	const showing = { stories: 0, reads: 0, memes: 0 } as const;
	const accessAllowed = () => session.ready && !session.needsLogin && !session.choosing && !session.profile?.pin_locked;
	function clearEdition() {
		edition = null;
	}
	async function regenerate() {
		const pid = session.profileId, version = session.version;
		rerunning = true;
		try {
			const r = await hub.regenerateToday();
			if (pid === session.profileId && version === session.version && accessAllowed()) {
				notice = r.rerun ? 'Rebuilt from the best of everything new. The curator is re-checking and will polish it shortly.' : 'Rebuilt from the best of everything new.';
				await load();
			}
		} catch (e) { if (pid === session.profileId && version === session.version && accessAllowed()) notice = e instanceof Error ? e.message : 'Could not regenerate Today.'; }
		finally { rerunning = false; }
	}

	async function load(background = false) {
		if (background && (document.visibilityState === 'hidden' || controller)) return;
		if (!accessAllowed()) {
			loadToken++;
			controller?.abort();
			controller = null;
			clearEdition();
			loading = false;
			onloadingchange?.(false);
			return;
		}
		const token = ++loadToken;
		controller?.abort();
		controller = new AbortController();
		const signal = controller.signal;
		const profileVersion = session.version;
		const profileId = session.profileId;
		if (editionVersion !== profileVersion || editionProfile !== profileId) edition = null;
		loading = !edition;
		onloadingchange?.(loading);
		error = '';
		try {
			const result = await hub.today(signal, { ...showing });
			if (signal.aborted || token !== loadToken || profileVersion !== session.version || profileId !== session.profileId || !accessAllowed()) return;
			if (edition?.rerun && edition.rerun.requested_at > edition.rerun.completed_at && result.rerun && result.rerun.completed_at >= result.rerun.requested_at) notice = 'Master curator finished. Your selection is up to date.';
			edition = result;
			editionVersion = profileVersion;
			editionProfile = profileId;
		} catch (e) {
			if (signal.aborted || token !== loadToken || profileVersion !== session.version || profileId !== session.profileId || !accessAllowed()) return;
			if (!edition) error = e instanceof Error ? e.message : 'Today could not be loaded.';
		} finally {
			if (!signal.aborted && token === loadToken && profileVersion === session.version && profileId === session.profileId && accessAllowed()) {
				loading = false;
				onloadingchange?.(false);
				controller = null;
				lastRefresh = Date.now();
			}
		}
	}

	$effect(() => {
		const access = accessAllowed();
		const identity = `${session.profileId}:${session.version}:${session.profile?.pin_locked}:${session.needsLogin}:${session.choosing}`;
		if (!access || identity !== editionIdentity) {
			editionIdentity = identity;
			clearEdition();
		}
		revision;
		untrack(() => { void load(); });
	});
	onMount(() => {
		const refresh = () => {
			clock = Date.now();
			if (document.visibilityState !== 'hidden') void load(true);
		};
		const timer = window.setInterval(() => {
			clock = Date.now();
			if (clock - lastRefresh >= (edition?.rerun && edition.rerun.requested_at > edition.rerun.completed_at ? 10_000 : 5 * 60_000)) void load(true);
		}, 10_000);
		window.addEventListener('focus', refresh);
		document.addEventListener('visibilitychange', refresh);
		return () => {
			window.clearInterval(timer);
			window.removeEventListener('focus', refresh);
			document.removeEventListener('visibilitychange', refresh);
		};
	});
	onDestroy(() => controller?.abort());

	const section = (key: string) => edition?.sections.find((s) => s.key === key);
	const entries = (key: string): EditionEntry[] => {
		const found = section(key);
		return found && 'entries' in found ? found.entries : [];
	};
	let editionIdentity = '';
	const lead = $derived(entries('lead')[0]);
	const curating = $derived(!!(edition?.rerun && edition.rerun.requested_at > edition.rerun.completed_at));
	const greeting = $derived.by(() => {
		const hour = new Date(clock).getHours();
		const name = session.profile?.name ? `, ${session.profile.name}` : '';
		return `${hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening'}${name}`;
	});
	const today = $derived(new Intl.DateTimeFormat(undefined, { weekday: 'long', month: 'long', day: 'numeric' }).format(new Date(clock)));
	const itemHref = (entry: EditionEntry) => `/item/${entry.item_id}`;
	const hasLocalImage = (entry: EditionEntry) => entry.item.local_image;
	const archivedSummary = (entry: EditionEntry) => typeof entry.item.enrichment?.summary === 'string' ? entry.item.enrichment.summary.trim() : '';
	const cardSummary = (entry: EditionEntry) => entry.summary?.trim() || archivedSummary(entry) || entry.item.summary?.trim();
	const summaryLabel = (entry: EditionEntry) => entry.summary?.trim() || archivedSummary(entry) ? 'AI summary' : undefined;
	function sourceCount(entry: EditionEntry) { return Math.max(0, (entry.sources ?? 1) - 1); }
	function articleRated(entry: EditionEntry, state: import('$lib/hub/types').ItemState) { entry.item.state = state; }
	function supportsArticleFeedback(entry: EditionEntry) { return ['news', 'article', 'web'].includes(entry.item.type); }
</script>



<div class="today page" data-area="hub">
	<div class="top" class:has-lead={!!lead}>
		<header class="masthead">
			<p class="date">Today · {today}</p>
			<h1>{greeting}</h1>
			<button class="btn regen" use:focusable disabled={rerunning} onclick={regenerate} title="Rebuild The Front Page from the best of The Wire, The Finds, The Dispatch, The Study and Meme Radar, then let the curator re-check it">{rerunning ? 'Regenerating…' : curating ? '↻ Regenerate (polishing…)' : '↻ Regenerate'}</button>
			{#if edition?.intro}
				<Cited text={edition.intro} itemIds={edition.intro_refs.map((r) => r.item_id)} />
			{:else if !loading}
				<p class="plain-greeting">A little of what you follow, gathered in one place.</p>
			{/if}
			{#if notice}<p class="notice" role="status">{notice}</p>{/if}
			{#if error}
				<div class="load-error" role="alert"><p>Today could not be loaded. {error}</p><button class="btn" use:focusable onclick={() => (revision++)}>Try again</button></div>
			{:else if loading && !edition}
				<p class="status" role="status">Gathering today's edition…</p>
			{/if}
		</header>
		{#if lead}
			<section class="lead-section" aria-label="Today's lead" data-nav-row>
				<p class="kicker">The lead</p>
				<StoryCard href={itemHref(lead)} title={lead.item.title} source={lead.item.publisher || lead.item.author || undefined} summary={cardSummary(lead)} summaryLabel={summaryLabel(lead)} more={sourceCount(lead)} mediaAvailable={hasLocalImage(lead)}>
					{#snippet image()}{#if hasLocalImage(lead)}<LocalImage id={lead.item_id} alt={lead.item.title} />{/if}{/snippet}
					{#snippet why()}<WhyPopover reasons={lead.item.verdict?.reasons ?? []} /><EditionActions entry={lead} onchanged={(action) => { notice = action === 'promote' ? 'Kept in your edition.' : 'Set aside.'; revision++; }} /><SeenDismiss itemIds={[lead.item_id]} title={lead.item.title} onchanged={() => { notice = 'Removed from today.'; revision++; }} />{#if supportsArticleFeedback(lead)}<ArticleFeedback itemId={lead.item_id} title={lead.item.title} rating={lead.item.state.rating} onchanged={(state) => articleRated(lead, state)} />{/if}{/snippet}
				</StoryCard>
			</section>
		{/if}
	</div>

	<Overview refresh={revision} />

	<footer class="edition-end">
		{#if edition && !session.isChild}<SetAsideLink count={edition.set_aside} href="/today/set-aside" />{/if}
		<aside class="shelf" aria-label="Shelf health"><ShelfStatus /></aside>
	</footer>
</div>

<style>
	.today { max-width: 84rem; margin-inline: auto; padding-top: clamp(1.6rem, 4vw, 3.5rem); padding-bottom: 5rem; color: var(--ink); }
	.top { padding-bottom: clamp(1.4rem, 3vw, 2.2rem); border-bottom: 1px solid var(--rule); }
	@media (min-width: 1000px) {
		.top.has-lead { display: grid; grid-template-columns: minmax(0, 5fr) minmax(0, 6fr); gap: clamp(1.5rem, 3.5vw, 3.5rem); align-items: start; }
	}
	.date { margin-bottom: .6rem; color: var(--faint); font-size: var(--fs-sm); }
	.masthead h1 { margin-bottom: 1.1rem; font: 520 clamp(2rem, 5.5vw, 4.2rem) / 1.05 var(--font-display); letter-spacing: -.025em; overflow-wrap: anywhere; }
	:global(.masthead .cited) { font-size: var(--fs-md); line-height: 1.7; }
	.plain-greeting { color: var(--ink-soft); font-size: var(--fs-md); }
	.regen { margin-bottom: 1rem; }
	.kicker { margin: 0 0 .5rem; color: var(--faint); font-size: var(--fs-sm); letter-spacing: .06em; text-transform: uppercase; }
	.lead-section { min-width: 0; margin-top: 1.4rem; }
	@media (min-width: 1000px) { .lead-section { margin-top: 0; } }
	.lead-section :global(.story-card) { border: 0; padding-top: 0; }
	.lead-section :global(.story-card .title) { font-size: clamp(1.5rem, 3vw, 2.3rem); }
	.edition-end { display: grid; gap: 1.2rem; justify-items: center; margin-top: 2.5rem; padding-top: 1.5rem; border-top: 1px solid var(--rule); }
	.shelf { width: min(100%, 26rem); }
	.status { color: var(--ink-soft); padding: 1rem 0; }
	.load-error { padding: 1.5rem; border-left: 2px solid var(--warn); background: var(--bg-2); }
	.notice { padding: .5rem 0; color: var(--ink-soft); font-size: var(--fs-sm); }
</style>
