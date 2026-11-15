import { request } from '$lib/api';

/** Mood Board sharing: copy to another household profile, or a public link (backend: bt/hub/moodboard_share.py). */
export interface ShareTarget { id: number; name: string; emoji: string; color: string }
export interface ShareLink {
 id: number; kind: 'pin' | 'board'; board_id: number; pin_id: number | null; board_name: string; pin_title: string;
 hint: string; include_source: boolean; expires_at: number | null; expired: boolean; created_at: number;
 views: number; last_viewed: number | null;
}
export interface ShareNotice {
 id: number; kind: 'pin' | 'board'; count: number; board_id: number; board_name: string;
 sender: string; sender_emoji: string; created_at: number;
}
export interface PublicPin { id: number; title: string; width: number; height: number; media_type: string; source_url?: string }
export interface PublicShare { kind: 'pin' | 'board'; title: string; expires_at: number | null; pins: PublicPin[]; next_cursor: number | null }

const base = '/hub/mood-share';
export const shareApi = {
 targets: () => request<{profiles: ShareTarget[]}>(`${base}/targets`, {toast: false}),
 toProfile: (board: number, to: number, pin_id?: number) =>
  request<{ok: boolean; added: number; recipient: string}>(`${base}/boards/${board}/to-profile`, {method: 'POST', body: {to, pin_id}, toast: false}),
 notices: () => request<{notices: ShareNotice[]}>(`${base}/notices`, {toast: false}),
 seen: (ids: number[]) => request<unknown>(`${base}/notices/seen`, {method: 'POST', body: {ids}, toast: false}),
 links: (board_id?: number) => request<{links: ShareLink[]}>(`${base}/links${board_id ? `?board_id=${board_id}` : ''}`, {toast: false}),
 createLink: (board: number, body: {pin_id?: number; expires_days: number | null; include_source: boolean}) =>
  request<{path: string; link: ShareLink}>(`${base}/boards/${board}/links`, {method: 'POST', body, toast: false}),
 revoke: (id: number) => request<unknown>(`${base}/links/${id}`, {method: 'DELETE', toast: false})
};

/** Public page: no cookie, no profile header. */
export const publicMedia = (token: string, pin: number) => `/api/public/mood/${encodeURIComponent(token)}/pins/${pin}/media`;
export async function publicShare(token: string, cursor?: number | null): Promise<PublicShare | 404 | 429> {
 const res = await fetch(`/api/public/mood/${encodeURIComponent(token)}${cursor ? `?cursor=${cursor}` : ''}`,
  {credentials: 'omit', referrerPolicy: 'no-referrer', headers: {Accept: 'application/json'}});
 if (res.status === 429) return 429;
 if (!res.ok) return 404;
 return res.json();
}
