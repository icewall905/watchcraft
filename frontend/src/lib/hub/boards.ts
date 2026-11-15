import { request } from '$lib/api';
import { qs } from './api';
export interface MoodBoard {
 id: number; name: string; interests: string; instructions: string; enabled: boolean; interval_hours: number;
 pin_count: number; target_images: number; reference_url: string | null; reference_name: string; last_run?: number | null; last_status?: string; error?: string;
 retry_after?: number; last_result?: string; cover_url?: string | null;
 preview_urls?: string[]; saved_count?: number; last_added?: number | null; created_at?: number;
 avoid?: string; seed_searches?: string; media?: 'any' | 'photos' | 'gifs'; min_width?: number; strictness?: 'strict' | 'balanced'; child_friendly?: boolean;
}
export interface BoardPin {
 id: number; board_id: number; title: string; image_url: string; thumb_url?: string; source_url: string;
 media_type: string; attribution?: string; license?: string; saved: boolean;
 feedback?: '' | 'good' | 'bad'; feedback_note?: string;
 width?: number; height?: number; vision_seen?: string; vision_why?: string;
}
export type BoardInput = Pick<MoodBoard, 'name' | 'interests' | 'instructions' | 'enabled' | 'interval_hours'> & {target_images: number; avoid: string; seed_searches: string; media: 'any' | 'photos' | 'gifs'; min_width: number; strictness: 'strict' | 'balanced'; child_friendly: boolean};
export type BoardImageInput = {url?: string; data_base64?: string; title?: string; source_url?: string};
export interface BoardPage { board: MoodBoard; pins: BoardPin[]; next_cursor: string | null; has_more: boolean }
export interface PinterestImport { id: number; board_id: number; status: string; imported: number; skipped: number; total: number; error: string }
export interface FoundPin extends BoardPin { board_name: string; similarity: number }
export interface BoardSearch { available: boolean; reason: string; indexed: number; pins_total: number; pins: FoundPin[] }
export interface ExtensionKey { id: number; name: string; hint: string; created_at: number; last_used: number | null }
export interface PinterestResult { token: string; title: string; pin_url: string; attribution: string; width: number; height: number; preview_url: string; on_board: boolean }
export interface PinterestStatus { enabled: boolean; blocked: boolean; blocked_until: number; reason: string; last_block_at: number; last_reason: string; hour_left: number; day_left: number; images_left: number }
const base = '/hub/boards';
export const boardsApi = {
 pinterest: (id: number, url: string) => request<{import: PinterestImport}>(`${base}/${id}/pinterest`, {method: 'POST', body: {url}, toast: false}),
 pinterestStatus: (id: number, signal?: AbortSignal) => request<{import: PinterestImport | null}>(`${base}/${id}/pinterest`, {signal, toast: false}),
 pinterestLive: (id: number, q: string, more = false) => request<{results: PinterestResult[]; has_more: boolean}>(`${base}/${id}/pinterest/search${qs({q, more: more || undefined})}`, {toast: false}),
 pinterestAdd: (id: number, token: string) => request<{pin: BoardPin; existing?: boolean}>(`${base}/${id}/pinterest/add`, {method: 'POST', body: {token}, toast: false}),
 pinterestLiveStatus: () => request<PinterestStatus>(`${base}/pinterest/status`, {toast: false}),
 search: (q: string, signal?: AbortSignal) => request<BoardSearch>(`${base}/search${qs({q})}`, {signal, toast: false}),
 list: (signal?: AbortSignal) => request<{boards: MoodBoard[]}>(base, {signal, toast: false}),
 detail: (id: number, params: {cursor?: string; saved?: boolean; unreviewed?: boolean} = {}, signal?: AbortSignal) => request<BoardPage>(`${base}/${id}${qs(params)}`, {signal, toast: false}),
 create: (body: BoardInput) => request<{board: MoodBoard}>(base, {method: 'POST', body, toast: false}),
 update: (id: number, body: BoardInput) => request<{board: MoodBoard}>(`${base}/${id}`, {method: 'PATCH', body, toast: false}),
 reshuffle: (id: number) => request<{replaced: number; kept: number; board: MoodBoard}>(`${base}/${id}/reshuffle`, {method: 'POST', body: {}, toast: false}),
 refresh: (id: number) => request<{status: 'queued' | 'collecting' | 'cooldown'; queued: boolean; retry_after: number; last_result: string; board: MoodBoard}>(`${base}/${id}/refresh`, {method: 'POST', body: {}, toast: false}),
 addImage: (id: number, body: BoardImageInput) => request<{pin: BoardPin; existing?: boolean}>(`${base}/${id}/images`, {method: 'POST', body, toast: false}),
 setReference: (id: number, body: BoardImageInput) => request<{board: MoodBoard}>(`${base}/${id}/reference`, {method: 'POST', body, toast: false}),
 removeReference: (id: number) => request<{board: MoodBoard}>(`${base}/${id}/reference`, {method: 'DELETE', toast: false}),
 pin: (id: number, pin: number, body: {saved?: boolean; hidden?: boolean; board_id?: number}) => request<{pin: BoardPin}>(`${base}/${id}/pins/${pin}`, {method: 'PATCH', body, toast: false}),
 review: (id: number, pin: number, body: {verdict: '' | 'good' | 'bad'; note?: string}) => request<{pin: BoardPin; board: MoodBoard}>(`${base}/${id}/pins/${pin}/feedback`, {method: 'PUT', body, toast: false}),
 extKeys: () => request<{keys: ExtensionKey[]}>('/ext/keys', {toast: false}),
 createExtKey: (name: string) => request<{key: string; info: ExtensionKey}>('/ext/keys', {method: 'POST', body: {name}, toast: false}),
 revokeExtKey: (id: number) => request<unknown>(`/ext/keys/${id}`, {method: 'DELETE', toast: false}),
 remove: (id: number) => request<unknown>(`${base}/${id}`, {method: 'DELETE', toast: false})
};
