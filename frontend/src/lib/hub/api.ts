// WatchCraft Hub API client (/api/hub). Contract: docs/HUB.md.
import { request } from '$lib/api';
import type {
	Alert, Briefing, Collection, Counts, Curator, CuratorDetail, CuratorInput, Issue, Item, ItemDetail, ItemState,
	NewsGroup, Page, Registry, SavedSearch, SearchResult, ScoutPlan, ScoutReport, ScoutReportConfig, ScoutRun, Source, SourceTest, PageRevision, SitemapChanges, WatcherConfig, WatcherState, WatcherSave, RepairResult, Team, Template, Topic, TopicDetail, WebResult,
	MemeExplain, MemeFormat, MemeTrend, TodayEdition, Verdict, HubLesson
} from './types';

export type ShelfInterest = { id: number; name: string; ready: number; target: number; fresh_24h: number; publishers: number; needs_refill: boolean; last_success: number | null; next_run: number };
export type ShelfCategory = { id: number; name: string; emoji: string; kind: string; ready: number; target: number; fresh_7d: number; creators: number; pending: number; needs_refill: boolean };
export type ShelfStatus = {
	as_of: number;
	articles: { ready: number; target: 100; fresh_24h: number; publishers: number; stories: number; needs_refill: boolean; reason: string; interests: ShelfInterest[] };
	videos: { ready: number; target: number; fresh_7d: number; creators: number; needs_refill: boolean; reason: string; categories: ShelfCategory[] };
	maintenance: { last_checked_at: number | null; next_check_at: number | null; collectors_queued: number[]; status: string };
};

type Params = Record<string, string | number | boolean | undefined | null>;
export const qs = (o: Params) => {
	const s = new URLSearchParams();
	for (const [k, v] of Object.entries(o)) if (v !== undefined && v !== null && v !== '' && v !== false) s.set(k, String(v));
	const str = s.toString();
	return str ? `?${str}` : '';
};
const H = '/hub';
const get = <T>(p: string, signal?: AbortSignal) => request<T>(H + p, { signal });
const send = <T>(method: string, p: string, body?: unknown) => request<T>(H + p, { method, body: body ?? {} });

export const hub = {
	registry: () => get<Registry>('/registry'),
	shelf: (signal?: AbortSignal) => request<ShelfStatus>(H + '/shelf', { signal, toast: false }),
	counts: () => request<Counts>(H + '/counts', { toast: false }),

	items: (params: Params, signal?: AbortSignal) => get<Page<Item>>('/items' + qs(params), signal),
	item: (id: number) => get<ItemDetail>(`/items/${id}`),
	articleShare: (id: number) => get<{ path: string | null }>(`/items/${id}/share`),
	shareArticle: (id: number) => send<{ path: string | null }>('POST', `/items/${id}/share`),
	revokeArticleShare: (id: number) => send<{ path: string | null }>('DELETE', `/items/${id}/share`),
	setState: (id: number, patch: Partial<ItemState> & { read?: boolean }) => send<ItemState>('POST', `/items/${id}/state`, patch),
	articleFeedback: (id: number, body: { rating: -1 | 0 | 1 | 2; chips?: string[]; text?: string }) =>
		request<{ state: ItemState; feedback: { rating: number; chips: string[]; text: string } }>(
			`${H}/items/${id}/feedback`, { method: 'POST', body, toast: false }),
	// The caller owns the message: a 422 here means "nothing to learn from", which the
	// meme tracker answers with a question rather than an error toast.
	curatorFeedback: (id: number, curator_id: number, verdict: 'more' | 'less', terms?: string[]) =>
		request<{ curator_id: number; verdict: string; added: string[]; tuned_up: string[]; tuned_down: string[] }>(
			`${H}/items/${id}/curator-feedback`,
			{ method: 'POST', body: { curator_id, verdict, ...(terms?.length ? { terms } : {}) }, toast: false }),
	importUrl: (url: string) => send<Item>('POST', '/items/import', { url }),
	news: (params: Params, signal?: AbortSignal) => get<{ groups: NewsGroup[]; topics: Topic[]; article_count?: number; story_count?: number; supply?: { target: number; window_days: number; available: number; shortfall: number; met: boolean; pending_review: number; as_of: number; ready_count?: number; ready_target?: number; ready_shortfall?: number; candidate_count?: number; candidate_target?: number } }>('/news' + qs(params), signal),

	captureStatus: () => get<{ counts: Record<string, number>; asset_bytes: number; asset_limit: number; enabled: boolean; asset_max_mb: number; rescue: boolean }>('/capture/status'),
	setCaptureSettings: (enabled: boolean, asset_max_mb: number, rescue: boolean) =>
		send<{ enabled: boolean; asset_max_mb: number; rescue: boolean }>('PUT', '/capture/settings', { enabled, asset_max_mb, rescue }),
	retryCaptures: () => send<{ queued: number }>('POST', '/capture/retry-failed'),
	queueCapture: (id: number) => send<{ status: string }>('POST', `/items/${id}/capture/queue`),
	captureItem: (id: number) => send<{ ok: boolean }>('POST', `/items/${id}/capture`),

	collections: () => get<Collection[]>('/collections'),
	createCollection: (body: Partial<Collection>) => send<Collection>('POST', '/collections', body),
	updateCollection: (id: number, body: Partial<Collection>) => send<Collection>('PATCH', `/collections/${id}`, body),
	deleteCollection: (id: number) => send<{ ok: boolean }>('DELETE', `/collections/${id}`),
	addToCollection: (id: number, itemId: number) => send<{ ok: boolean }>('POST', `/collections/${id}/items/${itemId}`),
	removeFromCollection: (id: number, itemId: number) => send<{ ok: boolean }>('DELETE', `/collections/${id}/items/${itemId}`),

	team: (signal?: AbortSignal) => get<Team>('/team', signal),
	reportConfig: () => get<ScoutReportConfig>('/scout-report/config'),
	updateReportConfig: (body: Partial<ScoutReportConfig>) => send<ScoutReportConfig>('PUT', '/scout-report/config', body),
	scoutReports: () => get<ScoutReport[]>('/scout-report/issues'),
	generateScoutReport: (period: 'daily' | 'weekly', curator_id: number | null) => send<ScoutReport>('POST', '/scout-report/generate', { period, curator_id }),
	scoutReportPreview: () => request<{ enabled: boolean; issue: ScoutReport | null; frequency?: string; next_run?: number | null; counts?: { findings: number; runs: number; attention: number; stories: number } }>(H + '/scout-report/preview', { toast: false }),

	scoutPlans: () => get<ScoutPlan[]>('/scout/plans'),
	scoutPlan: (query: string, interval_hours: number) => send<ScoutPlan>('POST', '/scout/plans', { query, interval_hours }),
	scoutPlanUpdate: (id: number, enabled: boolean) => send<ScoutPlan>('PATCH', `/scout/plans/${id}`, { enabled }),
	scoutPlanDelete: (id: number) => send<{ ok: boolean }>('DELETE', `/scout/plans/${id}`),
	scoutRuns: () => get<ScoutRun[]>('/scout/runs'),
	scoutRun: (query: string) => send<ScoutRun>('POST', '/scout/runs', { query }),
	scoutAccept: (id: number, index: number, curator_id: number | null, curator_name?: string) =>
		send<Source>('POST', `/scout/runs/${id}/accept`, { index, curator_id, curator_name }),
	scoutDelete: (id: number) => send<{ ok: boolean }>('DELETE', `/scout/runs/${id}`),
	sources: () => get<Source[]>('/sources'),
	createSource: (body: Partial<Source>) => send<Source>('POST', '/sources', body),
	updateSource: (id: number, body: Partial<Source>) => send<Source>('PATCH', `/sources/${id}`, body),
	deleteSource: (id: number) => send<{ ok: boolean }>('DELETE', `/sources/${id}`),
	testSource: (id: number) => send<SourceTest>('POST', `/sources/${id}/test`),
	sourceRevisions: (id: number) => get<PageRevision[]>(`/sources/${id}/revisions`),
	sourceChanges: (id: number) => get<SitemapChanges>(`/sources/${id}/changes`),
	watcher: (id: number) => get<WatcherState>(`/sources/${id}/watcher`),
	testWatcher: (id: number, config: WatcherConfig) => request<SourceTest & { config: WatcherConfig }>(`${H}/sources/${id}/watcher/test`, { method: 'POST', body: { config }, toast: false }),
	saveWatcher: (id: number, config: WatcherConfig, reason: 'edit' | 'repair' = 'edit', force = false) => request<WatcherSave>(`${H}/sources/${id}/watcher`, { method: 'PUT', body: { config, reason, force }, toast: false }),
	rollbackWatcher: (id: number, revisionId: number, force = false) => send<WatcherSave>('POST', `/sources/${id}/watcher/rollback/${revisionId}`, { force }),
	repairSource: (id: number) => send<RepairResult>('POST', `/sources/${id}/repair`),
	sourceChecks: (id: number) => get<{ id: number; checked_at: number; trigger: string; status: 'ok' | 'partial' | 'failed'; item_count: number; error: string }[]>(`/sources/${id}/checks`),

	curators: (kind?: 'watch' | 'research') => get<Curator[]>('/curators' + qs({ kind })),
	curator: (id: number) => get<CuratorDetail>(`/curators/${id}`),
	createCurator: (body: CuratorInput) => send<Curator>('POST', '/curators', body),
	updateCurator: (id: number, body: Partial<CuratorInput>) => send<Curator>('PATCH', `/curators/${id}`, body),
	deleteCurator: (id: number) => send<{ ok: boolean }>('DELETE', `/curators/${id}`),
	curatorAction: (id: number, action: 'run' | 'pause' | 'resume' | 'duplicate') => send<Curator>('POST', `/curators/${id}/${action}`),
	curatorFollowUp: (id: number) => send<Curator>('POST', `/curators/${id}/follow-up`),
	templates: () => get<Template[]>('/curators/templates'),
	draft: (goal: string, kind: 'watch' | 'research' = 'watch') => send<CuratorInput & { ai: boolean; source_suggestions: { provider: string; url: string; name: string }[] }>('POST', '/curators/draft', { goal, kind }),
	report: (id: number) => send<Curator>('POST', `/curators/${id}/report`),
	exportCurator: (id: number) => request<Record<string, unknown>>(`${H}/curators/${id}/export`),

	topics: (kind?: 'knowledge' | 'news') => get<Topic[]>('/topics' + qs({ kind })),
	topic: (id: number) => get<TopicDetail>(`/topics/${id}`),
	createTopic: (body: Partial<Topic>) => send<Topic>('POST', '/topics', body),
	updateTopic: (id: number, body: Partial<Topic>) => send<Topic>('PATCH', `/topics/${id}`, body),
	deleteTopic: (id: number) => send<{ ok: boolean }>('DELETE', `/topics/${id}`),
	linkTopic: (id: number, itemId: number) => send<{ ok: boolean }>('POST', `/topics/${id}/items/${itemId}`),
	unlinkTopic: (id: number, itemId: number) => send<{ ok: boolean }>('DELETE', `/topics/${id}/items/${itemId}`),

	briefings: () => get<Briefing[]>('/briefings'),
	createBriefing: (body: Partial<Briefing>) => send<Briefing>('POST', '/briefings', body),
	updateBriefing: (id: number, body: Partial<Briefing>) => send<Briefing>('PATCH', `/briefings/${id}`, body),
	deleteBriefing: (id: number) => send<{ ok: boolean }>('DELETE', `/briefings/${id}`),
	generateBriefing: (id: number) => send<Issue>('POST', `/briefings/${id}/generate`),
	issues: (id: number) => get<Issue[]>(`/briefings/${id}/issues`),

	/** Rank by meaning instead of words. Reports why it is unavailable when no embeddings model is set. */
	semanticSearch: (q: string, limit = 20) => get<{ available: boolean; reason: string; items: (Item & { similarity?: number })[] }>('/search/semantic' + qs({ q, limit })),
	semanticStatus: () => get<{ available: boolean; reason: string; model: string; indexed: number; items: number; compared: number; cap: number }>('/search/semantic/status'),
	alerts: (state = 'new', severity?: string, curator_id?: number) => get<Page<Alert>>('/alerts' + qs({ state, severity, curator_id })),
	alertState: (id: number, state: string, snooze_hours?: number) => send<{ ok: boolean }>('POST', `/alerts/${id}/state`, { state, snooze_hours }),

	search: (params: Params, signal?: AbortSignal) => get<SearchResult>('/search' + qs(params), signal),
	webSearch: (q: string, providers: string[]) => send<{ results: WebResult[]; errors: { provider: string; message: string }[] }>('POST', '/search/web', { q, providers }),
	savedSearches: () => get<SavedSearch[]>('/saved-searches'),
	saveSearch: (body: Partial<SavedSearch>) => send<SavedSearch>('POST', '/saved-searches', body),
	deleteSavedSearch: (id: number) => send<{ ok: boolean }>('DELETE', `/saved-searches/${id}`),

	memeItems: (params: Params, signal?: AbortSignal) => get<Page<Item>>('/memes/items' + qs(params), signal),
	memeFacets: () => get<{ communities: { key: string; count: number }[]; languages: { key: string; count: number }[]; hidden_languages: string[] }>('/memes/facets'),
	memeTrends: (params: Params) => get<{ trends: MemeTrend[] }>('/memes/trends' + qs(params)),
	memeTrendState: (id: number, body: { followed?: boolean; hidden?: boolean }) => send<{ followed: boolean; hidden: boolean }>('POST', `/memes/trends/${id}/state`, body),
	memeFormats: (params: Params) => get<{ formats: MemeFormat[]; total: number }>('/memes/formats' + qs(params)),
	memeFormat: (id: number) => get<MemeFormat & { items: Item[] }>(`/memes/formats/${id}`),
	memeSimilar: (id: number) => get<{ items: Item[] }>(`/memes/items/${id}/similar`),
	memeExplain: (id: number, refresh = false) => send<{ ai: boolean; explanation: MemeExplain }>('POST', `/memes/items/${id}/explain`, { refresh }),
	/** Analyze collected examples and recount trends now instead of on the next scheduler tick. */
	memeSweep: () => send<{ analyzed: number; transitions: number; formats: number }>('POST', '/memes/sweep'),
	hideMemeLanguage: (code: string, hidden = true) =>
		send<{ hidden_languages: string[] }>('POST', `/memes/languages/${encodeURIComponent(code)}/hide`, { hidden }),

	// Master curator: the daily edition and what it left out.
	rerunToday: () => send<{requested_at: number; completed_at: number}>('POST', '/today/rerun'),
	today: (signal?: AbortSignal, preview?: { stories: number; reads: number; memes: number }) =>
		get<TodayEdition>('/today' + (preview ? qs({ preview: true, ...preview }) : ''), signal),
	setAside: (params: Params) => get<Page<Item>>('/today/set-aside' + qs(params)),
	overrule: (id: number, action: 'promote' | 'demote' | 'clear') => send<Verdict | null>('POST', `/items/${id}/overrule`, { action }),
	why: (id: number) => get<{ verdict: Verdict | null; story: { id: number; sources: number } | null }>(`/items/${id}/why`),
	story: (id: number) => get<{ id: number; title: string; items: Item[] }>(`/stories/${id}`),
	preferences: () => get<{ summary: string; built_at: number | null; lessons: HubLesson[] }>('/curator/preferences'),
	addPreference: (body: { text: string; scope?: string; polarity?: 1 | -1 }) => send<HubLesson>('POST', '/curator/preferences', body),
	editPreference: (id: number, body: { text?: string; polarity?: 1 | -1; active?: boolean }) => send<HubLesson>('PATCH', `/curator/preferences/${id}`, body),
	deletePreference: (id: number) => send<{ ok: boolean }>('DELETE', `/curator/preferences/${id}`)
};

const span = (s: number) => s < 3600 ? `${Math.max(1, Math.floor(s / 60))}m` : s < 86400 ? `${Math.floor(s / 3600)}h` : `${Math.floor(s / 86400)}d`;
export const ago = (t: number | null | undefined) => {
	if (!t) return '';
	const s = Date.now() / 1000 - t;
	if (s < 0) return `in ${span(-s)}`;
	if (s < 60) return 'just now';
	if (s < 86400 * 30) return `${span(s)} ago`;
	return new Date(t * 1000).toLocaleDateString();
};
export const host = (url: string) => { try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return ''; } };
