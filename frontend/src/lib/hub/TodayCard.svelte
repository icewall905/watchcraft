<script lang="ts">
	import { hub } from './api';
	import { session } from '$lib/stores/session.svelte';
	import { focusable } from '$lib/nav';
	import type { TodayEdition } from './types';

	let edition = $state<TodayEdition | null>(null);
	$effect(() => {
		const profileVersion = session.version;
		edition = null;
		if (!session.hubAllowed) { edition = null; return; }
		const controller = new AbortController();
		void hub.today(controller.signal).then((value) => {
			if (!controller.signal.aborted && profileVersion === session.version) edition = value;
		}).catch(() => {});
		return () => controller.abort();
	});
	const sectionEntries = (key: string) => {
		const section = edition?.sections.find((s) => s.key === key);
		return section && 'entries' in section ? section.entries : [];
	};
	const lead = $derived(sectionEntries('lead')[0]);
	const count = (key: string) => sectionEntries(key).length;
</script>

{#if session.hubAllowed && edition && (edition.curated || lead || count('stories') || count('reads') || count('memes'))}
	<a class="today-card" href="/news" aria-label="Open News" use:focusable={{ group: 'today-preview', id: 'today-preview' }}>
		<span class="copy"><span class="label">A considered read for today</span>
			{#if lead}<strong>{lead.item.title}</strong>{:else}<strong>Today, at your pace</strong>{/if}
			<span class="counts">{count('stories')} stories · {count('reads')} reads · {count('memes')} lighter moments</span>
		</span>
		<span class="open">Today <span aria-hidden="true">↗</span></span>
	</a>
{/if}

<style>
	.today-card { margin: 0 var(--gutter) 1.25rem; padding: 1rem 1.2rem; display: flex; align-items: center; justify-content: space-between; gap: 1rem; border: 1px solid var(--rule); border-radius: var(--r-md); background: var(--surface); color: var(--ink); text-decoration: none; }
	.copy { display: grid; gap: .25rem; min-width: 0; }
	.label { color: var(--faint); font-size: var(--fs-xs); }
	.copy strong { overflow: hidden; font: 560 var(--fs-lg) / var(--lh-snug) var(--font-display); text-overflow: ellipsis; white-space: nowrap; }
	.counts { color: var(--ink-soft); font-size: var(--fs-sm); }
	.open { flex: none; color: var(--ink-soft); font-size: var(--fs-sm); }
	.today-card:hover .open { color: var(--accent); }
	@media (max-width: 520px) { .today-card { margin-inline: var(--gutter); padding: .85rem; } .copy strong { white-space: normal; } }
</style>
