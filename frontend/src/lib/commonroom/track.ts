import { crApi, type EventKind } from '$lib/hub/commonroom';

// What the owner does with a post beyond voting (open, dwell, links, plays, comments, full article):
// batched like impressions, flushed after a short pause or when the page is hidden.
let queue: {pick_id: number; kind: EventKind; value?: number}[] = [];
let timer: ReturnType<typeof setTimeout> | undefined;
const once = new Set<string>();

function flush(keepalive = false) {
	clearTimeout(timer); timer = undefined;
	if (!queue.length) return;
	const events = queue.splice(0, 50);
	void crApi.events(events, keepalive).catch(() => {});
	if (queue.length) flush(keepalive);
}

/** Record one event; `dedupe` keeps kinds like play/comments to one per pick per page life. */
export function track(pick_id: number | undefined, kind: EventKind, value?: number, dedupe = kind !== 'dwell') {
	if (!pick_id) return;
	const key = `${pick_id}:${kind}`;
	if (dedupe && once.has(key)) return;
	once.add(key);
	queue.push({pick_id, kind, ...(value !== undefined ? {value} : {})});
	if (!timer) timer = setTimeout(() => flush(), 2000);
}

/** Outbound link clicks inside an element carrying data-pick (the card or the post view). */
export function trackLinks(e: MouseEvent) {
	const a = (e.target as Element | null)?.closest?.('a[href]') as HTMLAnchorElement | null;
	if (!a || e.defaultPrevented || !/^https?:/.test(a.href) || a.origin === location.origin) return;
	const host = a.closest('[data-pick]') as HTMLElement | null;
	track(Number(host?.dataset.pick) || undefined, 'out');
}

if (typeof window !== 'undefined') {
	addEventListener('pagehide', () => flush(true));
	document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') flush(true); });
}
