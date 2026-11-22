import { request } from '$lib/api';
import { qs } from './api';
export interface BoardPerson { id: number; name: string; emoji: string; color: string; child: boolean }
export interface MoodBoard {
 id: number; name: string; interests: string; instructions: string; enabled: boolean; interval_hours: number;
 pin_count: number; target_images: number; reference_url: string | null; reference_name: string; last_run?: number | null; last_status?: string; error?: string;
 retry_after?: number; last_result?: string; cover_url?: string | null;
 preview_urls?: string[]; saved_count?: number; last_added?: number | null; created_at?: number;
 avoid?: string; seed_searches?: string; media?: 'any' | 'photos' | 'gifs'; min_width?: number; strictness?: 'strict' | 'balanced'; child_friendly?: boolean; ai_images?: 'ignore' | 'mark' | 'reject';
 /** Shared boards: owner, the other members, this profile's role; `kids` = a child uses it. */
 owner?: BoardPerson | null; members?: BoardPerson[]; role?: 'owner' | 'member'; kids?: boolean; kid_hidden_count?: number;
}
export interface BoardPin {
 id: number; board_id: number; title: string; image_url: string; thumb_url?: string; source_url: string;
 media_type: string; attribution?: string; license?: string; saved: boolean;
 feedback?: '' | 'good' | 'bad'; feedback_note?: string;
 width?: number; height?: number; vision_seen?: string; vision_why?: string; ai_flag?: string;
 /** Grown-ups only, on boards a child uses: '' unchecked | ok | hidden | override, and why. */
 kid_state?: '' | 'ok' | 'hidden' | 'override'; kid_reason?: string; added_by?: string; profile_id?: number;
}
export type BoardInput = Pick<MoodBoard, 'name' | 'interests' | 'instructions' | 'enabled' | 'interval_hours'> & {target_images: number; avoid: string; seed_searches: string; media: 'any' | 'photos' | 'gifs'; min_width: number; strictness: 'strict' | 'balanced'; child_friendly: boolean; ai_images: 'ignore' | 'mark' | 'reject'};
export type BoardImageInput = {url?: string; data_base64?: string; title?: string; source_url?: string};
export interface BoardPage { board: MoodBoard; pins: BoardPin[]; next_cursor: string | null; has_more: boolean }
export interface PinterestImport { id: number; board_id: number; status: string; imported: number; skipped: number; existing: number; done: number; total: number; error: string; note: string; next_at: number }
export interface FoundPin extends BoardPin { board_name: string; similarity: number }
export interface BoardSearch { available: boolean; reason: string; indexed: number; pins_total: number; pins: FoundPin[] }
export interface ExtensionKey { id: number; name: string; hint: string; created_at: number; last_used: number | null }
export interface PinterestResult { token: string; title: string; pin_url: string; attribution: string; width: number; height: number; preview_url: string; on_board: boolean }
export interface PinterestStatus { enabled: boolean; blocked: boolean; blocked_until: number; reason: string; last_block_at: number; last_reason: string; hour_left: number; day_left: number; images_left: number }
export interface BoardSource { id: number; name: string; host: string; kind: 'search_template' | 'gallery' | 'feed' | 'subreddit' | 'network_search' | 'commons_category'; status: 'ok' | 'broken' | 'disabled'; enabled: boolean; added_by: string; fit: number; tried: number; hits: number; kept: number; last_run: number; created_at: number; url: string }
export interface SourceHunt { id: number; status: 'queued' | 'running' | 'complete' | 'failed'; found: number; error: string; log: {at: number; step: string; message: string}[]; created_at: number; finished_at: number }
export interface BoardSources { sources: BoardSource[]; hunt: SourceHunt | null }
export interface LearnedStatement { sid: string; text: string; polarity: 1 | -1; confidence: 'tentative' | 'established' | 'explicit'; locked: boolean; scope: 'board' | 'all'; updated: number }
export interface LearnedReview { pin_id: number; verdict: 'good' | 'bad'; note: string; title: string; picture: string; created_at: number; thumb_url: string | null }
export interface LearnedChatOp { op: 'add' | 'edit' | 'remove' | 'scope'; sid: string | null; text: string | null; polarity: 1 | -1; before?: string }
export interface LearnedChat { id: number; role: 'user' | 'curator'; text: string; proposal: {ops?: LearnedChatOp[]}; proposal_status: 'none' | 'pending' | 'accepted' | 'rejected' | 'stale'; created_at: number }
export interface BoardLearned {
 board: {id: number; name: string; interests: string; instructions: string; avoid: string; rules: string};
 statements: LearnedStatement[];
 brief: {text: string; state: 'none' | 'fresh' | 'stale'; compiled_at: number; edited_at: number; error: string; absorbed: number; dropped: {sid: string; text: string; why: string}[]};
 pending: {id: number; text: string; polarity: number; reason: string; created_at: number}[];
 reviews: LearnedReview[]; review_counts: {good: number; bad: number};
 hosts: {host: string; reason: string; good: number; bad: number}[];
 prompt: string; chat: LearnedChat[];
}
export interface LearnedIndex { boards: {id: number; name: string; interests: string; statements: number; brief: 'none' | 'fresh' | 'stale'; flagged: number; fits: number; not_fits: number}[]; shared: LearnedStatement[] }
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
 kid: (id: number, pin: number, visible: boolean) => request<{pin: BoardPin; board: MoodBoard}>(`${base}/${id}/pins/${pin}/kid`, {method: 'POST', body: {visible}, toast: false}),
 review: (id: number, pin: number, body: {verdict: '' | 'good' | 'bad'; note?: string}) => request<{pin: BoardPin; board: MoodBoard}>(`${base}/${id}/pins/${pin}/feedback`, {method: 'PUT', body, toast: false}),
 extKeys: () => request<{keys: ExtensionKey[]}>('/ext/keys', {toast: false}),
 createExtKey: (name: string) => request<{key: string; info: ExtensionKey}>('/ext/keys', {method: 'POST', body: {name}, toast: false}),
 revokeExtKey: (id: number) => request<unknown>(`/ext/keys/${id}`, {method: 'DELETE', toast: false}),
 sources: (id: number, signal?: AbortSignal) => request<BoardSources>(`${base}/${id}/sources`, {signal, toast: false}),
 findSources: (id: number) => request<BoardSources>(`${base}/${id}/find-sources`, {method: 'POST', body: {}, toast: false}),
 setSource: (id: number, source: number, enabled: boolean) => request<BoardSources>(`${base}/${id}/sources/${source}`, {method: 'PATCH', body: {enabled}, toast: false}),
 removeSource: (id: number, source: number) => request<BoardSources>(`${base}/${id}/sources/${source}`, {method: 'DELETE', toast: false}),
 learnedIndex: (signal?: AbortSignal) => request<LearnedIndex>(`${base}/learned`, {signal, toast: false}),
 learned: (id: number, signal?: AbortSignal) => request<BoardLearned>(`${base}/${id}/learned`, {signal, toast: false}),
 learnAdd: (id: number, text: string, polarity: 1 | -1) => request<BoardLearned>(`${base}/${id}/learned/statements`, {method: 'POST', body: {text, polarity}, toast: false}),
 learnEdit: (id: number, sid: string, body: {text?: string; polarity?: 1 | -1; scope?: 'board' | 'all' | 'off'; remove?: boolean}) => request<BoardLearned>(`${base}/${id}/learned/statements/${sid}`, {method: 'PATCH', body, toast: false}),
 learnReject: (id: number, tid: number) => request<BoardLearned>(`${base}/${id}/learned/teachings/${tid}/reject`, {method: 'POST', body: {}, toast: false}),
 saveBrief: (id: number, text: string) => request<BoardLearned>(`${base}/${id}/learned/brief`, {method: 'PUT', body: {text}, toast: false}),
 refineBrief: (id: number) => request<BoardLearned>(`${base}/${id}/learned/refine`, {method: 'POST', body: {}, toast: false}),
 learnChat: (id: number, text: string) => request<BoardLearned & {messages: LearnedChat[]}>(`${base}/${id}/learned/chat`, {method: 'POST', body: {text}, toast: false}),
 learnAccept: (id: number, mid: number) => request<BoardLearned>(`${base}/${id}/learned/chat/${mid}/accept`, {method: 'POST', body: {}, toast: false}),
 learnDiscard: (id: number, mid: number) => request<BoardLearned>(`${base}/${id}/learned/chat/${mid}/reject`, {method: 'POST', body: {}, toast: false}),
 remove: (id: number) => request<unknown>(`${base}/${id}`, {method: 'DELETE', toast: false})
};
