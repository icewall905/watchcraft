import { request } from '$lib/api';

export type Mode = 'page_watch' | 'rss' | 'github_releases' | 'web_search' | 'bing_news';
export type Plan = { mode: Mode; source: string; selector: string; terms: string[] };
export type Watch = {
	id: number; goal: string; plan: Plan; interval: number; enabled: boolean; completed: boolean;
	next_check: number; last_check: number | null; error: string;
};
export type WatchUpdate = { id: number; watch_id: number; text: string; url: string; created_at: number; unread: boolean };

const call = <T>(path = '', opts: Parameters<typeof request>[1] = {}) =>
	request<T>('/hub/watcher' + path, { ...opts, toast: false });

export const watcher = {
	list: () => call<Watch[]>(),
	draft: (goal: string) => call<{ plan?: Plan; question?: string }>('/draft', { body: { goal } }),
	create: (goal: string, plan: Plan, interval: number) => call<Watch>('', { body: { goal, plan, interval } }),
	enable: (id: number, enabled: boolean) => call(`/watches/${id}`, { method: 'PATCH', body: { enabled } }),
	remove: (id: number) => call(`/watches/${id}`, { method: 'DELETE' }),
	updates: (unread = false, signal?: AbortSignal) => call<WatchUpdate[]>(unread ? '/updates?unread=true' : '/updates', { signal }),
	testNotification: () => call<WatchUpdate>('/updates/test', { method: 'POST' }),
	read: (id: number) => call(`/updates/${id}/read`, { method: 'POST' }),
	androidStatus: () => call<{ configured: boolean }>('/android'),
	pairAndroid: () => call<{ endpoint: string; since: number }>('/android', { method: 'POST' }),
	revokeAndroid: () => call('/android', { method: 'DELETE' })
};

export const intervals = { 10: '10 minutes', 60: '1 hour', 1440: 'Daily' } as Record<number, string>;
export const modes: Record<Mode, string> = {
	page_watch: 'Page text changes', rss: 'New feed entries', github_releases: 'GitHub releases',
	web_search: 'Web search', bing_news: 'News search'
};
export const isSearch = (mode: Mode) => mode === 'web_search' || mode === 'bing_news';
