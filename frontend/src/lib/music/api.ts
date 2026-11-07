// Music Videos API client (/api/music). Backend: backend/bt/music/api.py.
import { request } from '$lib/api';

export type Track = {
	video_id: string; title: string; artist: string; artist_id: string; views: number; duration_s: number; thumbnail: string;
	fav?: boolean; fav_artist?: boolean; reason?: string;
	/** Stored on this server: plays from disk, no YouTube needed. */
	offline?: boolean;
	/** Client-only: what the DJ said when it queued this track. */
	dj_line?: string;
};
export type FavArtist = { name: string; artist_id: string; key: string };
export type Favorites = { tracks: Track[]; artists: FavArtist[] };
export type Curator = { key: string; name: string; emoji: string; color: string; taste: string };
export type Mix = {
	id: number; name: string; kind: 'seed' | 'curator' | 'manual'; emoji: string; description: string; curator: string;
	curator_name: string; color: string; blurb: string; seed_video_id: string; status: 'building' | 'ready' | 'error';
	error: string; count: number; created_at: number; refreshed_at: number; covers: string[]; tracks?: Track[];
};
export type OfflineSummary = { wanted: number; saved: number; pending: number };
export type MusicHome = { favorites: Favorites; mixes: Mix[]; recent: Track[]; ai: boolean; curators: Curator[]; offline: OfflineSummary };
export type ForYou = { loved: Track[]; fresh: Track[]; generated_at: number };
export type DjResult = { line: string; tracks: Track[]; source: 'ai' | 'radio' };

const M = '/music';
const get = <T>(p: string, toast = true) => request<T>(M + p, { toast });
const send = <T>(method: string, p: string, body?: unknown, toast = true) => request<T>(M + p, { method, body: body ?? {}, toast });
const enc = encodeURIComponent;
/** Only the fields the backend stores; client-only extras stay home. */
const slim = (t: Track) => ({ video_id: t.video_id, title: t.title, artist: t.artist, artist_id: t.artist_id, views: t.views, duration_s: t.duration_s, thumbnail: t.thumbnail });

export const music = {
	home: () => get<MusicHome>('/home'),
	search: (q: string) => get<{ tracks: Track[] }>(`/search?q=${enc(q)}`),
	artist: (name: string) => get<{ name: string; fav: boolean; tracks: Track[] }>(`/artist?name=${enc(name)}`),
	radio: (videoId: string) => get<{ tracks: Track[] }>(`/radio/${enc(videoId)}`),
	forYou: (refresh = false) => get<ForYou>(`/for-you${refresh ? '?refresh=true' : ''}`),
	favorites: () => get<Favorites>('/favorites'),
	favTrack: (t: Track, on: boolean) => send<Favorites>('POST', '/favorites', { kind: 'track', on, track: slim(t) }),
	favArtist: (name: string, on: boolean, artistId = '') => send<Favorites>('POST', '/favorites', { kind: 'artist', on, artist: name, artist_id: artistId }),
	play: (videoId: string, skipped: boolean) => send<{ ok: boolean }>('POST', '/plays', { video_id: videoId, skipped }, false),
	mixes: () => get<{ mixes: Mix[] }>('/mixes'),
	mix: (id: number) => get<Mix>(`/mixes/${id}`),
	createMix: (body: { kind: Mix['kind']; seed?: Track; description?: string; curator?: string; name?: string }) =>
		send<Mix>('POST', '/mixes', { ...body, seed: body.seed ? slim(body.seed) : undefined }),
	refreshMix: (id: number) => send<Mix>('POST', `/mixes/${id}/refresh`),
	addToMix: (id: number, t: Track) => send<Mix>('POST', `/mixes/${id}/tracks`, slim(t)),
	removeFromMix: (id: number, videoId: string) => send<Mix>('DELETE', `/mixes/${id}/tracks/${enc(videoId)}`),
	deleteMix: (id: number) => send<{ ok: boolean }>('DELETE', `/mixes/${id}`),
	dj: (body: { played: string[]; queue: string[]; vibe?: string; request?: string; n?: number }) =>
		send<DjResult>('POST', '/dj', body, false)
};

export const fmtDuration = (s: number) => (s > 0 ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}` : '');
export const fmtViews = (n: number) =>
	n >= 1e9 ? `${(n / 1e9).toFixed(1)}B` : n >= 1e6 ? `${Math.round(n / 1e6)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}K` : n ? String(n) : '';
