<script lang="ts">
	import { focusable } from '$lib/nav';
	import { session } from '$lib/stores/session.svelte';
	import { toast } from '$lib/stores/toast.svelte';
	import StatementRow from './Statement.svelte';
	import { curator, longDate, type Knowledge, type Snapshot } from './api';
	let { knowledge, onchange, onwrong, onhistory }: {
		knowledge: Knowledge; onchange: (s: Snapshot) => void; onwrong: (text: string) => void; onhistory: () => void;
	} = $props();
	const filled = $derived(knowledge.sections.filter((s) => s.statements.length));
	let weaving = $state(false);
	async function weave() {
		weaving = true;
		try { await curator.weave(); toast.success('Weaving started. Refresh in a minute to see the new version.'); }
		catch { /* the request already showed the error */ }
		finally { weaving = false; }
	}
</script>

<div class="knowledge stack">
	<div class="hstack meta" data-nav-row="knowledge-head">
		<h3 id="curator-knowledge" tabindex="-1">What I know about you</h3>
		<span class="spacer"></span>
		{#if knowledge.version}
			<span class="small muted">Version {knowledge.version.version} · woven {longDate(knowledge.version.created_at)} ·</span>
		{/if}
		<button class="btn ghost link" use:focusable onclick={onhistory} disabled={!knowledge.history.length}>History</button>
		{#if session.isAdmin}<button class="btn ghost link" disabled={weaving} use:focusable onclick={weave}>Weave now</button>{/if}
	</div>
	{#if !knowledge.version}
		<p class="empty muted">Nothing woven yet. Tonight I'll fold what you've taught me into this.</p>
	{:else if !filled.length}
		<p class="empty muted">This version is empty. Teach me something below, or tell me in the chat.</p>
	{:else}
		{#each filled as section (section.key)}
			<section class="card" aria-label={section.title}>
				<h4 tabindex="-1">{section.title}</h4>
				<ul>
					{#each section.statements as st (st.sid)}<StatementRow statement={st} {onchange} {onwrong} />{/each}
				</ul>
			</section>
		{/each}
	{/if}
</div>

<style>
	h3, h4 { margin: 0; }
	h4 { font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); }
	.meta { gap: 0.4rem; }
	.link { min-height: 2rem; padding: 0.2rem 0.6rem; font-size: 0.85rem; text-decoration: underline; }
	.card { display: grid; gap: 0.25rem; padding: 0.9rem 1rem; border: 1px solid var(--line); border-radius: var(--r-lg); background: var(--surface); }
	ul { list-style: none; margin: 0; padding: 0; }
	.empty { padding: 1rem; border: 1px dashed var(--line-strong); border-radius: var(--r-md); }
</style>
