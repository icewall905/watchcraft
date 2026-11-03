<script lang="ts">
	import { onDestroy } from 'svelte';
	import { hub } from '$lib/hub/api';
	import type { EditionEntry, EditionSection, TodayEdition } from '$lib/hub/types';
	import { session } from '$lib/stores/session.svelte';
	import { focusable } from '$lib/nav';
	import Cited from '$lib/hub/Cited.svelte';
	import LocalImage from '$lib/hub/LocalImage.svelte';
	import SectionHeader from '$lib/hub/calm/SectionHeader.svelte';
	import StoryCard from '$lib/hub/calm/StoryCard.svelte';
	import ReadCard from '$lib/hub/calm/ReadCard.svelte';
	import MemeTile from '$lib/hub/calm/MemeTile.svelte';
	import QuietLine from '$lib/hub/calm/QuietLine.svelte';
	import SetAsideLink from '$lib/hub/calm/SetAsideLink.svelte';
	import WhyPopover from '$lib/hub/calm/WhyPopover.svelte';
	import EditionActions from '$lib/hub/calm/EditionActions.svelte';
	import EmptyCalm from '$lib/hub/calm/EmptyCalm.svelte';

	let edition = $state<TodayEdition | null>(null);
	let loading = $state(true);
	let error = $state('');
	let revision = $state(0);
	let notice = $state('');
	let controller: AbortController | null = null;
	let loadToken = 0;

	async function load() {
		const token = ++loadToken;
		controller?.abort();
		controller = new AbortController();
		const signal = controller.signal;
		const profileVersion = session.version;
		edition = null;
		loading = true;
		error = '';
		try {
			const result = await hub.today(signal);
			if (signal.aborted || token !== loadToken || profileVersion !== session.version) return;
			edition = result;
		} catch (e) {
			if (signal.aborted || token !== loadToken || profileVersion !== session.version) return;
			error = e instanceof Error ? e.message : 'Today could not be loaded.';
		} finally {
			if (!signal.aborted && token === loadToken && profileVersion === session.version) loading = false;
		}
	}

	$effect(() => { session.version; revision; void load(); });
	onDestroy(() => controller?.abort());

	const section = (key: string) => edition?.sections.find((s) => s.key === key);
	const entries = (key: string): EditionEntry[] => {
		const found = section(key);
		return found && 'entries' in found ? found.entries : [];
	};
	const research = $derived(section('research'));
	const alerts = $derived(section('alerts'));
	const hasContent = $derived(!!(entries('lead').length || entries('stories').length || entries('reads').length || entries('memes').length || (research && 'curators' in research && research.curators.length) || (alerts && 'count' in alerts && alerts.count)));
	const greeting = $derived.by(() => {
		const hour = new Date().getHours();
		const name = session.profile?.name ? `, ${session.profile.name}` : '';
		return `${hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening'}${name}`;
	});
	const today = new Intl.DateTimeFormat(undefined, { weekday: 'long', month: 'long', day: 'numeric' }).format(new Date());
	const itemHref = (entry: EditionEntry) => `/item/${entry.item_id}`;
	const hasLocalImage = (entry: EditionEntry) => entry.item.local_image;
	function sourceCount(entry: EditionEntry) { return Math.max(0, (entry.sources ?? 1) - 1); }
</script>

<svelte:head><title>Today · WatchCraft</title><meta name="description" content="A quiet, curated overview of what matters today." /></svelte:head>

<div class="today page" data-area="hub">
	<header class="masthead">
		<p class="date">{today}</p>
		<h1>{greeting}</h1>
		{#if edition?.intro}
			<Cited text={edition.intro} itemIds={edition.intro_refs.map((r) => r.item_id)} />
		{:else}
			<p class="plain-greeting">A little of what you follow, gathered in one place.</p>
		{/if}
	</header>

	{#if error}
		<div class="load-error" role="alert"><p>Today could not be loaded. {error}</p><button class="btn" use:focusable onclick={() => (revision++)}>Try again</button></div>
	{:else if loading && !edition}
		<p class="status" role="status">Gathering today's edition…</p>
	{:else if edition}
		{#if notice}<p class="notice" role="status">{notice}</p>{/if}
		{#if entries('lead').length}
			{@const lead = entries('lead')[0]}
			<section class="lead-section" aria-label="Today's lead" data-nav-row>
				<StoryCard href={itemHref(lead)} title={lead.item.title} source={lead.item.publisher || lead.item.author || undefined} summary={lead.summary || lead.item.summary} more={sourceCount(lead)} mediaAvailable={hasLocalImage(lead)}>
					{#snippet image()}{#if hasLocalImage(lead)}<LocalImage id={lead.item_id} alt={lead.item.title} />{/if}{/snippet}
					{#snippet why()}<WhyPopover reasons={lead.item.verdict?.reasons ?? []} /><EditionActions entry={lead} onchanged={(action) => { notice = action === 'promote' ? 'Kept in your edition.' : 'Set aside.'; revision++; }} />{/snippet}
				</StoryCard>
			</section>
		{/if}

		{#if entries('stories').length}
			<section class="section" aria-label="Stories">
				<SectionHeader title="Stories" action={{ href: '/news', label: 'All news' }} />
					<div class="story-list" data-nav-row>
					{#each entries('stories') as entry (entry.item_id)}
						<StoryCard href={itemHref(entry)} title={entry.item.title} source={entry.item.publisher || entry.item.author || undefined} summary={entry.summary || entry.item.summary} more={sourceCount(entry)} mediaAvailable={hasLocalImage(entry)}>
							{#snippet image()}{#if hasLocalImage(entry)}<LocalImage id={entry.item_id} alt={entry.item.title} />{/if}{/snippet}
							{#snippet why()}<WhyPopover reasons={entry.item.verdict?.reasons ?? []} /><EditionActions entry={entry} onchanged={(action) => { notice = action === 'promote' ? 'Kept in your edition.' : 'Set aside.'; revision++; }} />{/snippet}
						</StoryCard>
					{/each}
				</div>
			</section>
		{/if}

		{#if entries('reads').length}
			<section class="section">
				<SectionHeader title="Worth your time" action={{ href: '/discover', label: 'More discoveries' }} />
				<div class="reads" data-nav-row>
					{#each entries('reads') as entry (entry.item_id)}
						<ReadCard href={itemHref(entry)} title={entry.item.title} source={entry.item.publisher || entry.item.author || undefined} summary={entry.item.summary} mediaAvailable={hasLocalImage(entry)}>
							{#snippet image()}{#if hasLocalImage(entry)}<LocalImage id={entry.item_id} alt={entry.item.title} />{/if}{/snippet}
							{#snippet why()}<WhyPopover reasons={entry.item.verdict?.reasons ?? []} /><EditionActions entry={entry} onchanged={(action) => { notice = action === 'promote' ? 'Kept in your edition.' : 'Set aside.'; revision++; }} />{/snippet}
						</ReadCard>
					{/each}
				</div>
			</section>
		{/if}

		{#if entries('memes').length}
			<section class="section meme-section">
				<SectionHeader title="A lighter moment" action={{ href: '/memes', label: 'Meme Radar' }} />
				<div class="memes" data-nav-row>
					{#each entries('memes') as entry (entry.item_id)}
						<MemeTile href={itemHref(entry)} caption={entry.item.meme?.spoiler || entry.item.title} meta={entry.item.publisher || entry.item.meme?.community || undefined} mediaAvailable={hasLocalImage(entry)} sensitive={!!entry.item.meme?.sensitive} video={!!entry.item.local_video}>
							{#snippet media()}{#if hasLocalImage(entry)}<LocalImage id={entry.item_id} alt={entry.item.meme?.spoiler || entry.item.title} />{/if}{/snippet}
							{#snippet why()}<WhyPopover reasons={entry.item.verdict?.reasons ?? []} /><EditionActions entry={entry} onchanged={(action) => { notice = action === 'promote' ? 'Kept in your edition.' : 'Set aside.'; revision++; }} />{/snippet}
						</MemeTile>
					{/each}
				</div>
			</section>
		{/if}

		{#if research && 'curators' in research && research.curators.length}
			<section class="quiet-section" data-nav-row><QuietLine text={`New research on ${research.curators.map((c) => c.subject || c.name).join(', ')}`} href={`/research/${research.curators[0].id}`} linkLabel="Read the report" /></section>
		{/if}
		{#if alerts && 'count' in alerts && alerts.count > 0}
			<section class="quiet-section" data-nav-row><QuietLine text={`${alerts.count} alert${alerts.count === 1 ? '' : 's'} waiting`} href="/alerts" linkLabel="Review alerts" tone="warn" /></section>
		{/if}
		<div class="edition-end">
			<SetAsideLink count={edition.set_aside} href="/today/set-aside" />
			{#if hasContent}<EmptyCalm />{:else}<EmptyCalm title={edition.curated ? "That's everything for today" : 'Your first edition is taking shape'} detail={edition.curated ? undefined : 'As your curators finish a pass, the stories that fit will gather here.'} />{/if}
		</div>
	{/if}
</div>

<style>
	.today { max-width: 76rem; margin-inline: auto; padding-top: clamp(2rem, 5vw, 4.5rem); padding-bottom: 5rem; color: var(--ink); }
	.masthead { padding: 0 0 clamp(1.8rem, 4vw, 3rem); border-bottom: 1px solid var(--rule); margin-bottom: clamp(1.5rem, 3vw, 2.5rem); }
	.date { margin-bottom: .6rem; color: var(--faint); font-size: var(--fs-sm); }
	h1 { margin-bottom: 1.2rem; font: 520 clamp(2.4rem, 6vw, 4.5rem) / 1.02 var(--font-display); letter-spacing: -.025em; }
	:global(.masthead .cited) { font-size: var(--fs-md); line-height: 1.7; }
	.plain-greeting { color: var(--ink-soft); font-size: var(--fs-md); }
	.lead-section { padding-bottom: clamp(1.5rem, 3vw, 2.5rem); }
	.lead-section :global(.story-card) { border: 0; padding-top: 0; }
	.lead-section :global(.story-card .title) { font-size: clamp(1.7rem, 4vw, 2.8rem); }
	.section { margin-top: clamp(1.7rem, 4vw, 3rem); }
	.story-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); column-gap: clamp(1.5rem, 4vw, 4rem); }
	.reads { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: clamp(1rem, 2.5vw, 2rem); }
	.memes { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: clamp(.7rem, 1.5vw, 1.25rem); }
	.quiet-section { padding: 1rem 0; border-top: 1px solid var(--rule); }
	.edition-end { display: grid; justify-items: center; border-top: 1px solid var(--rule); margin-top: 2rem; padding-top: 1rem; }
	.edition-end :global(.empty-calm) { padding-block: 2.3rem 1rem; }
	.status { color: var(--ink-soft); padding: 2rem 0; }
	.load-error { padding: 1.5rem; border-left: 2px solid var(--warn); background: var(--bg-2); }
	.notice { padding: .5rem 0; color: var(--ink-soft); font-size: var(--fs-sm); }
	@media (max-width: 760px) { .story-list { grid-template-columns: 1fr; } .reads { grid-template-columns: repeat(2, minmax(0, 1fr)); } .memes { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
	@media (max-width: 440px) { .reads { grid-template-columns: 1fr; } .memes { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
</style>
