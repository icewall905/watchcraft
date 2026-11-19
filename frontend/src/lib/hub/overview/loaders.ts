// The Front Page overview: one panel per Hub page with what is waiting there, its sub-categories
// and pictures. Item pages come from one server summary (/hub/today/overview); the rest read each
// page's own API, so every page keeps its own access rules.
import { api } from '$lib/api';
import { hub } from '$lib/hub/api';
import { boardsApi } from '$lib/hub/boards';
import { crApi } from '$lib/hub/commonroom';
import { music } from '$lib/music/api';
import type { HomeRow } from '$lib/types';
import type { OverviewPage, TodayOverview } from '$lib/hub/types';

/** A picture or line: `img` is a plain URL; `itemId` / `boardUrl` go through the profile-aware loaders. */
export type Pic = { title: string; href: string; img?: string; itemId?: number; boardUrl?: string; meta?: string; summary?: string;
	/** Small overlay on the picture, e.g. a video's length. */ badge?: string; video?: boolean };
export type Chip = Pic & { count?: number };
export type Panel = {
	key: string; href: string; waiting: number; today: number; unit: [string, string];
	pics: Pic[]; chips: Chip[]; lines?: Pic[]; urgent?: boolean; more?: boolean;
};
export type Loader = { key: string; href: string; load: (signal: AbortSignal, shared: Shared) => Promise<Panel | null> };
export type Shared = { overview: () => Promise<TodayOverview | null>; home: () => Promise<HomeRow[]> };

const DAY = 86400;
const WEEK = 7 * DAY;
const now = () => Date.now() / 1000;
const within = (t: number | null | undefined, span: number) => !!t && t >= now() - span;
const hasPic = (p: Pic) => !!(p.img || p.itemId || p.boardUrl);
export const pictured = (panel: Panel) => panel.pics.filter(hasPic).length;

function panel(key: string, href: string, waiting: number, unit: [string, string], extra: Partial<Panel> = {}): Panel | null {
	if (waiting <= 0) return null;
	return { key, href, waiting, today: 0, unit, pics: [], chips: [], ...extra };
}

/** Server-summarised item page → panel. */
function fromServer(key: string, href: string, page: OverviewPage | undefined, unit: [string, string], urgent = false): Panel | null {
	if (!page) return null;
	const pics = page.previews.map((p) => ({ title: p.title, href: urgent ? '/alerts' : `/item/${p.item_id}`, itemId: p.has_image ? p.item_id : undefined, meta: p.meta || undefined,
		// Memes speak for themselves; their AI descriptions only describe the picture.
		summary: key === 'memes' ? undefined : p.summary || undefined }));
	return panel(key, href, page.waiting, unit, {
		today: page.new_today, urgent, pics,
		lines: pics.filter((p) => !p.itemId).slice(0, 3),
		chips: page.groups.map((g) => ({ title: g.label, href: g.href, count: g.count, itemId: g.image_item_id ?? undefined }))
	});
}

const server = (key: keyof TodayOverview['pages'], href: string, unit: [string, string], urgent = false): Loader => ({
	key, href, load: async (_signal, shared) => fromServer(key, href, (await shared.overview())?.pages[key], unit, urgent)
});

export const duration = (s: number) => { if (!s) return ''; const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = String(Math.floor(s % 60)).padStart(2, '0'); return h ? `${h}:${String(m).padStart(2, '0')}:${x}` : `${m}:${x}`; };
const videoPics = (row: HomeRow) => row.items.map((i) => i.video).filter((v): v is NonNullable<typeof v> => !!v && !v.is_short)
	.map((v) => ({ title: v.title, href: `/watch/${encodeURIComponent(v.id)}`, img: api.thumb(v.id, 'mq'), meta: v.channel_name, at: v.published_at ? Date.parse(v.published_at) / 1000 : 0, badge: duration(v.duration_s), video: true }));
/** Plain teaser from generated prose: no markdown, citations or headings. */
export const teaser = (text: string, limit = 260) => {
	const t = text.replace(/\s*\[\d+(?:\s*,\s*\d+)*\]/g, '').replace(/^\s*(?:#+|[-•*]|\d+\.)\s+/gm, '').replace(/[*_`>#]+/g, '').replace(/\s+/g, ' ').trim();
	return t.length <= limit ? t : `${t.slice(0, limit).replace(/\s+\S*$/, '')}…`;
};

export const LOADERS: Loader[] = [
	server('alerts', '/alerts', ['alert waiting', 'alerts waiting'], true),
	server('wire', '/news', ['story waiting', 'stories waiting']),
	server('finds', '/discover', ['unread find', 'unread finds']),
	server('memes', '/memes', ['new meme', 'new memes']),
	{ key: 'screen', href: '/tv', load: async (_signal, shared) => {
		const rows = (await shared.home()).filter((r) => r.kind === 'category');
		const seen = new Set<string>();
		const all = rows.flatMap(videoPics).filter((v) => !seen.has(v.href) && !!seen.add(v.href));
		return panel('screen', '/tv', all.length, ['video lined up', 'videos lined up'], {
			today: all.filter((v) => within(v.at, DAY)).length,
			pics: [...all].sort((a, b) => b.at - a.at).slice(0, 6),
			chips: rows.filter((r) => r.items.length).map((r) => ({ title: `${r.emoji} ${r.title}`.trim(), href: r.category_id ? `/category/${r.category_id}` : '/tv', count: r.items.length, img: videoPics(r)[0]?.img }))
		});
	} },
	{ key: 'subscriptions', href: '/subscriptions', load: async (_signal, shared) => {
		const vids = (await shared.home()).filter((r) => r.kind === 'subscriptions').flatMap(videoPics).filter((v) => within(v.at, WEEK));
		const byChannel = new Map<string, { n: number; pic: (typeof vids)[number] }>();
		for (const v of vids) { const c = byChannel.get(v.meta ?? ''); if (c) c.n++; else byChannel.set(v.meta ?? '', { n: 1, pic: v }); }
		return panel('subscriptions', '/subscriptions', vids.length, ['new upload this week', 'new uploads this week'], {
			today: vids.filter((v) => within(v.at, DAY)).length, pics: vids.slice(0, 6),
			chips: [...byChannel].filter(([name]) => name).sort((a, b) => b[1].n - a[1].n).slice(0, 6).map(([name, c]) => ({ title: name, href: '/subscriptions', count: c.n, img: c.pic.img }))
		});
	} },
	{ key: 'scroll', href: '/shorts', load: async () => {
		const feeds = await api.shortsFeeds();
		const ready = feeds.filter((f) => f.ready_count > 0).sort((a, b) => b.ready_count - a.ready_count);
		// Shorts are read per feed; preview the fullest one.
		const first = ready[0] ? await api.shorts({ limit: 6, category_id: ready[0].id }).catch(() => null) : null;
		return panel('scroll', '/shorts', ready.reduce((n, f) => n + f.ready_count, 0), ['short ready', 'shorts ready'], {
			pics: (first?.items ?? []).map((c) => ({ title: c.video.title, href: '/shorts', img: api.thumb(c.video.id, 'mq'), meta: c.video.channel_name })),
			chips: ready.slice(0, 10).map((f) => ({ title: `${f.emoji ?? ''} ${f.name}`.trim(), href: '/shorts', count: f.ready_count }))
		});
	} },
	{ key: 'commons', href: '/common-room', load: async (signal) => {
		// New posts since the last Commons visit first, then the best of the rest, so Commons always has something to show.
		const [{ threads }, fresh, best] = await Promise.all([crApi.threads(signal), crApi.feed({ thread: 'all', section: 'new' }, signal).catch(() => null),
			crApi.feed({ thread: 'all', section: 'deep' }, signal).catch(() => null)]);
		const on = threads.filter((t) => t.enabled);
		const seen = new Set<number>();
		const posts = [...(fresh?.posts ?? []), ...(best?.posts ?? [])].filter((p) => !p.nsfw && !p.spoiler && !seen.has(p.post_id) && !!seen.add(p.post_id));
		const media = (p: (typeof posts)[number]) => { const m = p.media.find((x) => x.thumb || x.poster || x.kind === 'image'); return m ? m.thumb || m.poster || m.url : undefined; };
		const fresh_n = on.reduce((n, t) => n + (t.new_count || 0), 0);
		const talk = (p: (typeof posts)[number]) => [p.score ? `▲ ${p.score}` : '', p.comments ? `${p.comments} comments` : ''].filter(Boolean).join(' · ');
		const pics = posts.slice(0, 12).map((p) => ({ title: p.title || p.excerpt, href: `/common-room?post=${p.post_id}`, img: media(p),
			meta: [p.thread_name, p.community || p.platform].filter(Boolean).join(' · '), summary: p.title ? p.excerpt : undefined, badge: talk(p) || undefined }));
		// Pictured posts lead; the rest still show as text cards.
		pics.sort((x, y) => Number(!!y.img) - Number(!!x.img));
		return panel('commons', '/common-room', fresh_n || posts.length, fresh_n ? ['new post', 'new posts'] : ['post worth reading', 'posts worth reading'], {
			today: posts.filter((p) => within(p.published_at, DAY)).length, pics: pics.slice(0, 6),
			chips: on.sort((x, y) => (y.new_count || 0) - (x.new_count || 0)).map((t) => ({ title: t.name, href: '/common-room', count: t.new_count || t.pick_count }))
		});
	} },
	{ key: 'boards', href: '/boards', load: async (signal) => {
		const { boards } = await boardsApi.list(signal);
		const grown = boards.filter((b) => within(b.last_added, WEEK)).sort((a, b) => (b.last_added ?? 0) - (a.last_added ?? 0));
		return panel('boards', '/boards', grown.length, ['board grew this week', 'boards grew this week'], {
			today: grown.filter((b) => within(b.last_added, DAY)).length,
			pics: grown.flatMap((b) => (b.preview_urls ?? []).slice(0, 3).map((u) => ({ title: b.name, href: `/boards?board=${b.id}`, boardUrl: u }))).slice(0, 6),
			chips: grown.slice(0, 6).map((b) => ({ title: b.name, href: `/boards?board=${b.id}`, boardUrl: b.cover_url || b.preview_urls?.[0] || undefined, count: b.saved_count }))
		});
	} },
	{ key: 'jukebox', href: '/music', load: async () => {
		const home = await music.home();
		const mixes = [...home.mixes, ...home.recent_mixes].filter((m, i, all) => m.status === 'ready' && all.findIndex((x) => x.id === m.id) === i);
		const fresh = mixes.filter((m) => within(Math.max(m.created_at, m.refreshed_at), DAY));
		return panel('jukebox', '/music', mixes.length, ['mix ready', 'mixes ready'], {
			today: fresh.length,
			pics: mixes.filter((m) => m.covers[0]).slice(0, 6).map((m) => ({ title: `${m.emoji} ${m.name}`, href: '/music', img: m.covers[0], meta: `${m.count} songs`, summary: m.blurb || m.description || undefined })),
			chips: mixes.slice(0, 6).map((m) => ({ title: `${m.emoji} ${m.name}`, href: '/music', count: m.count, img: m.covers[0] }))
		});
	} },
	server('stacks', '/library', ['unread in your library', 'unread in your library']),
	server('almanac', '/knowledge', ['entry added this week', 'entries added this week']),
	{ key: 'dispatch', href: '/briefings', load: async () => {
		const list = (await hub.briefings()).filter((b) => within(b.last_generated, 2 * DAY));
		const latest = await Promise.all(list.map((b) => hub.issues(b.id).then((i) => i[0], () => undefined)));
		return panel('dispatch', '/briefings', list.length, ['new briefing', 'new briefings'], {
			today: list.filter((b) => within(b.last_generated, DAY)).length,
			lines: list.map((b, i) => ({ title: b.name, href: '/briefings', meta: latest[i]?.ai ? 'AI-written issue' : 'New issue', summary: latest[i] ? teaser(latest[i]!.content, 420) : undefined }))
		});
	} },
	{ key: 'study', href: '/research', load: async () => {
		const list = (await hub.curators('research')).filter((c) => within(c.report_at, WEEK));
		return panel('study', '/research', list.length, ['report updated', 'reports updated'], {
			today: list.filter((c) => within(c.report_at, DAY)).length,
			lines: list.map((c) => ({ title: c.subject || c.name, href: `/research/${c.id}`, meta: 'Report updated' }))
		});
	} },
	{ key: 'scout', href: '/scout-report', load: async () => {
		const r = await hub.scoutReportPreview();
		if (!r.enabled || !r.issue || !within(r.issue.created_at, WEEK)) return null;
		const lines = r.issue.sections.findings.map((f) => ({ title: f.title, href: '/scout-report', meta: f.curator_name }));
		return panel('scout', '/scout-report', r.counts?.findings ?? lines.length, ['finding in the new report', 'findings in the new report'], { today: within(r.issue.created_at, DAY) ? lines.length : 0, lines });
	} }
];

/** Order: alerts, then pages with something new today, then by size of the backlog. */
export function rank(panels: Panel[]): Panel[] {
	const score = (p: Panel) => (p.urgent ? 1e9 : 0) + (p.today > 0 ? 1e6 : 0) + Math.min(p.today, 999) * 1e3 + Math.min(p.waiting, 999);
	return [...panels].sort((a, b) => score(b) - score(a));
}
