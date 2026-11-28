// Jellyfin API client (/api/jellyfin). Backend: backend/bt/jellyfin/api.py.
// Image and media URLs come back signed and proxied: the Jellyfin token never reaches the browser.
import { request } from '$lib/api';

export type Track = { index: number; label: string; lang: string | null; default: boolean; forced?: boolean; external?: boolean; text?: boolean; url?: string; burned?: boolean };
export type Item = {
	id: string; name: string; type: string; collection_type: string | null; year: number | null; overview: string;
	runtime_s: number; official_rating: string | null; community_rating: number | null; genres: string[];
	series_id: string | null; series_name: string | null; season_id: string | null; season: number | null; episode: number | null;
	premiere: string | null; is_folder: boolean; child_count: number | null; unplayed: number | null; played: boolean; favorite: boolean;
	position_s: number; progress: number; image: string | null; thumb: string | null; backdrop: string | null; playable: boolean;
	people?: { name: string; role: string; type: string; image: string | null }[];
	audio?: Track[]; subtitles?: Track[];
	rating_from_series?: boolean; studios?: string[]; countries?: string[];
	tagline?: string; original_title?: string | null; critic_rating?: number | null;
	video?: { width: number | null; height: number | null; codec: string | null; range: string | null };
	trailer?: { item_id: string } | { url: string } | null;
	seasons?: Item[]; episodes?: Item[]; next_up?: Item | null;
};
export type Status = { configured: boolean; allowed: boolean; linked: boolean; user_name: string; is_admin: boolean; server_name: string; version: string };
export type Home = { resume: Item[]; next_up: Item[]; views: Item[]; latest: { view: Item; items: Item[] }[] };
export type Caps = { hevc: boolean; av1: boolean; vp9: boolean; ac3: boolean; surround: boolean; hdr: boolean; max_height: number };
export type PlayIn = { caps: Caps; max_bitrate?: number; start_s?: number; audio_index?: number | null; subtitle_index?: number | null; force_transcode?: boolean };
export type Play = {
	session: string; method: 'DirectPlay' | 'DirectStream' | 'Transcode'; kind: 'file' | 'hls'; url: string; start_s: number; duration_s: number;
	audio: Track[]; subtitles: Track[]; audio_index: number | null; subtitle_index: number | null; transcode_reasons: string[]; item: Item; next: Item | null;
};
export type Playing = {
	method: string | null;
	transcode: null | { video_codec: string; audio_codec: string; container: string; video_direct: boolean; audio_direct: boolean; hardware: string | null;
		bitrate: number | null; framerate: number | null; width: number | null; height: number | null; progress: number | null; reasons: string[] };
};
export type Encoding = { known: boolean; reason?: string; hardware?: string | null; decoding_codecs?: string[]; hevc_encoding?: boolean; av1_encoding?: boolean; tonemapping?: boolean; low_power?: boolean };

const J = '/jellyfin';
// Set while this screen plays a title another profile sent to it: requests then act as the sender,
// even when this screen is signed in to another account (backend/bt/jellyfin/cast.py).
let castGrant: string | null = null;
export const setCastGrant = (g: string | null) => { castGrant = g; };
const hdrs = () => (castGrant ? { 'X-Cast-Grant': castGrant } : undefined);
const get = <T>(p: string, toast = true) => request<T>(J + p, { toast, headers: hdrs() });
const send = <T>(method: string, p: string, body?: unknown, toast = true) => request<T>(J + p, { method, body: body ?? {}, toast, headers: hdrs() });
const qs = (o: Record<string, string | number | boolean | undefined | null>) =>
	new URLSearchParams(Object.entries(o).filter(([, v]) => v !== undefined && v !== null && v !== '' && v !== false).map(([k, v]) => [k, String(v)])).toString();

export const jellyfin = {
	status: () => get<Status>('/status', false),
	config: () => get<{ url: string; allow_children: boolean }>('/config'),
	saveConfig: (url: string, allow_children: boolean) => send<{ url: string; server_name?: string; version?: string }>('PUT', '/config', { url, allow_children }, false),
	encoding: () => get<Encoding>('/encoding', false),
	link: (username: string, password: string) => send<Status>('POST', '/link', { username, password }, false),
	unlink: () => send<Status>('DELETE', '/link'),
	home: () => get<Home>('/home'),
	views: () => get<Item[]>('/views'),
	items: (o: { parent_id?: string; types?: string; sort?: string; descending?: boolean; start?: number; limit?: number; q?: string; unplayed?: boolean; favorite?: boolean; genre?: string; max_age?: number | null }) =>
		get<{ items: Item[]; total: number }>(`/items?${qs(o)}`),
	filters: (parent_id: string, types?: string) => get<{ genres: string[]; ages: number[] }>(`/filters?${qs({ parent_id, types })}`, false),
	search: (q: string, toast = true) => get<{ items: Item[] }>(`/search?${qs({ q })}`, toast),
	item: (id: string) => get<Item>(`/items/${id}`),
	episodes: (seriesId: string, seasonId?: string) => get<{ items: Item[] }>(`/shows/${seriesId}/episodes?${qs({ season_id: seasonId })}`),
	played: (id: string, on: boolean) => send<{ ok: boolean }>('POST', `/items/${id}/played`, { on }),
	favorite: (id: string, on: boolean) => send<{ ok: boolean }>('POST', `/items/${id}/favorite`, { on }),
	play: (id: string, body: PlayIn) => send<Play>('POST', `/items/${id}/play`, body, false),
	progress: (session: string, position_s: number, paused: boolean, event: 'progress' | 'stopped' = 'progress', keepalive = false) =>
		request<{ ok: boolean }>(J + '/progress', { method: 'POST', body: { session, position_s, paused, event }, toast: false, keepalive, headers: hdrs() }),
	playing: (session: string) => get<Playing>(`/playing?${qs({ session })}`, false)
};

export const img = (url: string | null, w: number) => (url ? `${url}?w=${w}` : null);

export function fmtRuntime(s: number): string {
	if (!s) return '';
	const m = Math.round(s / 60);
	return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`;
}

export function fmtTime(s: number): string {
	s = Math.max(0, Math.floor(s));
	const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
	return h ? `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}` : `${m}:${String(sec).padStart(2, '0')}`;
}

export function subtitleLine(it: Item): string {
	if (it.type === 'Episode') return `${it.series_name ?? ''}${it.season != null ? ` · S${it.season}` : ''}${it.episode != null ? `E${it.episode}` : ''}`;
	return [it.year, it.type === 'Series' && it.child_count ? `${it.child_count} seasons` : fmtRuntime(it.runtime_s)].filter(Boolean).join(' · ');
}

export function href(it: Item): string {
	if (it.type === 'CollectionFolder' || it.collection_type) return `/jellyfin/library/${it.id}`;
	if (it.type === 'Episode' || it.type === 'Movie' || it.type === 'Video' || it.type === 'MusicVideo') return `/jellyfin/item/${it.id}`;
	return `/jellyfin/item/${it.id}`;
}

/** What this browser can decode, so Jellyfin direct-plays where possible and transcodes the rest. */
export function deviceCaps(): Caps {
	const v = document.createElement('video');
	const can = (t: string) => {
		try {
			if (typeof MediaSource !== 'undefined' && MediaSource.isTypeSupported(t)) return true;
		} catch { /* old engines */ }
		return v.canPlayType(t) === 'probably';
	};
	return {
		hevc: can('video/mp4; codecs="hvc1.1.6.L120.90"') || can('video/mp4; codecs="hev1.1.6.L120.90"'),
		av1: can('video/mp4; codecs="av01.0.08M.08"'),
		vp9: can('video/webm; codecs="vp9"'),
		ac3: can('audio/mp4; codecs="ac-3"') || can('audio/mp4; codecs="ec-3"'),
		surround: false,
		// HDR sources stay HDR only on HDR screens; elsewhere Jellyfin tonemaps them to SDR while transcoding.
		hdr: typeof matchMedia === 'function' && matchMedia('(dynamic-range: high)').matches,
		max_height: Math.round(Math.max(screen.height, screen.width * 9 / 16) * (window.devicePixelRatio || 1)) >= 1440 ? 2160 : 1080
	};
}

export const QUALITIES: { label: string; bitrate: number; force: boolean }[] = [
	{ label: 'Auto', bitrate: 120_000_000, force: false },
	{ label: '20 Mbps', bitrate: 20_000_000, force: true },
	{ label: '8 Mbps', bitrate: 8_000_000, force: true },
	{ label: '4 Mbps', bitrate: 4_000_000, force: true },
	{ label: '1.5 Mbps', bitrate: 1_500_000, force: true }
];

/** Where a search hit opens: playable titles start playing (resuming), everything else shows its page. */
export const openHref = (it: Item) => (it.playable ? `/jellyfin/play/${it.id}` : `/jellyfin/item/${it.id}`);
