import { request } from '$lib/api';
import { qs } from './api';
export type Strictness = 'strict' | 'balanced' | 'loose';
export interface Thread {
 id: number; name: string; interests: string; instructions: string; avoid: string; strictness: Strictness; child_friendly: boolean; enabled: boolean;
 last_run: number | null; last_status: string; error: string; new_count: number; pick_count: number; source_count: number;
}
export interface PlatformInfo { key: string; label: string; example: string; sorts: string[] }
export interface ThreadSource { id: number; source_id: number; platform: string; locator: string; label: string; origin: 'user' | 'ai'; enabled: boolean; error: string; last_ok: number | null }
export interface Media { kind: 'image' | 'video'; url: string; thumb?: string; poster?: string }
export type Verdict = 'up' | 'down' | '';
export interface Post {
 pick_id: number; post_id: number; thread_id: number; thread_name: string; platform: string; community: string; author: string; title: string; excerpt: string;
 url: string; link_url: string; domain: string; media: Media[]; score: number | null; comments: number | null; published_at: number | null;
 why: string; relevance: number; saved: boolean; verdict: Verdict; nsfw: boolean; spoiler: boolean;
}
export type PostDetail = Post & { body: string };
export interface Comment { id: string; author: string; body: string; score: number | null; published_at: number | null; children: Comment[] }
export interface FeedPage { posts: Post[]; next_cursor: string; has_more: boolean; building: boolean }
export interface Block { id: number; kind: 'community' | 'author' | 'domain'; value: string }
export type ThreadInput = {name: string; interests: string; instructions?: string; avoid?: string; strictness?: Strictness};
const base = '/hub/commonroom';
export const crApi = {
 threads: (signal?: AbortSignal) => request<{threads: Thread[]; platforms: PlatformInfo[]; child: boolean}>(`${base}/threads`, {signal, toast: false}),
 create: (body: ThreadInput) => request<Thread>(`${base}/threads`, {method: 'POST', body, toast: false}),
 update: (id: number, body: Partial<Thread>) => request<Thread>(`${base}/threads/${id}`, {method: 'PATCH', body, toast: false}),
 remove: (id: number) => request<unknown>(`${base}/threads/${id}`, {method: 'DELETE', toast: false}),
 run: (id: number) => request<{queued: boolean}>(`${base}/threads/${id}/run`, {method: 'POST', body: {}, toast: false}),
 sources: (id: number) => request<{sources: ThreadSource[]}>(`${base}/threads/${id}/sources`, {toast: false}),
 addSource: (id: number, body: {platform: string; locator: string}) => request<ThreadSource>(`${base}/threads/${id}/sources`, {method: 'POST', body, toast: false}),
 setSource: (id: number, link: number, enabled: boolean) => request<ThreadSource>(`${base}/threads/${id}/sources/${link}`, {method: 'PATCH', body: {enabled}, toast: false}),
 removeSource: (id: number, link: number) => request<unknown>(`${base}/threads/${id}/sources/${link}`, {method: 'DELETE', toast: false}),
 learned: (id: number) => request<{lessons: string[]; up: number; down: number}>(`${base}/threads/${id}/learned`, {toast: false}),
 visit: (thread: 'all' | number) => request<{boundary: number}>(`${base}/visit`, {method: 'POST', body: {thread}, toast: false}),
 feed: (p: {thread: 'all' | number; section: 'new' | 'deep'; boundary: number; cursor?: string}, signal?: AbortSignal) => request<FeedPage>(`${base}/feed${qs(p)}`, {signal, toast: false}),
 post: (id: number) => request<PostDetail>(`${base}/posts/${id}`, {toast: false}),
 comments: (id: number) => request<{comments: Comment[]; url: string}>(`${base}/posts/${id}/comments`, {toast: false}),
 seen: (pick_ids: number[], keepalive = false) => request<unknown>(`${base}/seen`, {method: 'POST', body: {pick_ids}, toast: false, keepalive}),
 feedback: (pick: number, body: {verdict: Verdict; reasons: string[]; note: string}) => request<{ok: boolean}>(`${base}/picks/${pick}/feedback`, {method: 'PUT', body, toast: false}),
 pick: (pick: number, body: {saved?: boolean; hidden?: boolean}) => request<unknown>(`${base}/picks/${pick}`, {method: 'PUT', body, toast: false}),
 blocks: () => request<{blocks: Block[]}>(`${base}/blocks`, {toast: false}),
 block: (kind: Block['kind'], value: string) => request<unknown>(`${base}/blocks`, {method: 'POST', body: {kind, value}, toast: false}),
 unblock: (id: number) => request<unknown>(`${base}/blocks/${id}`, {method: 'DELETE', toast: false})
};
