// Typed API client. Every request carries X-Profile-Id; 401 anywhere triggers the login screen.
import { toast } from './stores/toast.svelte';
import { thumbnailPreference } from './stores/thumbnailPreference.svelte';
import type {
	AgentEvent,
	AgentStatus,
	CandidateDetail,
	Candidate,
	CaptionTrack,
	Category,
	CategoryInput,
	ChannelEntry,
	ChannelInfo,
	Feedback,
	HistoryEntry,
	HomeRow,
	Lesson,
	LlmTestResult,
	ProxyProvider,
	ProxyTestResult,
	CookieTestResult,
	ImportFeedResult,
	ImportSubscriptionsResult,
	Profile,
	SbSegment,
	Settings,
	StatsPageData,
	MemoryStatus,
	StatsExploreData,
	StatsExploreFilter,
	Video,
	VideoDetail,
	AgentControl,
	DownloadItem,
	DownloadsResponse,
	DownloaderState
} from './types';

export const PROFILE_KEY = 'bt.profile';

export class ApiError extends Error {
	constructor(
		public status: number,
		message: string,
		public body?: unknown
	) {
		super(message);
	}
}

let unauthorizedHandler: (() => void) | null = null;
let profileLockedHandler: (() => void) | null = null;
export function onProfileLocked(fn: () => void) { profileLockedHandler = fn; }
export function onUnauthorized(fn: () => void) {
	unauthorizedHandler = fn;
}

export function currentProfileId(): string | null {
	try {
		return localStorage.getItem(PROFILE_KEY);
	} catch {
		return null;
	}
}

interface ReqOpts {
	method?: string;
	body?: unknown;
	raw?: BodyInit;
	/** Show an error toast on failure (default true). */
	toast?: boolean;
	signal?: AbortSignal;
	headers?: Record<string, string>;
	/** Survive page unload (history save on exit). */
	keepalive?: boolean;
}

function detailOf(body: unknown, fallback: string): string {
	if (body && typeof body === 'object' && 'detail' in body) {
		const d = (body as { detail: unknown }).detail;
		if (typeof d === 'string') return d;
		if (Array.isArray(d)) return d.map((x) => (x && typeof x === 'object' && 'msg' in x ? String(x.msg) : String(x))).join('; ');
	}
	return fallback;
}

export async function request<T>(path: string, opts: ReqOpts = {}): Promise<T> {
	const headers: Record<string, string> = { Accept: 'application/json', ...(opts.headers ?? {}) };
	const pid = currentProfileId();
	if (pid && !headers['X-Profile-Id']) headers['X-Profile-Id'] = pid;
	let body: BodyInit | undefined = opts.raw;
	if (opts.body !== undefined) {
		headers['Content-Type'] = 'application/json';
		body = JSON.stringify(opts.body);
	}
	let res: Response;
	try {
		res = await fetch(`/api${path}`, {
			method: opts.method ?? (body ? 'POST' : 'GET'),
			headers,
			body,
			credentials: 'same-origin',
			signal: opts.signal,
			keepalive: opts.keepalive
		});
	} catch (e) {
		if ((e as Error).name === 'AbortError') throw e;
		if (opts.toast !== false) toast.error('Cannot reach the WatchCraft server. Check that it is running.');
		throw new ApiError(0, 'network error');
	}
	const ct = res.headers.get('content-type') ?? '';
	const parsed: unknown = ct.includes('application/json') ? await res.json().catch(() => null) : await res.text().catch(() => '');
	if (res.status === 401 && !path.startsWith('/auth/')) {
		unauthorizedHandler?.();
		throw new ApiError(401, 'auth required', parsed);
	}
	if (!res.ok) {
		const msg = detailOf(parsed, `${res.status} ${res.statusText}`);
		if (res.status === 403 && msg === 'profile PIN required') profileLockedHandler?.();
		if (opts.toast !== false) toast.error(msg);
		throw new ApiError(res.status, msg, parsed);
	}
	return parsed as T;
}

const get = <T>(p: string, o?: ReqOpts) => request<T>(p, o);
const post = <T>(p: string, body?: unknown, o?: ReqOpts) => request<T>(p, { ...o, method: 'POST', body: body ?? {} });
const put = <T>(p: string, body?: unknown, o?: ReqOpts) => request<T>(p, { ...o, method: 'PUT', body: body ?? {} });
const patch = <T>(p: string, body?: unknown, o?: ReqOpts) => request<T>(p, { ...o, method: 'PATCH', body: body ?? {} });
const del = <T>(p: string, o?: ReqOpts) => request<T>(p, { ...o, method: 'DELETE' });
const q = (o: Record<string, string | number | boolean | undefined | null>) => {
	const s = new URLSearchParams();
	for (const [k, v] of Object.entries(o)) if (v !== undefined && v !== null && v !== '') s.set(k, String(v));
	const str = s.toString();
	return str ? `?${str}` : '';
};

export const api = {
	// auth
	authStatus: () => get<{ required: boolean; authenticated: boolean }>('/auth/status', { toast: false }),
	login: (password: string) => post<{ ok: boolean }>('/auth/login', { password }, { toast: false }),
	logout: () => post<{ ok: boolean }>('/auth/logout'),
	health: () => get<{ ok: boolean }>('/health', { toast: false }),

	// profiles
	profiles: () => get<Profile[]>('/profiles'),
	createProfile: (p: Pick<Profile, 'name' | 'emoji' | 'color'> & { pin?: string }) => post<Profile>('/profiles', p),
	updateProfile: (id: number, p: Partial<Profile> & { pin?: string }) => patch<Profile>(`/profiles/${id}`, p),
	unlockProfile: (id: number, pin: string) => post<{ ok: boolean }>(`/profiles/${id}/unlock`, { pin }, { toast: false }),
	lockProfile: (id: number) => post<{ ok: boolean }>(`/profiles/${id}/lock`),
	deleteProfile: (id: number) => del<{ ok: boolean }>(`/profiles/${id}`),

	// categories
	categories: () => get<Category[]>('/categories'),
	autofillCategory: (brief: string, field: 'seed_queries' | 'seed_channels' | 'include_keywords' | 'exclude_keywords') => post<{ values: string[] }>('/categories/autofill', { brief, field }),
	category: (id: number) => get<Category>(`/categories/${id}`),
	createCategory: (c: Partial<CategoryInput> & { playlist_url?: string }) => post<Category>('/categories', c),
	updateCategory: (id: number, c: Partial<Category>) => patch<Category>(`/categories/${id}`, c),
	deleteCategory: (id: number) => del<{ ok: boolean }>(`/categories/${id}`),
	reorderCategories: (ids: number[]) => post<{ ok: boolean }>('/categories/reorder', ids),

	// feeds
	home: (perRow = 20) => get<HomeRow[]>(`/feed/home${q({ per_row: perRow })}`),
	categoryFeed: (id: number, status: 'accepted' | 'pending' | 'rejected' | 'all' = 'accepted', o: { offset?: number; limit?: number; include_watched?: boolean; after?: string } = {}, profileId?: number | null) =>
		get<Candidate[]>(`/feed/category/${id}${q({ status, ...o })}`, profileId ? { headers: { 'X-Profile-Id': String(profileId) } } : undefined),
	dismissCandidate: (id: number) => post<{ ok: boolean }>(`/candidates/${id}/dismiss`),
	acceptCandidate: (id: number, profileId: number) => post<{ ok: boolean; category_id: number; learn_from_override: boolean }>(
		`/candidates/${id}/accept`, {}, { headers: { 'X-Profile-Id': String(profileId) } }),
	addCandidate: (video_id: string, category_id: number) => post<{ ok: boolean }>('/candidates', { video_id, category_id }),

	// channels
	channels: () => get<ChannelEntry[]>('/channels'),
	putChannel: (id: string, body: Partial<Pick<ChannelEntry, 'name' | 'avatar' | 'handle' | 'subscribed' | 'blocked'>>) =>
		put<{ id: string; subscribed: boolean; blocked: boolean; trust: number }>(`/channels/${encodeURIComponent(id)}`, body),
	blockChannel: (id: string, name?: string) =>
		post<{ id: string; subscribed: boolean; blocked: boolean; trust: number; dismissed: number }>(
			`/channels/${encodeURIComponent(id)}/block`, name ? { name } : {}),
	channel: (ref: string) => get<{ channel: ChannelInfo; videos: Video[] }>(`/channel/${encodeURIComponent(ref)}`),
	channelAvatar: (id: string) => `/api/channel/${encodeURIComponent(id)}/avatar`,

	// video (ticket A endpoints)
	search: (query: string, n = 24, signal?: AbortSignal) => get<Video[]>(`/search${q({ q: query, n })}`, { signal }),
	video: (id: string) => get<VideoDetail>(`/video/${encodeURIComponent(id)}`, { toast: false }),
	manifestUrl: (id: string, maxHeight?: number) => `/api/video/${encodeURIComponent(id)}/manifest.mpd${q({ max_height: maxHeight })}`,
	fallbackUrl: (id: string) => `/api/video/${encodeURIComponent(id)}/fallback`,
	captions: (id: string) => get<CaptionTrack[]>(`/video/${encodeURIComponent(id)}/captions`, { toast: false }),
	sponsorblock: (id: string) => get<SbSegment[]>(`/video/${encodeURIComponent(id)}/sponsorblock`, { toast: false }),
	comments: (id: string, n = 40) => get<{ author: string; text: string; likes: number }[]>(`/video/${encodeURIComponent(id)}/comments${q({ n })}`, { toast: false }),
	thumb: (id: string, quality: 'hq' | 'mq' | 'maxres' = 'hq') => `/api/thumb/${encodeURIComponent(id)}?q=${quality}${thumbnailPreference.dearrow ? '&dearrow=1' : ''}`,

	// offline cache (Ticket D)
	downloads: async (): Promise<DownloadsResponse> => {
		// Accept a bare list or {items|downloads, totals, downloader|downloader_state}.
		const r = await get<unknown>('/downloads', { toast: false });
		const o = (Array.isArray(r) ? { items: r } : (r ?? {})) as Record<string, unknown>;
		const items = (o.items ?? o.downloads ?? []) as DownloadItem[];
		const dl = (o.downloader ?? o.downloader_state ?? o.state ?? null) as DownloaderState | null;
		const t = (o.totals ?? {}) as DownloadsResponse['totals'];
		return {
			items,
			reencode: o.reencode as DownloadsResponse['reencode'],
			downloader: dl,
			totals: {
				...t,
				used_bytes: t.used_bytes ?? dl?.used_bytes ?? items.filter((i) => i.status === 'done').reduce((a, i) => a + (i.size_bytes || 0), 0),
				max_bytes: t.max_bytes ?? dl?.max_bytes ?? 0
			}
		};
	},
	saveOffline: (video_id: string) => post<DownloadItem>('/downloads', { video_id }),
	reencodeDownloads: () => post<{ queued: number }>('/downloads/reencode', {}),
	clearDownloads: () => del<{ ok: boolean; removed: number }>('/downloads'),
	removeDownload: (video_id: string) => del<{ ok: boolean }>(`/downloads/${encodeURIComponent(video_id)}`),
	fileUrl: (id: string) => `/api/video/${encodeURIComponent(id)}/file`,

	// feedback & lessons
	feedback: (body: { video_id: string; category_id?: number | null; rating: number; chips?: string[]; text?: string }) => post<Feedback>('/feedback', body),
	feedbackList: (limit = 100) => get<Feedback[]>(`/feedback${q({ limit })}`),
	deleteFeedback: (id: number) => del<{ ok: boolean }>(`/feedback/${id}`),
	lessons: () => get<Lesson[]>('/lessons'),
	createLesson: (l: Partial<Lesson> & { text: string }) => post<Lesson>('/lessons', l),
	updateLesson: (id: number, l: Partial<Lesson>) => patch<Lesson>(`/lessons/${id}`, l),
	deleteLesson: (id: number) => del<{ ok: boolean }>(`/lessons/${id}`),

	// history
	history: (limit = 60) => get<(HistoryEntry & { video: Video | { id: string } })[]>(`/history${q({ limit })}`),
	historyFor: (videoId: string) => get<HistoryEntry>(`/history/${encodeURIComponent(videoId)}`, { toast: false }),
	restartHistory: (videoId: string, updated_at: string) =>
		post<{ ok: boolean; restarted: boolean }>(`/history/${encodeURIComponent(videoId)}/restart`, { updated_at }, { toast: false }),
	saveHistory: (h: { video_id: string; position_s: number; duration_s?: number; category_id?: number | null; finished?: boolean }, keepalive = false) =>
		post<{ ok: boolean; finished: boolean }>('/history', h, { toast: false, keepalive }),
	clearHistory: () => del<{ ok: boolean }>('/history'),
	markSeen: (video_id: string) => post<{ ok: boolean; dismissed: number }>('/history/seen', { video_id }),

	// settings
	settings: () => get<Settings>('/settings'),
	saveSettings: <K extends keyof Settings>(key: K, value: Partial<Settings[K]>) => put<Settings[K]>(`/settings/${key}`, value),
	uploadCookies: (text: string) => request<{ present: boolean }>('/cookies', { method: 'PUT', raw: text, headers: { 'Content-Type': 'text/plain' } }),
	deleteCookies: () => del<{ present: boolean }>('/cookies'),
	testCookies: () => post<CookieTestResult>('/cookies/test', {}, { toast: false }),
	importSubscriptions: () => post<ImportSubscriptionsResult>('/ytimport/subscriptions', {}, { toast: false }),
	importFeed: (body: { category_id?: number; playlist_name?: string; source: string; playlist_url?: string; limit: number }) =>
		post<ImportFeedResult>('/ytimport/feed', body, { toast: false }),
	stats: () => get<{ videos: number; candidates_24h: number; lessons: number }>('/stats', { toast: false }),
	statsPage: (days = 30) => get<StatsPageData>(`/stats/page${q({ days })}`, { toast: false }),
	memoryStatus: () => get<MemoryStatus>('/agent/memory', { toast: false }),
	runMemory: () => post<{ queued: boolean }>('/agent/memory/run', {}),
	statsExplore: (filter: StatsExploreFilter, offset = 0) => get<StatsExploreData>(`/stats/explore${q({ ...filter, offset })}`),

	// agent
	agentStatus: () => get<AgentStatus>('/agent/status', { toast: false }),
	agentEvents: (o: { after_id?: number; limit?: number; category_id?: number } = {}) => get<AgentEvent[]>(`/agent/events${q(o)}`, { toast: false }),
	agentPause: () => post<AgentControl>('/agent/pause'),
	agentResume: () => post<AgentControl>('/agent/resume'),
	agentResetBackoff: () => post<AgentControl>('/agent/reset-backoff'),
	agentRun: (category_id?: number | null) => post<AgentControl>('/agent/run', { category_id: category_id ?? null }),
	agentPlaying: (seconds = 90) => request<AgentControl>(`/agent/playing${q({ seconds })}`, { method: 'POST', toast: false }),
	candidate: (id: number) => get<CandidateDetail>(`/agent/candidates/${id}`),
	llmModels: (body: { base_url?: string; api_key?: string }) => post<{ ok: boolean; models: string[]; error?: string }>('/agent/llm-models', body, { toast: false }),
	llmTest: (body: { base_url?: string; model?: string; api_key?: string }) => post<LlmTestResult>('/agent/llm-test', body, { toast: false }),
	proxyProviders: () => get<{ providers: ProxyProvider[] }>('/proxy/providers', { toast: false }),
	proxyTest: (url: string) => post<ProxyTestResult>('/proxy/test', { url }, { toast: false })
};
