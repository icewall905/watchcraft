<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { api } from '$lib/api';
	import { session } from '$lib/stores/session.svelte';
	import { toast } from '$lib/stores/toast.svelte';
	import { focusable } from '$lib/nav';
	import { timeAgo, isVideo } from '$lib/util';
	import type { Feedback, Lesson } from '$lib/types';
	import Section from './Section.svelte';
	import TextField from '$lib/components/form/TextField.svelte';
	import Select from '$lib/components/form/Select.svelte';
	import Toggle from '$lib/components/form/Toggle.svelte';
	import Slider from '$lib/components/form/Slider.svelte';
	import SaveBar from './SaveBar.svelte';
	import { SettingsDraft } from './useSettings.svelte';
	import ContextOverview from '$lib/components/ContextOverview.svelte';
	import type { MemoryStatus } from '$lib/types';
	import { agent } from '$lib/stores/agent.svelte';
	const config = new SettingsDraft('memory');
	let scope = $state<'video' | 'article'>('video');
	let memory = $state<MemoryStatus | null>(null);
	let running = $state(false);
	let dataRequest = 0;
	let memoryRequest = 0;
	let requestedIdentity = '';
	const identity = () => `${scope}:${session.profileId}:${session.version}:${session.profile?.pin_locked}:${session.needsLogin}:${session.choosing}`;
	const canRead = () => session.ready && !!session.profile && !session.profile.pin_locked && !session.needsLogin && !session.choosing;
	const current = (token: number, expected: string) => token === dataRequest && expected === identity() && canRead();
	async function loadMemory() {
		const token = ++memoryRequest, expected = identity();
		if (!canRead()) { memory = null; return; }
		try {
			const result = await api.memoryStatus(scope);
			if (token === memoryRequest && expected === identity() && canRead()) memory = result;
		} catch { /* keep the last snapshot for the same scope and profile */ }
	}
	async function runNow() {
		const expected = identity();
		running = true;
		try {
			const result = await api.runMemory();
			if (expected !== identity() || !canRead()) return;
			toast.success(result.queued ? 'Memory consolidation queued for all profiles' : 'Memory consolidation is already running');
			await loadMemory();
		} finally { if (expected === identity()) running = false; }
	}

	let lessons = $state<Lesson[] | null>(null);
	let feedback = $state<Feedback[] | null>(null);
	let draft = $state('');
	let draftCat = $state<number>(0);
	let editing = $state<number | null>(null);
	let editText = $state('');

	async function load() {
		const token = ++dataRequest, expected = identity();
		if (!canRead()) { lessons = null; feedback = null; memory = null; return; }
		lessons = null; feedback = null;
		const [newLessons, newFeedback] = await Promise.all([
			api.lessons(scope).catch(() => []), api.feedbackList(100, scope).catch(() => [])
		]);
		if (current(token, expected)) { lessons = newLessons; feedback = newFeedback; }
	}
	onMount(() => {
		const timer = setInterval(loadMemory, 5000);
		return () => clearInterval(timer);
	});
	$effect(() => {
		const next = identity();
		if (next !== requestedIdentity) {
			requestedIdentity = next;
			dataRequest++; memoryRequest++;
			lessons = null; feedback = null; memory = null; editing = null; draft = ''; editText = '';
			untrack(() => { void load(); void loadMemory(); });
		}
	});

	const catOptions = $derived([{ value: 0, label: 'All my playlists and feeds' }, ...session.categories.map((c) => ({ value: c.id, label: `${c.emoji} ${c.name}` }))]);
	const catLabel = (id: number | null) => {
		const c = session.categoryById(id);
		return c ? `${c.emoji} ${c.name}` : scope === 'article' ? 'All articles' : 'All playlists';
	};

	async function add() {
		if (!draft.trim() || !canRead()) return;
		const expected = identity();
		await api.createLesson({ text: draft.trim(), ...(scope === 'article' ? { content_type: 'article' as const, category_id: null, channel_id: null } : { category_id: draftCat || null }) });
		if (expected !== identity() || !canRead()) return;
		draft = '';
		toast.success(scope === 'article' ? 'Rule added. The AI will use it when curating future articles.' : 'Rule added. The AI follows it from the next video on.');
		load();
	}
	async function toggle(l: Lesson) {
		const expected = identity();
		await api.updateLesson(l.id, { active: !l.active });
		if (expected !== identity() || !canRead()) return;
		l.active = !l.active;
	}
	async function saveEdit(l: Lesson) {
		const expected = identity();
		await api.updateLesson(l.id, { text: editText.trim() });
		if (expected !== identity() || !canRead()) return;
		l.text = editText.trim();
		editing = null;
		toast.success('Rule updated');
	}
	async function del(l: Lesson) {
		const expected = identity();
		await api.deleteLesson(l.id);
		if (expected !== identity() || !canRead()) return;
		lessons = (lessons ?? []).filter((x) => x.id !== l.id);
	}
	async function delFb(f: Feedback) {
		const expected = identity();
		await api.deleteFeedback(f.id);
		if (expected !== identity() || !canRead()) return;
		feedback = (feedback ?? []).filter((x) => x.id !== f.id);
	}
	const RATING: Record<number, string> = { [-1]: '👎', 0: '📝', 1: '👍', 2: '❤️' };
</script>

<Section title="Nightly memory consolidation" intro="Turn learned preferences into a compact, stable core before the next day. Original rules and previous core versions are retained.">
	<div class="stack">
		<Toggle label="Enable nightly consolidation" hint="Runs once nightly within four hours of the scheduled time. Waits while the curator is paused, disabled, or the model is unavailable. Manual runs work independently of this toggle." bind:checked={config.value.enabled} />
		<div class="row">
			<div class="grow"><TextField label="Nightly time" hint="24-hour HH:MM" bind:value={config.value.time} placeholder="03:00" group="memory-schedule" /></div>
			<div class="grow"><TextField label="Time zone" hint="Observes daylight-saving changes." bind:value={config.value.timezone} placeholder="Europe/Copenhagen" group="memory-schedule" /></div>
		</div>
		<Slider label="Core token budget per profile" hint="Default: 15,000 tokens. This is a ceiling, not a size to fill. Leave room in the model's context window for transcripts, task data and the answer. Budget uses a text estimate of four characters per token; actual request tokens are shown below." bind:value={config.value.max_core_tokens} min={10000} max={100000} step={1000} format={(v) => `≈ ${v.toLocaleString()} tokens`} />
		<SaveBar dirty={config.dirty} busy={config.busy} onsave={() => config.save('Memory schedule saved')} onreset={() => config.reset()} />
		<div class="row">
			<button class="btn primary" disabled={running || memory?.run.state === 'queued' || memory?.run.state === 'running'} use:focusable={{ group: 'memory-run', onSelect: runNow }}>Run now</button>
			<span class="small muted" aria-live="polite">{memory?.run.state === 'queued' ? 'Queued — waiting for the worker' : memory?.run.state === 'running' ? 'Consolidating memories…' : `Last run: ${memory?.run.state ?? 'not yet run'}`}{memory?.run.finished_at ? ` · ${timeAgo(memory.run.finished_at)}` : ''}</span>
		</div>
		{#if agent.control.paused}<p class="small muted">The curator is paused. Resume it to process the queued run.</p>{/if}
		{#if !session.settings?.agent.enabled}<p class="small muted">Enable the curator in AI and limits to process the queued run.</p>{/if}
		{#if memory?.run.error}<p class="small muted">{memory.run.error}</p>{/if}
		{#each memory?.run.profiles ?? [] as result}<p class="small muted">{result.profile ?? `Profile ${result.profile_id}`}: {result.status}{result.lessons !== undefined ? ` · ${result.lessons} source rules` : ''}{result.core_chars ? ` · approximately ${Math.ceil(result.core_chars / 4).toLocaleString()} core tokens` : ''}</p>{/each}
	</div>
</Section>

<div style="height:2rem"></div>
<Section title="Core identity, knowledge and rules" intro="For {session.profile?.name ?? 'this profile'}. This stable text goes first, followed by recent corrections, then task instructions and the current {scope === 'article' ? 'article' : 'video or search'}.">
	{#if memory}
		<p class="small muted">{memory.versions} saved versions · {memory.active_rules} active source rules · {memory.corrections} recent corrections or evidence reviews{memory.core ? ` · approximately ${Math.ceil(memory.core.length / 4).toLocaleString()} core tokens` : ''}{memory.core_updated_at ? ` · updated ${timeAgo(memory.core_updated_at)}` : ''}</p>
		{#if memory.core}
			<p class="small faint">This version was built with a {memory.core_budget_tokens?.toLocaleString()}-token budget. Save a different budget and use Run now to rebuild it.</p>
			{#if !memory.core_valid}<p class="small muted">A source rule changed, was disabled or deleted. This version is not being used; original active rules apply until the next successful run.</p>{/if}
			<pre class="core-memory">{memory.core}</pre>
		{:else}<p class="muted">No core yet. Use Run now to consolidate the current rules.</p>{/if}
	{:else}<p class="muted">Loading memory…</p>{/if}
</Section>

<div style="height:2rem"></div>
<Section title="AI context per request" intro="Input sizes across all profiles during the last 24 hours. The same overview is on Stats.">
	{#if memory}<ContextOverview context={memory.context} />{/if}
</Section>

<div style="height:2rem"></div>
<Section title="AI memory" intro="Rules learned from feedback, plus any you add yourself. Inferences can be refined after at least two independent opposing {scope === 'article' ? 'articles' : 'videos'} outweigh their support. Rules you add or edit stay protected. Revisions appear in Agent activity; earlier wording and evidence are retained.">
	<div class="scope-picker" role="group" aria-label="Memory content type">
		<button class="scope" class:chosen={scope === 'video'} aria-pressed={scope === 'video'} use:focusable onclick={() => (scope = 'video')}>Videos</button>
		<button class="scope" class:chosen={scope === 'article'} aria-pressed={scope === 'article'} use:focusable onclick={() => (scope = 'article')}>Articles</button>
	</div>
	<div class="add panel">
		<TextField label="Teach the AI a rule" bind:value={draft} placeholder={scope === 'article' ? 'e.g. Prefer clear reporting that explains the local impact' : 'e.g. Avoid videos where people shout at each other'} onsubmit={add} group="lesson-add" />
		<div class="row">
			{#if scope === 'video'}<div class="grow"><Select label="Applies to" bind:value={draftCat} options={catOptions} group="lesson-add2" /></div>{/if}
			<button class="btn primary" use:focusable={{ group: 'lesson-add2', onSelect: add }}>Add rule</button>
		</div>
	</div>

	{#if lessons === null}
		<div class="skeleton" style="height:8rem"></div>
	{:else if lessons.length === 0}
		<p class="muted">{scope === 'article' ? 'No article rules yet. Rate articles with 👍 or 👎 and the AI will distil that feedback into rules here.' : 'No rules yet. Rate a few videos (👍 👎 📝 in the player) and the AI will distil them into rules here.'}</p>
	{:else}
		<div class="lessons">
			{#each lessons as l (l.id)}
				<div class="lesson" class:off={!l.active}>
					{#if editing === l.id}
						<div class="grow"><TextField bind:value={editText} multiline rows={2} group="l-edit-{l.id}" /></div>
						<button class="btn mini primary" use:focusable={{ group: `l-${l.id}`, onSelect: () => saveEdit(l) }}>Save</button>
						<button class="btn mini" use:focusable={{ group: `l-${l.id}`, onSelect: () => (editing = null) }}>Cancel</button>
					{:else}
						<div class="grow">
							<p class="lt">{l.text}</p>
							<p class="small muted">{l.evidence?.kind ?? 'Unreviewed'}{l.evidence && l.evidence.kind !== 'explicit' ? ` · ${l.evidence.support} supporting / ${l.evidence.opposition} opposing ${scope === 'article' ? 'articles' : 'videos'}` : ' · changes only when you edit it'}</p>
							<p class="small faint">{catLabel(l.category_id)}{l.channel_id ? ', one channel' : ''}. Weight {l.weight.toFixed(1)}. Updated {timeAgo(l.updated_at)}.</p>
						</div>
						<button class="btn mini" use:focusable={{ group: `l-${l.id}`, onSelect: () => toggle(l) }}>{l.active ? 'Active' : 'Paused'}</button>
						<button class="btn mini" use:focusable={{ group: `l-${l.id}`, onSelect: () => ((editing = l.id), (editText = l.text)) }}>Edit</button>
						<button class="btn mini danger" aria-label="Delete rule" use:focusable={{ group: `l-${l.id}`, onSelect: () => del(l) }}>Delete</button>
					{/if}
				</div>
			{/each}
		</div>
	{/if}
</Section>

<div style="height:2rem"></div>

<Section title="Feedback history" intro="{scope === 'article' ? 'Article feedback' : 'Video feedback'} you gave the AI. Deleting feedback does not delete rules it already learned.">
	{#if feedback === null}
		<div class="skeleton" style="height:6rem"></div>
	{:else if feedback.length === 0}
		<p class="muted">No feedback yet.</p>
	{:else}
		<div class="lessons">
			{#each feedback as f (f.id)}
				<div class="lesson">
					<span class="r">{RATING[f.rating] ?? '•'}</span>
					<div class="grow">
						{#if scope === 'article' && f.item_id}<p class="lt"><a href={`/item/${f.item_id}`} use:focusable>{f.item?.title ?? 'Open archived article'}</a></p>
						{:else}<p class="lt">{isVideo(f.video) ? f.video.title : f.video_id}</p>{/if}
						<p class="small muted">
							{[...(f.chips ?? []), f.text].filter(Boolean).join('. ') || 'No comment'}
						</p>
						<p class="tiny faint">{scope === 'article' ? 'All articles' : catLabel(f.category_id)}, {timeAgo(f.created_at)}{f.processed ? ', learned' : ', waiting for the AI'}</p>
					</div>
					<button class="btn mini danger" use:focusable={{ group: `fb-${f.id}`, onSelect: () => delFb(f) }}>Delete</button>
				</div>
			{/each}
		</div>
	{/if}
</Section>

<style>
	.core-memory { white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; font-size: 0.85rem; max-height: 32rem; overflow: auto; padding: 1rem; border: 1px solid var(--line); border-radius: var(--r-md); background: rgba(0,0,0,0.15); margin-top: 0.8rem; }
	.add {
		display: grid;
		gap: 0.8rem;
	}
	.row {
		display: flex;
		gap: 0.8rem;
		align-items: center;
		flex-wrap: wrap;
	}
	.grow {
		flex: 1;
		min-width: min(100%, 14rem);
	}
	.lessons {
		display: grid;
		gap: 0.5rem;
	}
	.lesson {
		display: flex;
		gap: 0.7rem;
		align-items: center;
		padding: 0.8rem 0.9rem;
		border-radius: var(--r-md);
		background: rgba(255, 255, 255, 0.035);
		border: 1px solid var(--line);
		flex-wrap: wrap;
	}
	.lesson.off {
		opacity: 0.55;
	}
	.lt {
		font-weight: 600;
	}
	.r {
		font-size: 1.5rem;
	}
	.btn.mini {
		min-height: 2.2rem;
		padding: 0.2rem 0.85rem;
		font-size: 0.85rem;
	}
	.scope-picker { display: inline-flex; justify-self: start; gap: .2rem; padding: .25rem; border: 1px solid var(--line); border-radius: var(--r-pill); background: var(--bg-2); }
	.scope { min-height: 2.5rem; padding: .4rem 1rem; border: 0; border-radius: var(--r-pill); color: var(--muted); background: transparent; cursor: pointer; }
	.scope.chosen { color: var(--text); background: var(--surface-2); box-shadow: 0 1px 3px rgba(0,0,0,.2); }
</style>
