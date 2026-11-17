<script lang="ts">
	// The Front Page overview: one card per section with what arrived in the last 24 hours.
	// Each card reads that page's own API, so access rules (kid profiles, curated visibility) stay theirs.
	import { api } from '$lib/api';
	import { hub } from '$lib/hub/api';
	import { boardsApi } from '$lib/hub/boards';
	import { crApi } from '$lib/hub/commonroom';
	import { music } from '$lib/music/api';
	import { ALL_ENTRIES } from '$lib/navigation';
	import { focusable } from '$lib/nav';
	import { session } from '$lib/stores/session.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import LocalImage from '$lib/hub/LocalImage.svelte';
	import BoardImage from '$lib/hub/BoardImage.svelte';
	import type { Counts } from '$lib/hub/types';

	// Pictures: `img` is a plain public URL; `itemId`/`boardUrl` go through the profile-aware loaders.
	type Preview = { title: string; href: string; img?: string; itemId?: number; boardUrl?: string; meta?: string };
	type Card = { key: string; href: string; count: number; more?: boolean; unit: [string, string]; items: Preview[]; thumbs?: boolean; urgent?: boolean };
	type Loader = { key: string; href: string; load: (signal: AbortSignal, counts: Counts | null) => Promise<Card | null> };

	const DAY = 86400;
	const since = () => Date.now() / 1000 - DAY;
	const fresh = (t: number | null | undefined) => !!t && t >= since();
	const nav = (href: string) => ALL_ENTRIES.find((e) => e.href === href);
	const label = (href: string) => nav(href)?.label ?? href;
	const card = (key: string, href: string, count: number, unit: [string, string], items: Preview[], extra: Partial<Card> = {}): Card | null =>
		count > 0 ? { key, href, count, unit, items: items.slice(0, extra.thumbs ? 4 : 3), ...extra } : null;

	const LOADERS: Loader[] = [
		{ key: 'wire', href: '/news', load: async (signal, c) => {
			const r = await hub.news({ mode: 'latest', days: 1, limit: 3 }, signal);
			const items = r.groups.map((g) => ({ title: g.title, href: g.items[0] ? `/item/${g.items[0].id}` : '/news', meta: g.items[0]?.publisher || undefined }));
			return card('wire', '/news', c?.news_today ?? r.new_count ?? items.length, ['new story', 'new stories'], items);
		} },
		{ key: 'finds', href: '/discover', load: async (signal, c) => {
			const r = await hub.items({ section: 'discover', limit: 3 }, signal);
			const items = r.items.filter((i) => !i.state.read_at).map((i) => ({ title: i.title, href: `/item/${i.id}`, meta: i.publisher || i.curator_names[0] }));
			return card('finds', '/discover', c?.discover_unread ?? items.length, ['unread find', 'unread finds'], items);
		} },
		{ key: 'commons', href: '/common-room', load: async (signal, c) => {
			const r = await crApi.feed({ thread: 'all', section: 'new' }, signal);
			const items = r.posts.map((p) => ({ title: p.title || p.excerpt, href: '/common-room', meta: `${p.thread_name} · ${p.community || p.platform}` }));
			return card('commons', '/common-room', c?.commonroom ?? items.length, ['new post', 'new posts'], items);
		} },
		{ key: 'memes', href: '/memes', load: async (signal) => {
			const r = await hub.memeItems({ limit: 24 }, signal);
			const recent = r.items.filter((i) => fresh(i.first_seen));
			const items = recent.map((i) => ({ title: i.meme?.spoiler || i.title, href: `/item/${i.id}`, itemId: i.local_image ? i.id : undefined }));
			return card('memes', '/memes', recent.length, ['new meme', 'new memes'], items, { more: recent.length === r.items.length && r.items.length >= 24, thumbs: items.some((i) => i.itemId) });
		} },
		{ key: 'boards', href: '/boards', load: async (signal, c) => {
			const { boards } = await boardsApi.list(signal);
			const grown = boards.filter((b) => fresh(b.last_added)).sort((a, b) => (b.last_added ?? 0) - (a.last_added ?? 0));
			const items = grown.flatMap((b) => (b.preview_urls ?? []).slice(0, 2).map((u) => ({ title: b.name, href: '/boards', boardUrl: u })));
			const shared = c?.boards_shared_new ?? 0;
			return card('boards', '/boards', grown.length + shared, ['board with new images', 'boards with new images'], items, { thumbs: items.length > 0 });
		} },
		{ key: 'screen', href: '/tv', load: async () => {
			const rows = await api.home(12);
			const seen = new Set<string>();
			const vids = rows.filter((r) => r.kind !== 'continue').flatMap((r) => r.items).map((i) => i.video)
				.filter((v): v is NonNullable<typeof v> => !!v && !v.is_short && fresh(v.published_at ? Date.parse(v.published_at) / 1000 : null))
				.filter((v) => !seen.has(v.id) && !!seen.add(v.id));
			const items = vids.map((v) => ({ title: v.title, href: `/watch/${encodeURIComponent(v.id)}`, img: api.thumb(v.id, 'mq'), meta: v.channel_name }));
			return card('screen', '/tv', vids.length, ['new video', 'new videos'], items, { thumbs: true });
		} },
		{ key: 'jukebox', href: '/music', load: async () => {
			const home = await music.home();
			const mixes = [...home.mixes, ...home.recent_mixes]
				.filter((m, i, all) => m.status === 'ready' && all.findIndex((x) => x.id === m.id) === i && fresh(Math.max(m.created_at, m.refreshed_at)));
			const items = mixes.map((m) => ({ title: `${m.emoji} ${m.name}`, href: '/music', meta: `${m.count} songs` }));
			return card('jukebox', '/music', mixes.length, ['fresh mix', 'fresh mixes'], items);
		} },
		{ key: 'stacks', href: '/library', load: async (signal) => {
			const r = await hub.items({ section: 'library', sort: 'newest', limit: 12 }, signal);
			const recent = r.items.filter((i) => fresh(i.first_seen));
			const items = recent.map((i) => ({ title: i.title, href: `/item/${i.id}`, meta: i.publisher || undefined }));
			return card('stacks', '/library', recent.length, ['new in your library', 'new in your library'], items, { more: recent.length === r.items.length && r.items.length >= 12 });
		} },
		{ key: 'almanac', href: '/knowledge', load: async () => {
			const topics = (await hub.topics('knowledge')).filter((t) => !t.muted && fresh(t.last_added));
			const items = topics.map((t) => ({ title: t.name, href: '/knowledge', meta: `${t.count} entries` }));
			return card('almanac', '/knowledge', topics.length, ['topic grew', 'topics grew'], items);
		} },
		{ key: 'dispatch', href: '/briefings', load: async () => {
			const list = (await hub.briefings()).filter((b) => fresh(b.last_generated));
			const items = list.map((b) => ({ title: b.name, href: '/briefings', meta: 'New issue' }));
			return card('dispatch', '/briefings', list.length, ['new briefing', 'new briefings'], items);
		} },
		{ key: 'study', href: '/research', load: async () => {
			const list = (await hub.curators('research')).filter((c) => fresh(c.report_at));
			const items = list.map((c) => ({ title: c.subject || c.name, href: `/research/${c.id}`, meta: 'Report updated' }));
			return card('study', '/research', list.length, ['report updated', 'reports updated'], items);
		} },
		{ key: 'scout', href: '/scout-report', load: async () => {
			const r = await hub.scoutReportPreview();
			if (!r.enabled || !r.issue || !fresh(r.issue.created_at)) return null;
			const items = r.issue.sections.findings.map((f) => ({ title: f.title, href: '/scout-report', meta: f.curator_name }));
			return card('scout', '/scout-report', r.counts?.findings ?? items.length, ['finding in the new report', 'findings in the new report'], items);
		} },
		{ key: 'alerts', href: '/alerts', load: async (_signal, c) => {
			if (!c?.alerts_new) return null;
			const r = await hub.alerts('new');
			const items = r.items.map((a) => ({ title: a.title, href: '/alerts', meta: a.severity }));
			return card('alerts', '/alerts', c.alerts_new, ['alert waiting', 'alerts waiting'], items, { urgent: true });
		} }
	];

	let cards = $state<Card[]>([]);
	let quiet = $state<string[]>([]);
	let done = $state(false);

	$effect(() => {
		const version = session.version;
		cards = []; quiet = []; done = false;
		if (!session.ready || session.needsLogin || session.choosing || !session.hubAllowed) return;
		const controller = new AbortController();
		const current = () => !controller.signal.aborted && version === session.version;
		const visible = LOADERS.filter((l) => nav(l.href));
		void hub.counts().catch(() => null).then(async (counts) => {
			const results = await Promise.all(visible.map((l) => l.load(controller.signal, counts).then((c) => ({ l, c }), () => ({ l, c: null }))));
			if (!current()) return;
			cards = results.flatMap((r) => (r.c ? [r.c] : []));
			quiet = results.filter((r) => !r.c).map((r) => label(r.l.href));
			done = true;
		});
		return () => controller.abort();
	});
</script>

{#if cards.length}
	<section class="whatsnew" aria-label="What's new">
		<header class="wn-head">
			<h2>What's new</h2>
			<p>Across The Hub in the last 24 hours.</p>
		</header>
		<div class="grid" data-nav-row>
			{#each cards as c (c.key)}
				<article class="card" class:urgent={c.urgent} aria-label={label(c.href)}>
					<a class="card-head" href={c.href} use:focusable>
						<span class="icon"><Icon name={nav(c.href)?.icon ?? 'news'} /></span>
						<span class="name">{label(c.href)}</span>
						<span class="count"><strong>{c.more ? `${c.count}+` : c.count}</strong> {c.count === 1 ? c.unit[0] : c.unit[1]}</span>
					</a>
					{#if c.thumbs}
						<div class="thumbs">
							{#each c.items as p, i (i)}
								<a href={p.href} use:focusable aria-label={p.title} title={p.title}>{#if p.img}<img src={p.img} alt="" loading="lazy" />{:else if p.itemId}<LocalImage id={p.itemId} alt="" />{:else if p.boardUrl}<BoardImage url={p.boardUrl} alt="" fit />{:else}<span class="ph">{p.title}</span>{/if}</a>
							{/each}
						</div>
					{:else if c.items.length}
						<ul>
							{#each c.items as p, i (i)}
								<li><a href={p.href} use:focusable><span class="t">{p.title}</span>{#if p.meta}<span class="m">{p.meta}</span>{/if}</a></li>
							{/each}
						</ul>
					{/if}
					<a class="open" href={c.href} use:focusable tabindex="-1">Open {label(c.href)} →</a>
				</article>
			{/each}
		</div>
		{#if quiet.length}<p class="quiet">Nothing new in {quiet.join(', ')}.</p>{/if}
	</section>
{:else if done}
	<section class="whatsnew" aria-label="What's new"><p class="quiet">Nothing new across The Hub in the last 24 hours.</p></section>
{/if}

<style>
	.whatsnew { margin-bottom: clamp(1.8rem, 4vw, 3rem); }
	.wn-head { display: flex; align-items: baseline; gap: .8rem; flex-wrap: wrap; margin-bottom: 1rem; }
	.wn-head h2 { margin: 0; font: 520 var(--fs-lg, 1.35rem) / 1.2 var(--font-display); }
	.wn-head p { margin: 0; color: var(--faint); font-size: var(--fs-sm); }
	.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 17rem), 1fr)); gap: clamp(.7rem, 1.6vw, 1.1rem); }
	.card { display: flex; flex-direction: column; gap: .7rem; min-width: 0; padding: 1rem 1.05rem .85rem; border: 1px solid var(--rule); border-radius: var(--r-lg); background: var(--surface); }
	.card.urgent { border-color: color-mix(in srgb, var(--bad) 55%, var(--rule)); }
	.card-head { display: grid; grid-template-columns: auto 1fr; grid-template-rows: auto auto; column-gap: .65rem; align-items: center; color: var(--ink); text-decoration: none; }
	.icon { grid-row: span 2; display: grid; place-items: center; width: 2.3rem; height: 2.3rem; border-radius: var(--r-md); background: var(--surface-2); color: var(--accent); }
	.name { font: 560 var(--fs-md, 1rem) / 1.25 var(--font-display); }
	.card-head:hover .name, .card-head:focus-visible .name { color: var(--accent); }
	.count { color: var(--muted); font-size: var(--fs-sm); }
	.count strong { color: var(--ink); font-variant-numeric: tabular-nums; }
	.urgent .count strong { color: var(--bad); }
	ul { list-style: none; margin: 0; padding: 0; display: grid; gap: .45rem; }
	li a { display: grid; gap: .05rem; min-width: 0; color: var(--ink); text-decoration: none; }
	.t { overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; font-size: var(--fs-sm); line-height: 1.35; overflow-wrap: anywhere; }
	.m { overflow: hidden; color: var(--faint); font-size: .75rem; text-overflow: ellipsis; white-space: nowrap; }
	li a:hover .t, li a:focus-visible .t { color: var(--accent); }
	.thumbs { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .4rem; }
	.thumbs a { display: block; aspect-ratio: 1; border-radius: var(--r-sm); overflow: hidden; background: var(--bg-2); }
	.thumbs a :global(img) { width: 100%; height: 100%; object-fit: cover; display: block; }
	.ph { display: block; padding: .3rem; font-size: .65rem; color: var(--faint); overflow: hidden; }
	.open { margin-top: auto; color: var(--accent); font-size: var(--fs-sm); font-weight: 600; text-decoration: none; }
	.open:hover { text-decoration: underline; }
	.quiet { margin: .9rem 0 0; color: var(--faint); font-size: var(--fs-sm); }
</style>
