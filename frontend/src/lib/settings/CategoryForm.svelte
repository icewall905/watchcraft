<script lang="ts">
	import { api } from '$lib/api';
	import { session } from '$lib/stores/session.svelte';
	import { toast } from '$lib/stores/toast.svelte';
	import { focusable } from '$lib/nav';
	import type { Category, CategoryInput } from '$lib/types';
	import TextField from '$lib/components/form/TextField.svelte';
	import ChipInput from '$lib/components/form/ChipInput.svelte';
	import Toggle from '$lib/components/form/Toggle.svelte';
	import Slider from '$lib/components/form/Slider.svelte';
	import Stepper from '$lib/components/form/Stepper.svelte';
	import Select from '$lib/components/form/Select.svelte';
	import EmojiPicker from '$lib/components/EmojiPicker.svelte';
	import ColorPicker from '$lib/components/ColorPicker.svelte';

	let { category = null, preset = null, onDone }: { category?: Category | null; preset?: Partial<CategoryInput> | null; onDone: (saved?: Category) => void } = $props();

	const DEFAULTS: CategoryInput = {
		name: '',
		emoji: '✨',
		color: '#7c5cff',
		brief: '',
		seed_queries: [],
		seed_channels: [],
		include_keywords: [],
		exclude_keywords: [],
		min_duration_s: 120,
		max_duration_s: 1800,
		language: '',
		max_age_days: 0,
		target_queue: 20,
		threshold: 65,
		priority: 2,
		kid_safe: false,
		enabled: true
	};
	// svelte-ignore state_referenced_locally
	let f = $state<CategoryInput>({ ...DEFAULTS, ...(preset ?? {}), ...(category ? pick(category) : {}) });
	let busy = $state(false);
	let autofilling = $state<string | null>(null);
	type AutofillField = 'seed_queries' | 'seed_channels' | 'include_keywords' | 'exclude_keywords';

	async function autofill(field: AutofillField) {
		if (autofilling) return;
		if (!f.brief.trim()) return toast.warn('Write a brief for the AI first.');
		autofilling = field;
		try {
			const result = await api.autofillCategory(f.brief, field);
			const existing = new Set(f[field].map((value) => value.toLowerCase()));
			const additions = result.values.filter((value) => { const key = value.toLowerCase(); if (existing.has(key)) return false; existing.add(key); return true; });
			f[field] = [...f[field], ...additions];
			if (!result.values.length) toast.warn('The AI found no suggestions for this field.');
		} catch {
			// The API client displays the error.
		} finally {
			autofilling = null;
		}
	}
	let playlistUrl = $state('');
	let confirmDel = $state(false);
	let minMin = $state(Math.round(f.min_duration_s / 60));
	let maxMin = $state(Math.round(f.max_duration_s / 60));

	function pick(c: Category): CategoryInput {
		const { id: _i, profile_id: _p, position: _po, created_at: _c, last_worked_at: _l, stats: _s, ...rest } = c;
		return rest;
	}

	async function save() {
		if (!f.name.trim()) return toast.warn('Give the playlist a name.');
		if (!f.brief.trim() && (category || !playlistUrl.trim())) return toast.warn('Describe what you want in the brief. The AI uses it for every decision.');
		busy = true;
		const body = { ...$state.snapshot(f), name: f.name.trim(), min_duration_s: minMin * 60, max_duration_s: Math.max(minMin, maxMin) * 60 };
		try {
			const saved = category ? await api.updateCategory(category.id, body) : await api.createCategory({ ...body, playlist_url: playlistUrl.trim() || undefined });
			await session.loadCategories();
			toast.success(category ? `Saved ${saved.name}` : `Created ${saved.name}. The AI will start looking soon.`);
			onDone(saved);
		} finally {
			busy = false;
		}
	}

	async function remove() {
		if (!category) return;
		if (!confirmDel) return (confirmDel = true);
		await api.deleteCategory(category.id);
		await session.loadCategories();
		toast.success(`Deleted ${category.name}`);
		onDone();
	}

	const LANGS = [
		{ value: '', label: 'Any language' },
		{ value: 'en', label: 'English' },
		{ value: 'da', label: 'Danish' },
		{ value: 'de', label: 'German' },
		{ value: 'sv', label: 'Swedish' },
		{ value: 'no', label: 'Norwegian' },
		{ value: 'fr', label: 'French' },
		{ value: 'es', label: 'Spanish' },
		{ value: 'nl', label: 'Dutch' }
	];
	const BRIEF_PLACEHOLDER = `Describe it like you would to a friend who picks videos for you. For example:

Simple, fun science for kids 5–9: Mark Rober, cat videos, experiments, space, animals. Short and upbeat, explained without jargon. Nothing scary or mean, no pranks, no clickbait faces, no shouting.`;
</script>

<div class="form">
	<div class="top">
		<div class="badge" style:--c={f.color}>{f.emoji}</div>
		<div class="grow"><TextField label="Name" bind:value={f.name} placeholder="e.g. Science for kids" autofocus id="cat-name" /></div>
	</div>

    {#if !category}
        <TextField label="Initial playlist URL (optional)" bind:value={playlistUrl} placeholder="https://www.youtube.com/playlist?list=PL…" hint="Import up to 200 starting videos and let AI generate the prompt from all of them. Imported videos skip judging; future replacements use your Keep ready setting. Public playlists work without cookies; private playlists need cookies in Settings → Cookies." id="cat-playlist" />
    {/if}
	<TextField multiline rows={7} label="Brief for the AI" hint={playlistUrl.trim() && !category ? "Optional extra preferences to add to the generated prompt." : "The most important field. Say what you want, what to avoid, and who is watching."} bind:value={f.brief} placeholder={BRIEF_PLACEHOLDER} id="cat-brief" />

	<div class="two">
		<div class="autofill-field">
			<ChipInput label="Seed searches" hint="Specific YouTube search phrases. Press Enter after each phrase; commas stay within a phrase." splitCommas={false} bind:values={f.seed_queries} placeholder="egg drop experiment" />
			<button class="btn autofill" disabled={!!autofilling} use:focusable={{ group: 'cat-autofill', onSelect: () => autofill('seed_queries') }} aria-label="Autofill Seed searches with AI">{autofilling === 'seed_queries' ? 'Generating…' : '✨ Autofill with AI'}</button>
		</div>
		<div class="autofill-field">
			<ChipInput label="Seed channels" hint="@handles, channel IDs or URLs the AI should mine." bind:values={f.seed_channels} placeholder="@MarkRober" />
			<button class="btn autofill" disabled={!!autofilling} use:focusable={{ group: 'cat-autofill', onSelect: () => autofill('seed_channels') }} aria-label="Autofill Seed channels with AI">{autofilling === 'seed_channels' ? 'Generating…' : '✨ Autofill with AI'}</button>
		</div>
		<div class="autofill-field">
			<ChipInput label="Must mention" hint="Optional words that should appear (title, description or tags)." bind:values={f.include_keywords} placeholder="experiment" />
			<button class="btn autofill" disabled={!!autofilling} use:focusable={{ group: 'cat-autofill', onSelect: () => autofill('include_keywords') }} aria-label="Autofill Must mention with AI">{autofilling === 'include_keywords' ? 'Generating…' : '✨ Autofill with AI'}</button>
		</div>
		<div class="autofill-field">
			<ChipInput label="Never show" hint="Videos with these words in the title are skipped without asking the AI." bind:values={f.exclude_keywords} placeholder="prank" />
			<button class="btn autofill" disabled={!!autofilling} use:focusable={{ group: 'cat-autofill', onSelect: () => autofill('exclude_keywords') }} aria-label="Autofill Never show with AI">{autofilling === 'exclude_keywords' ? 'Generating…' : '✨ Autofill with AI'}</button>
		</div>
	</div>

	<div class="one">
		<Stepper label="Shortest video" bind:value={minMin} min={0} max={180} step={1} format={(v) => (v ? `${v} min` : 'any')} />
		<Stepper label="Longest video" bind:value={maxMin} min={1} max={600} step={5} format={(v) => `${v} min`} />
		<Stepper label="Max age" hint="Only videos newer than this." bind:value={f.max_age_days} min={0} max={3650} step={30} format={(v) => (v ? `${v} days` : 'any age')} />
		<Select label="Language" bind:value={f.language} options={LANGS} />
		<Stepper label="Keep ready" hint="The AI stops searching when this many unwatched picks are waiting." bind:value={f.target_queue} min={3} max={200} step={10} format={(v) => `${v} videos`} />
		<Stepper label="Priority" hint="Higher gets more of the AI's time." bind:value={f.priority} min={1} max={5} step={1} format={(v) => '●'.repeat(v) + '○'.repeat(5 - v)} />
	</div>

	<Slider label="How picky" hint="Minimum AI score (0–100) to accept a video. Higher means fewer but better picks." bind:value={f.threshold} min={30} max={95} step={1} format={(v) => `${v}+`} />

	<div class="two">
		<Toggle label="Kid-safe" hint="Reject anything the AI considers unsuitable for children, whatever the score." bind:checked={f.kid_safe} />
		<Toggle label="Enabled" hint="Disabled playlists are hidden from Home and the AI ignores them." bind:checked={f.enabled} />
	</div>

	<div class="field"><span class="label">Emoji</span><EmojiPicker bind:value={f.emoji} group="cat-emoji" /></div>
	<div class="field"><span class="label">Colour</span><ColorPicker bind:value={f.color} group="cat-color" /></div>

	<div class="actions">
		{#if category}
			<button class="btn danger" use:focusable={{ group: 'cat-form-acts', onSelect: remove }}>{confirmDel ? 'Press again: deletes all its picks' : 'Delete playlist'}</button>
		{/if}
		<span class="spacer"></span>
		<button class="btn" use:focusable={{ group: 'cat-form-acts', onSelect: () => onDone() }}>Cancel</button>
		<button class="btn primary big" disabled={busy} use:focusable={{ group: 'cat-form-acts', id: 'cat-save', onSelect: save }}>{busy ? (playlistUrl.trim() && !category ? 'Importing & generating prompt…' : 'Saving…') : category ? 'Save playlist' : 'Create playlist'}</button>
	</div>
</div>

<style>
	.form {
		display: grid;
		gap: 1.1rem;
	}
	.top {
		display: flex;
		gap: 1rem;
		align-items: end;
	}
	.grow {
		flex: 1;
	}
	.badge {
		display: grid;
		place-items: center;
		width: 3.6rem;
		height: 3.6rem;
		flex: none;
		border-radius: var(--r-md);
		font-size: 2rem;
		background: color-mix(in srgb, var(--c) 25%, transparent);
		border: 1px solid color-mix(in srgb, var(--c) 60%, transparent);
	}
	.two {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(min(100%, 22rem), 1fr));
		gap: 0.9rem;
	}
	.autofill-field { display: grid; gap: 0.4rem; align-content: start; }
	.autofill { justify-self: start; }
	/* Steppers use Left/Right for their value: one column keeps every control reachable with Up/Down. */
	.one {
		display: grid;
		gap: 0.75rem;
	}
	.actions {
		display: flex;
		gap: 0.7rem;
		align-items: center;
		flex-wrap: wrap;
		padding-top: 0.5rem;
		border-top: 1px solid var(--line);
	}
</style>
