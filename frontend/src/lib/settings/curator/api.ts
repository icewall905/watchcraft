// Settings → Curator: what the curator knows, what it learned and why (/api/curator).
import { request } from '$lib/api';

export type Surface = 'video' | 'short' | 'article' | 'news' | 'meme' | 'web' | 'moodboard' | 'music' | 'hub' | 'correction';
export type SignalKind = 'like' | 'love' | 'dislike' | 'save' | 'dismiss' | 'read' | 'seen' | 'overrule_up' | 'overrule_down'
	| 'typed_up' | 'typed_down' | 'hide_language' | 'fit' | 'not_fit' | 'favorite' | 'less' | 'note';
export type SectionKey = 'overall' | 'videos' | 'shorts' | 'news' | 'articles' | 'memes' | 'web' | 'moodboards' | 'music';
export type Polarity = 1 | -1;

export type Signal = {
	kind: SignalKind; surface: Surface; ref_type: string; ref_id: number | string | null;
	title: string; url?: string; note?: string; board?: string; at: number;
};
export type Teaching = {
	id: number; surface: Surface; section: SectionKey; polarity: Polarity; text: string; reason: string; signals: Signal[];
	status: 'pending' | 'merged' | 'rejected' | 'superseded'; user_authored: boolean; origin: string; board_id: number | null;
	correction: string; note: string; merged_version_id: number | null; rejected_at: number | null; created_at: number; updated_at: number;
};
export type Statement = {
	sid: string; text: string; polarity: Polarity; confidence: 'tentative' | 'established' | 'explicit'; locked: boolean;
	teaching_ids: number[]; board_id: number | null; since: number; updated: number;
};
export type KnowledgeSection = { key: SectionKey; title: string; statements: Statement[] };
export type Cause = 'migration' | 'nightly' | 'chat' | 'edit' | 'undo';
type Diffed = { sid: string; section: SectionKey; polarity: Polarity };
export type Changes = {
	added: (Diffed & { text: string })[]; changed: (Diffed & { before: string; after: string })[];
	removed: (Diffed & { text: string })[]; merged: number[];
};
export type Version = { id: number; version: number; cause: Cause; created_at: number; changes: Changes };
export type HistoryEntry = { id: number; version: number; cause: Cause; created_at: number; added: number; changed: number; removed: number; merged: number };
export type ProposalOp = { op: 'add' | 'edit' | 'remove'; section: SectionKey; sid: string | null; text: string | null; polarity: Polarity; before?: string };
export type ChatMessage = {
	id: number; role: 'user' | 'curator'; text: string; proposal: { ops?: ProposalOp[] };
	proposal_status: 'none' | 'pending' | 'accepted' | 'rejected' | 'stale';
	base_version_id: number | null; applied_version_id: number | null; created_at: number;
};
export type Knowledge = { version: Version | null; sections: KnowledgeSection[]; history: HistoryEntry[]; pending: Partial<Record<Surface, number>> };
export type Snapshot = { version: Version; sections: KnowledgeSection[] };

const C = '/curator';
const get = <T>(p: string, toast = true) => request<T>(C + p, { toast });
const send = <T>(method: string, p: string, body: unknown = {}, toast = true) => request<T>(C + p, { method, body, toast });

export const curator = {
	// toast=false where the caller shows the error inline.
	knowledge: () => get<Knowledge>('/knowledge', false),
	version: (id: number) => get<Version & { sections: KnowledgeSection[] }>(`/knowledge/versions/${id}`, false),
	restoreVersion: (id: number) => send<Version>('POST', `/knowledge/versions/${id}/restore`),
	editStatement: (sid: string, body: { text?: string; polarity?: Polarity; locked?: boolean; remove?: boolean }) =>
		send<Snapshot>('PATCH', `/knowledge/statements/${encodeURIComponent(sid)}`, body, false),
	why: (sid: string) => get<{ statement: Statement; teachings: Teaching[] }>(`/knowledge/statements/${encodeURIComponent(sid)}/why`, false),
	teachings: (status: Teaching['status'], surface = '') =>
		get<{ teachings: Teaching[] }>(`/teachings?status=${status}${surface ? `&surface=${encodeURIComponent(surface)}` : ''}`, false),
	teach: (body: { text: string; section: SectionKey; polarity: Polarity }) => send<Teaching>('POST', '/teachings', body),
	reject: (id: number, correction = '') =>
		send<{ teaching: Teaching; correction: Teaching | null; message: ChatMessage | null }>('POST', `/teachings/${id}/reject`, correction ? { correction } : {}),
	restoreTeaching: (id: number) => send<Teaching>('POST', `/teachings/${id}/restore`),
	chat: (toast = true) => get<{ messages: ChatMessage[] }>('/chat', toast),
	// The user's message can come back null; the curator's reply is always there.
	say: (text: string) => send<{ messages: (ChatMessage | null)[] }>('POST', '/chat', { text }, false),
	// The caller explains a 409 (the knowledge moved on since the suggestion) itself.
	accept: (id: number) => send<Snapshot & { message: ChatMessage }>('POST', `/chat/${id}/accept`, {}, false),
	discard: (id: number) => send<ChatMessage>('POST', `/chat/${id}/reject`),
	weave: () => send<{ started: boolean }>('POST', '/knowledge/weave')
};

export const SECTIONS: { key: SectionKey; title: string }[] = [
	{ key: 'overall', title: 'Overall' }, { key: 'videos', title: 'Videos' }, { key: 'shorts', title: 'Shorts' },
	{ key: 'news', title: 'News' }, { key: 'articles', title: 'Articles' }, { key: 'memes', title: 'Memes' },
	{ key: 'web', title: 'Web' }, { key: 'moodboards', title: 'Mood boards' }, { key: 'music', title: 'Music' }
];
export const sectionTitle = (key: string) => SECTIONS.find((s) => s.key === key)?.title ?? key;

export const SURFACE: Record<Surface, { icon: string; label: string; one: string; many: string }> = {
	video: { icon: '🎬', label: 'Videos', one: 'video', many: 'videos' },
	short: { icon: '📱', label: 'Shorts', one: 'short', many: 'shorts' },
	news: { icon: '📰', label: 'News', one: 'news story', many: 'news stories' },
	article: { icon: '📄', label: 'Articles', one: 'article', many: 'articles' },
	meme: { icon: '😂', label: 'Memes', one: 'meme', many: 'memes' },
	moodboard: { icon: '📌', label: 'Mood boards', one: 'pin', many: 'pins' },
	music: { icon: '🎵', label: 'Music', one: 'track', many: 'tracks' },
	web: { icon: '🌐', label: 'Web', one: 'page', many: 'pages' },
	hub: { icon: '✧', label: 'Hub', one: 'item', many: 'items' },
	correction: { icon: '✍️', label: 'Corrections', one: 'correction', many: 'corrections' }
};
const surface = (s: string) => SURFACE[s as Surface] ?? { icon: '•', label: s, one: 'item', many: 'items' };
export const surfaceIcon = (s: string) => surface(s).icon;
export const surfaceLabel = (s: string) => surface(s).label;

export const VERB: Record<SignalKind, string> = {
	like: 'Liked', dislike: 'Disliked', love: 'Loved', save: 'Saved', dismiss: 'Dismissed', overrule_up: 'Promoted',
	overrule_down: 'Demoted', typed_up: 'Told curator more', typed_down: 'Told curator less', hide_language: 'Hid language',
	fit: 'Fit for board', not_fit: 'Not a fit for board', favorite: 'Favourited', less: 'Play less', note: 'Wrote', read: 'Read', seen: 'Seen'
};
export const verb = (k: string) => VERB[k as SignalKind] ?? k;

export const CONFIDENCE: Record<Statement['confidence'], string> = { explicit: 'You told me', established: 'Established', tentative: 'Tentative' };
export const CAUSE: Record<Cause, string> = { nightly: 'Nightly weave', chat: 'Chat correction', edit: 'Your edit', undo: 'Restored', migration: 'Initial import' };
/** Only same-site paths and http(s) links; feed data can carry javascript: URLs. */
export function safeHref(u: string | undefined): string {
	if (!u) return '';
	if (u.startsWith('/')) return u[1] === '/' || u[1] === '\\' ? '' : u;
	try { const { protocol } = new URL(u); return protocol === 'http:' || protocol === 'https:' ? u : ''; } catch { return ''; }
}
export const direction = (p: Polarity) => (p === 1 ? 'Prefer' : 'Avoid');

const day = (t: number) => new Date(t * 1000);
export const shortDate = (t: number) => day(t).toLocaleDateString('en', { month: 'short', day: 'numeric' });
export const longDate = (t: number) => day(t).toLocaleString('en', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
function dateRange(times: number[]) {
	const lo = day(Math.min(...times)), hi = day(Math.max(...times));
	const a = shortDate(lo.getTime() / 1000);
	if (lo.toDateString() === hi.toDateString()) return a;
	if (lo.getMonth() === hi.getMonth() && lo.getFullYear() === hi.getFullYear()) return `${a}–${hi.getDate()}`;
	return `${a}–${shortDate(hi.getTime() / 1000)}`;
}

/** "Because you disliked 3 shorts: A, B, C (Nov 13–14)" — one clause per kind and surface. */
export function because(signals: Signal[]): string {
	const groups = new Map<string, Signal[]>();
	for (const s of signals) {
		const key = `${s.kind}:${s.surface}`;
		groups.set(key, [...(groups.get(key) ?? []), s]);
	}
	const clauses = [...groups.values()].map((list) => {
		const { kind, surface: where } = list[0], n = list.length, s = surface(where);
		const titles = list.slice(0, 3).map((x) => x.title).filter(Boolean);
		const more = n > titles.length && titles.length ? ', …' : '';
		const named = titles.length ? `: ${titles.join(', ')}${more}` : '';
		return `${verb(kind).toLowerCase()} ${n} ${n === 1 ? s.one : s.many}${named} (${dateRange(list.map((x) => x.at))})`;
	});
	return clauses.length ? `Because you ${clauses.join('; ')}` : '';
}
