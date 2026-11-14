import { request } from '$lib/api';
import { qs } from './api';
export interface MoodBoard {
 id: number; name: string; interests: string; instructions: string; enabled: boolean; interval_hours: number;
 pin_count: number; target_images: number; reference_url: string | null; reference_name: string; last_run?: number | null; last_status?: string; error?: string;
 retry_after?: number; last_result?: string; cover_url?: string | null;
}
export interface BoardPin {
 id: number; board_id: number; title: string; image_url: string; source_url: string;
 media_type: string; attribution?: string; license?: string; saved: boolean;
}
export type BoardInput = Pick<MoodBoard, 'name' | 'interests' | 'instructions' | 'enabled' | 'interval_hours'> & {target_images: number};
export type BoardImageInput = {url?: string; data_base64?: string; title?: string};
export interface BoardPage { board: MoodBoard; pins: BoardPin[]; next_cursor: string | null; has_more: boolean }
export interface PinterestImport { id: number; board_id: number; status: string; imported: number; skipped: number; total: number; error: string }
const base = '/hub/boards';
export const boardsApi = {
 pinterest: (id: number, url: string) => request<{import: PinterestImport}>(`${base}/${id}/pinterest`, {method: 'POST', body: {url}, toast: false}),
 pinterestStatus: (id: number, signal?: AbortSignal) => request<{import: PinterestImport | null}>(`${base}/${id}/pinterest`, {signal, toast: false}),
 list: (signal?: AbortSignal) => request<{boards: MoodBoard[]}>(base, {signal, toast: false}),
 detail: (id: number, params: {cursor?: string; saved?: boolean} = {}, signal?: AbortSignal) => request<BoardPage>(`${base}/${id}${qs(params)}`, {signal, toast: false}),
 create: (body: BoardInput) => request<{board: MoodBoard}>(base, {method: 'POST', body, toast: false}),
 update: (id: number, body: BoardInput) => request<{board: MoodBoard}>(`${base}/${id}`, {method: 'PATCH', body, toast: false}),
 refresh: (id: number) => request<{status: 'queued' | 'collecting' | 'cooldown'; queued: boolean; retry_after: number; last_result: string; board: MoodBoard}>(`${base}/${id}/refresh`, {method: 'POST', body: {}, toast: false}),
 addImage: (id: number, body: BoardImageInput) => request<{pin: BoardPin}>(`${base}/${id}/images`, {method: 'POST', body, toast: false}),
 setReference: (id: number, body: BoardImageInput) => request<{board: MoodBoard}>(`${base}/${id}/reference`, {method: 'POST', body, toast: false}),
 removeReference: (id: number) => request<{board: MoodBoard}>(`${base}/${id}/reference`, {method: 'DELETE', toast: false}),
 pin: (id: number, pin: number, body: {saved?: boolean; hidden?: boolean; board_id?: number}) => request<{pin: BoardPin}>(`${base}/${id}/pins/${pin}`, {method: 'PATCH', body, toast: false}),
 remove: (id: number) => request<unknown>(`${base}/${id}`, {method: 'DELETE', toast: false})
};
