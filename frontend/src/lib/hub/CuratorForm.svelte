<script lang="ts">
	// Full curator editor, used by /curators/new (after the draft), the edit tab and research.
	import { onMount, untrack } from 'svelte';
	import { focusable } from '$lib/nav';
	import TextField from '$lib/components/form/TextField.svelte';
	import Toggle from '$lib/components/form/Toggle.svelte';
	import Select from '$lib/components/form/Select.svelte';
	import Slider from '$lib/components/form/Slider.svelte';
	import ChipInput from '$lib/components/form/ChipInput.svelte';
	import EmojiPicker from '$lib/components/EmojiPicker.svelte';
	import ColorPicker from '$lib/components/ColorPicker.svelte';
	import { hub } from './api';
	import ErrorBox from './ErrorBox.svelte';
	import { scheduleOptions, errMsg, emptyCurator, toneOptions } from './ui';
	import type { Collection, CuratorInput, Registry, Source, Topic } from './types';

	type Suggestion = { provider: string; url: string; name: string };
	let { initial, providerKeys = [], suggestions = [], submitLabel = 'Save curator', onsave, oncancel }: {
		initial: CuratorInput; providerKeys?: string[]; suggestions?: Suggestion[]; submitLabel?: string;
		onsave: (v: CuratorInput) => Promise<void>; oncancel?: () => void;
	} = $props();

	const pick = (o: CuratorInput): Required<CuratorInput> => {
		const base = emptyCurator(o.kind) as Required<CuratorInput>;
		const src = structuredClone($state.snapshot(o)) as Record<string, unknown>;
		for (const k of Object.keys(base)) if (src[k] !== undefined) (base as Record<string, unknown>)[k] = src[k];
		return base;
	};
	let f = $state<Required<CuratorInput>>(untrack(() => pick(initial)));
	let registry = $state<Registry | null>(null);
	let sources = $state<Source[]>([]);
	let collections = $state<Collection[]>([]);
	let topics = $state<Topic[]>([]);
	let loadError = $state('');
	let formError = $state('');
	let busy = $state(false);
	let selIds = $state<number[]>([]);
	let provSel = $state<string[]>([]);
	let addUrl = $state<Record<string, string>>({});
	let addBusy = $state('');
	let approved = $state<string[]>([]);
	let showLook = $state(false);
	let colSel = $state(0), topicSel = $state(0);

	const providers = $derived(registry?.providers ?? []);
	const prov = (k: string) => providers.find((p) => p.key === k);
	const isYt = (s: Source) => /youtube\.com\/feeds|youtube\.com\/(channel|@)/.test(s.url);

	type Preset = { url: string; label: string };
	type Group = { title: string; keys: string[]; yt?: boolean; add?: string; addHint?: string; addLabel?: string; presets?: Preset[]; note?: string };
	const GROUPS: Group[] = [
		{ title: 'Search the web', keys: ['web_search'] },
		{ title: 'YouTube channel feeds (RSS)', keys: ['rss'], yt: true, add: 'rss', addHint: 'https://www.youtube.com/feeds/videos.xml?channel_id=…' },
		{ title: 'RSS', keys: ['rss'], yt: false, add: 'rss', addHint: 'https://example.org/feed.xml' },
		{ title: 'Lemmy communities', keys: ['lemmy'], add: 'lemmy', addHint: 'https://lemmy.world/c/selfhosted', addLabel: 'Add community',
			presets: [
				{ url: 'https://lemmy.world/c/selfhosted', label: 'lemmy.world · selfhosted' },
				{ url: 'https://lemmy.world/c/technology', label: 'lemmy.world · technology' },
				{ url: 'https://lemmy.world/c/news', label: 'lemmy.world · news' },
				{ url: 'https://lemmy.ml/c/linux', label: 'lemmy.ml · linux' },
				{ url: 'https://programming.dev/c/programming', label: 'programming.dev · programming' },
				{ url: 'https://sh.itjust.works/c/asklemmy', label: 'sh.itjust.works · asklemmy' }
			] },
		{ title: 'Bing News search', keys: ['bing_news'], note: 'Searches current reporting for each of this curator’s queries.' },
		{ title: 'Bluesky', keys: ['bluesky'], add: 'bluesky', addHint: 'https://bsky.app/profile/reuters.com', addLabel: 'Add account or feed',
			note: 'Collects the articles an account or custom feed links to.',
			presets: [
				{ url: 'https://bsky.app/profile/reuters.com', label: 'Reuters' },
				{ url: 'https://bsky.app/profile/apnews.com', label: 'AP News' },
				{ url: 'https://bsky.app/profile/bbcnews.bsky.social', label: 'BBC News' },
				{ url: 'https://bsky.app/profile/theguardian.com', label: 'The Guardian' },
				{ url: 'https://bsky.app/profile/npr.org', label: 'NPR' },
				{ url: 'https://bsky.app/profile/theverge.com', label: 'The Verge' },
				{ url: 'https://bsky.app/profile/arstechnica.com', label: 'Ars Technica' },
				{ url: 'https://bsky.app/profile/did:plc:kkf4naxqmweop7dv4l2iqqf5/feed/verified-news', label: 'Verified news feed' }
			] },
		{ title: 'TikTok', keys: ['tiktok'], add: 'tiktok', addHint: 'https://www.tiktok.com/@bbcnews', addLabel: 'Add account',
			note: 'Recent clips up to 3 minutes are saved locally and show up as Moments. Use a curator that collects memes.',
			presets: [
				{ url: 'https://www.tiktok.com/@bbcnews', label: '@bbcnews' },
				{ url: 'https://www.tiktok.com/@washingtonpost', label: '@washingtonpost' },
				{ url: 'https://www.tiktok.com/@nytimes', label: '@nytimes' },
				{ url: 'https://www.tiktok.com/@reuters', label: '@reuters' },
				{ url: 'https://www.tiktok.com/@nasa', label: '@nasa' },
				{ url: 'https://www.tiktok.com/@natgeo', label: '@natgeo' }
			] },
		{ title: 'Reddit', keys: ['subreddit'], add: 'subreddit', addHint: 'https://www.reddit.com/r/worldnews', addLabel: 'Add subreddit',
			note: 'Collects the articles a subreddit links to (top of the day); the thread stays as the discussion link.',
			presets: [
				{ url: 'https://www.reddit.com/r/worldnews', label: 'r/worldnews' },
				{ url: 'https://www.reddit.com/r/news', label: 'r/news' },
				{ url: 'https://www.reddit.com/r/technology', label: 'r/technology' },
				{ url: 'https://www.reddit.com/r/science', label: 'r/science' },
				{ url: 'https://www.reddit.com/r/europe', label: 'r/europe' },
				{ url: 'https://www.reddit.com/r/Denmark', label: 'r/Denmark' }
			] },
		{ title: 'Mastodon trends', keys: ['mastodon_trends'], add: 'mastodon_trends', addHint: 'https://mastodon.social/api/v1/trends/links', addLabel: 'Add trend feed',
			presets: [
				{ url: 'https://mastodon.social/api/v1/trends/links', label: 'mastodon.social · links' },
				{ url: 'https://mastodon.social/api/v1/trends/statuses', label: 'mastodon.social · posts' },
				{ url: 'https://mastodon.social/api/v1/trends/tags', label: 'mastodon.social · tags' },
				{ url: 'https://mastodon.world/api/v1/trends/links', label: 'mastodon.world · links' },
				{ url: 'https://fosstodon.org/api/v1/trends/links', label: 'fosstodon.org · links' }
			] },
		{ title: 'Mastodon communities', keys: ['mastodon'], add: 'mastodon', addHint: 'https://mastodon.world', addLabel: 'Add instance',
			note: 'Instances must allow anonymous public timelines. mastodon.social only answers hashtag timelines, so give the curator queries.',
			presets: [
				{ url: 'https://mastodon.social', label: 'mastodon.social' },
				{ url: 'https://mastodon.world', label: 'mastodon.world' },
				{ url: 'https://fosstodon.org', label: 'fosstodon.org' },
				{ url: 'https://hachyderm.io', label: 'hachyderm.io' },
				{ url: 'https://mas.to', label: 'mas.to' },
				{ url: 'https://techhub.social', label: 'techhub.social' }
			] },
		{ title: 'GitHub', keys: ['github_releases', 'github_search'], add: 'github_releases', addHint: 'https://github.com/owner/repository' },
		{ title: 'Security advisories', keys: ['nvd', 'cisa_kev'] },
		{ title: 'Research papers', keys: ['arxiv'] },
		{ title: 'Hacker News', keys: ['hackernews'] },
		{ title: 'dev.to', keys: ['devto'] },
		{ title: 'Lobste.rs', keys: ['lobsters'], add: 'lobsters', addHint: 'https://lobste.rs/hottest.json', addLabel: 'Add list' },
		{ title: 'Wikipedia', keys: ['wikipedia'] },
		{ title: 'Domain registration', keys: ['rdap'] }
	];
	const groups = $derived(GROUPS.filter((g) => g.keys.some(prov)));

	onMount(async () => {
		try {
			[registry, sources, collections, topics] = await Promise.all([hub.registry(), hub.sources(), hub.collections(), hub.topics('knowledge')]);
			const sel: number[] = [];
			const ps: string[] = [];
			const usable = (k: string) => { const p = registry!.providers.find((x) => x.key === k); return !!p && p.available && !p.needs_url; };
			for (const id of f.source_ids ?? []) {
				const s = sources.find((x) => x.id === id);
				if (!s) continue;
				if (!registry.providers.find((p) => p.key === s.provider)?.needs_url) { if (usable(s.provider) && !ps.includes(s.provider)) ps.push(s.provider); } else sel.push(id);
			}
			for (const k of providerKeys) if (usable(k) && !ps.includes(k)) ps.push(k);
			selIds = sel; provSel = ps;
			colSel = f.collection_id ?? 0; topicSel = f.topic_id ?? 0;
		} catch (e) { loadError = errMsg(e); }
	});

	const toggleIn = <T,>(arr: T[], v: T) => (arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);
	const sourceLabel = (s: Source) => s.name || s.url;
	const rows = (g: Group) => sources.filter((s) => g.keys.includes(s.provider) && prov(s.provider)?.needs_url && (g.yt === undefined || isYt(s) === g.yt));

	async function addFeed(g: Group) {
		const url = (addUrl[g.title] ?? '').trim();
		if (!url || !g.add) return;
		addBusy = g.title; formError = '';
		try {
			const s = await hub.createSource({ provider: g.add, url });
			sources = [...sources, s]; selIds = [...selIds, s.id]; addUrl[g.title] = '';
		} catch (e) { formError = errMsg(e); } finally { addBusy = ''; }
	}
	const presetSource = (g: Group, p: Preset) => sources.find((s) => s.provider === g.add && s.url === p.url);
	async function usePreset(g: Group, p: Preset) {
		const row = presetSource(g, p);
		if (row) { selIds = toggleIn(selIds, row.id); return; }
		if (!g.add) return;
		addBusy = p.url; formError = '';
		try {
			const s = await hub.createSource({ provider: g.add, url: p.url, name: p.label });
			sources = [...sources, s]; selIds = [...selIds, s.id];
		} catch (e) { formError = errMsg(e); } finally { addBusy = ''; }
	}
	async function approve(s: Suggestion) {
		addBusy = s.url; formError = '';
		try {
			const row = sources.find((x) => x.provider === s.provider && x.url === s.url) ?? await hub.createSource({ provider: s.provider, url: s.url, name: s.name });
			if (!sources.some((x) => x.id === row.id)) sources = [...sources, row];
			if (!selIds.includes(row.id)) selIds = [...selIds, row.id];
			approved = [...approved, s.url];
		} catch (e) { formError = errMsg(e); } finally { addBusy = ''; }
	}

	async function submit(e: SubmitEvent) {
		e.preventDefault();
		formError = '';
		if (!f.name.trim()) { formError = 'Give the curator a name.'; return; }
		busy = true;
		try {
			const ids = [...selIds];
			for (const key of provSel) {
				let row = sources.find((s) => s.provider === key && !s.url);
				if (!row) { row = await hub.createSource({ provider: key }); sources = [...sources, row]; }
				if (!ids.includes(row.id)) ids.push(row.id);
			}
			await onsave({ ...pick($state.snapshot(f)), name: f.name.trim(), source_ids: ids, collection_id: colSel || null, topic_id: topicSel || null });
		} catch (e) { formError = errMsg(e); } finally { busy = false; }
	}

	const SEVERITIES = [{ value: 'low', label: 'Low and above' }, { value: 'medium', label: 'Medium and above' }, { value: 'high', label: 'High and above' }, { value: 'critical', label: 'Critical only' }];
	const sched = $derived(scheduleOptions(f.interval_hours ?? 0));
</script>

{#if loadError}
	<ErrorBox message={loadError} onretry={() => location.reload()} />
{:else if !registry}
	<div class="skeleton" style="height:22rem" aria-busy="true"></div>
{:else}
<form class="cform" onsubmit={submit}>
	<fieldset>
		<legend>Identity</legend>
		<TextField label="Name" bind:value={f.name} placeholder="Open-source AI releases" />
		<TextField label="Description" bind:value={f.description} placeholder="A short note about this curator" />
		<div class="look">
			<span class="swatch" style:--c={f.color}>{f.icon}</span>
			<button type="button" class="btn" aria-expanded={showLook} use:focusable onclick={() => (showLook = !showLook)}>Icon and colour</button>
		</div>
		{#if showLook}<div class="stack"><EmojiPicker bind:value={f.icon} group="cf-emoji" /><ColorPicker bind:value={f.color} group="cf-color" /></div>{/if}
	</fieldset>

	<fieldset>
		<legend>What to find</legend>
		<TextField multiline rows={4} label="Goal and instructions" hint="Plain language. With AI on, this decides what counts as relevant." bind:value={f.instructions} placeholder="Tell me about new open-source AI model releases, ignore hype pieces." />
		<ChipInput label="Search queries" hint="Each query is run by search providers. Press Enter or comma to add." bind:values={f.queries} group="cf-q" />
		<ChipInput label="Keywords" hint="Feeds are filtered by these. Leave empty to let nightly judging assess relevance." bind:values={f.keywords} group="cf-k" />
		{#if !f.keywords.length && f.queries.length}
			<button class="btn ghost" type="button" use:focusable onclick={() => { f.keywords = [...new Set(f.queries.flatMap(q => q.toLowerCase().split(/[^\p{L}\p{N}]+/u)).filter(w => w.length >= 3 && !['the', 'and', 'for', 'latest', 'best', 'with'].includes(w)))].slice(0, 12); }}>Suggest keywords from my queries</button>
		{/if}
		<ChipInput label="Exclusions" hint="Skip anything mentioning these." bind:values={f.exclusions} group="cf-x" />
		<div class="field">
			<span class="label">Content types <span class="faint">(none selected = any)</span></span>
			<div class="opts">
				{#each registry.types as t}
					<label class="opt"><input type="checkbox" checked={f.content_types?.includes(t.key)} onchange={() => (f.content_types = toggleIn(f.content_types ?? [], t.key))} use:focusable /> <span>{t.icon} {t.label}</span></label>
				{/each}
			</div>
		</div>
	</fieldset>

	<fieldset>
		<legend>Where to look</legend>
		<p class="muted small">Only what you tick below is visited. <a href="/sources" use:focusable>Manage sources</a></p>
		{#if suggestions.length}
			<div class="sugg">
				<strong>Suggested feeds</strong>
				{#each suggestions as s (s.url)}
					<div class="srow">
						<span><b>{s.name || s.url}</b> <span class="faint">{prov(s.provider)?.label ?? s.provider} · {s.url}</span></span>
						{#if approved.includes(s.url)}<span class="ok">Added</span>{:else}<button type="button" class="btn" disabled={addBusy === s.url} use:focusable onclick={() => approve(s)}>Approve</button>{/if}
					</div>
				{/each}
			</div>
		{/if}
		{#each groups as g (g.title)}
			<div class="grp">
				<h4>{g.title}</h4>
				{#if g.note}<p class="faint small note">{g.note}</p>{/if}
				{#if g.presets && g.add && prov(g.add)?.available !== false}
					<div class="presets">
						{#each g.presets as p (p.url)}
							{@const row = presetSource(g, p)}
							{@const on = !!row && selIds.includes(row.id)}
							<button type="button" class="chip" class:on aria-pressed={on} disabled={addBusy === p.url} use:focusable onclick={() => usePreset(g, p)}>{p.label}</button>
						{/each}
					</div>
				{/if}
				{#each g.keys.map(prov).filter((p) => !!p) as p (p!.key)}
					{#if !p!.needs_url}
						<label class="opt" class:off={!p!.available}>
							<input type="checkbox" disabled={!p!.available} checked={provSel.includes(p!.key)} onchange={() => (provSel = toggleIn(provSel, p!.key))} use:focusable />
							<span>{p!.label} <span class="faint">{p!.available ? p!.description : p!.reason}</span></span>
						</label>
						{#if !p!.available && p!.key === 'web_search'}<a class="small" href="/settings?tab=search" use:focusable>Set up web search</a>{/if}
					{/if}
				{/each}
				{#each rows(g) as s (s.id)}
					<label class="opt"><input type="checkbox" checked={selIds.includes(s.id)} onchange={() => (selIds = toggleIn(selIds, s.id))} use:focusable /> <span>{sourceLabel(s)} {#if s.name && s.url}<span class="faint">{s.url}</span>{/if}</span></label>
				{/each}
				{#if g.add && prov(g.add)?.available !== false}
					<div class="addrow">
						<input class="input" type="url" placeholder={g.addHint} aria-label={`Add ${g.title} URL`} bind:value={addUrl[g.title]} use:focusable onkeydown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addFeed(g); } }} />
						<button type="button" class="btn" disabled={addBusy === g.title || !(addUrl[g.title] ?? '').trim()} use:focusable onclick={() => addFeed(g)}>{g.addLabel ?? 'Add feed URL'}</button>
					</div>
				{/if}
			</div>
		{/each}
	</fieldset>

	<fieldset>
		<legend>Intelligence</legend>
		<Toggle label="Use AI to judge relevance" hint="Uses the configured AI model. Without it, keyword matching is used." bind:checked={f.ai_enabled} group="cf-ai" />
		<Slider label="Minimum relevance score" min={0} max={100} step={5} bind:value={f.min_score} hint="Items scoring lower are not kept." group="cf-ai" />
		<label class="field"><span class="label">Max new items per run</span><input class="input" type="number" min="1" max="1000" bind:value={f.max_per_run} use:focusable /></label>
		<ChipInput label="From 'more like this' feedback" hint="Title terms saved when you marked findings as more relevant. Edit freely." bind:values={f.tuned_up} group="cf-tune" />
		<ChipInput label="From 'less like this' feedback" hint="Title terms saved when you marked findings as less relevant. Matching findings rank lower; nothing is excluded." bind:values={f.tuned_down} group="cf-tune" />
	</fieldset>

	<fieldset>
		<legend>Schedule</legend>
		<Select label="How often" bind:value={f.interval_hours} options={sched} group="cf-sched" />
		<label class="field"><span class="label">Or every N hours (0 = manual)</span><input class="input" type="number" min="0" max="8760" bind:value={f.interval_hours} use:focusable /></label>
		<Toggle label="Enabled" hint="Paused curators never run on schedule." bind:checked={f.enabled} group="cf-sched" />
	</fieldset>

	<fieldset>
		<legend>Output</legend>
		<Select label="Narration voice" hint="How this agent describes its work in the activity timeline. Wording only; the facts stay the same." bind:value={f.tone} options={toneOptions} group="cf-out" />
		<Select label="Collection" hint="Auto-save finds into a collection." bind:value={colSel} options={[{ value: 0, label: 'None' }, ...collections.map((c) => ({ value: c.id, label: `${c.icon} ${c.name}` }))]} group="cf-out" />
		<Select label="Knowledge topic" hint="Link finds to a long-lived topic." bind:value={topicSel} options={[{ value: 0, label: 'None' }, ...topics.map((t) => ({ value: t.id, label: t.name }))]} group="cf-out" />
		<Toggle label="Raise alerts" hint="Create an alert when something matches below." bind:checked={f.alert_enabled} group="cf-out" />
		{#if f.alert_enabled}
			<ChipInput label="Alert keywords" hint="e.g. your product or library names" bind:values={f.alert_keywords} group="cf-ak" />
			<Select label="Minimum severity" bind:value={f.alert_min_severity} options={SEVERITIES} group="cf-out" />
		{/if}
	</fieldset>

	{#if formError}<div class="err" role="alert">{formError}</div>{/if}
	<div class="hstack bar">
		<button class="btn primary big" type="submit" disabled={busy} use:focusable>{busy ? 'Saving…' : submitLabel}</button>
		{#if oncancel}<button type="button" class="btn ghost" use:focusable onclick={oncancel}>Cancel</button>{/if}
	</div>
</form>
{/if}

<style>
	.cform { display: grid; gap: 1.6rem; }
	fieldset { border: 1px solid var(--line); border-radius: var(--r-lg); padding: 1.2rem 1.3rem 1.3rem; display: grid; gap: .9rem; min-width: 0; background: var(--surface); }
	legend { padding: 0 .6rem; font-weight: 700; color: var(--accent); letter-spacing: .04em; }
	.look { display: flex; align-items: center; gap: .8rem; }
	.swatch { width: 3rem; height: 3rem; display: grid; place-items: center; font-size: 1.6rem; border-radius: var(--r-md); background: color-mix(in srgb, var(--c) 35%, transparent); border: 1px solid var(--c); }
	.opts { display: flex; flex-wrap: wrap; gap: .5rem; }
	.opt { display: flex; align-items: flex-start; gap: .6rem; min-height: 2.75rem; padding: .5rem .75rem; border: 1px solid var(--line); border-radius: var(--r-md); cursor: pointer; background: rgba(255,255,255,.03); }
	.opt input { width: 1.2rem; height: 1.2rem; margin-top: .15rem; accent-color: var(--accent); flex: none; }
	.opt.off { opacity: .55; cursor: not-allowed; }
	.opt span { display: grid; gap: .1rem; min-width: 0; overflow-wrap: anywhere; }
	.grp { display: grid; gap: .5rem; }
	.grp h4 { margin: .3rem 0 0; font-size: .9rem; color: var(--muted); }
	.addrow { display: flex; gap: .5rem; flex-wrap: wrap; }
	.addrow .input { flex: 1; min-width: 12rem; }
	.note { margin: 0; }
	.presets { display: flex; flex-wrap: wrap; gap: .4rem; }
	.chip { min-height: 2.25rem; padding: .35rem .7rem; border: 1px solid var(--line); border-radius: 999px; background: rgba(255,255,255,.03); color: var(--text); font: inherit; font-size: .85rem; cursor: pointer; }
	.chip:hover:not(:disabled) { border-color: var(--accent); }
	.chip.on { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 22%, transparent); font-weight: 700; }
	.chip:disabled { opacity: .55; cursor: progress; }
	.sugg { display: grid; gap: .5rem; padding: .8rem; border-radius: var(--r-md); border: 1px dashed var(--ai); }
	.srow { display: flex; justify-content: space-between; align-items: center; gap: .8rem; flex-wrap: wrap; }
	.srow span { overflow-wrap: anywhere; }
	.ok { color: var(--ok); font-weight: 700; }
	.err { padding: .8rem 1rem; border-radius: var(--r-md); border: 1px solid rgba(255,92,122,.45); background: rgba(255,92,122,.1); color: #ffd1d9; }
	.bar { position: sticky; bottom: 0; padding: .6rem 0; }
	.faint { color: var(--faint); font-weight: 400; font-size: .82rem; }
	@media (max-width: 760px) { fieldset { padding: 1rem; } .opt { width: 100%; } }
</style>
