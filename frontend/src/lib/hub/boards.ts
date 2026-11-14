import { request } from '$lib/api';
import { qs } from './api';
export interface MoodBoard {
 id: number; name: string; interests: string; instructions: string; enabled: boolean; interval_hours: number;
 pin_count: number; last_run?: number | null; last_status?: string; error?: string;
 retry_after?: number; last_result?: string;
}
export interface BoardPin {
 id: number; board_id: number; title: string; image_url: string; source_url: string;
 media_type: string; attribution?: string; license?: string; saved: boolean;
}
export type BoardInput = Pick<MoodBoard, 'name' | 'interests' | 'instructions' | 'enabled' | 'interval_hours'>;
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
 pin: (id: number, pin: number, body: {saved?: boolean; hidden?: boolean; board_id?: number}) => request<{pin: BoardPin}>(`${base}/${id}/pins/${pin}`, {method: 'PATCH', body, toast: false}),
 remove: (id: number) => request<unknown>(`${base}/${id}`, {method: 'DELETE', toast: false})
};
