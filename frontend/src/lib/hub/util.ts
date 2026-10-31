// Small helpers for hub pages (display only; never invent data).
import type { Item } from './types';

export const str = (m: Record<string, unknown> | undefined, k: string): string => {
	const v = m?.[k];
	return typeof v === 'string' ? v : typeof v === 'number' ? String(v) : '';
};
export const num = (m: Record<string, unknown> | undefined, k: string): number | null => {
	const v = m?.[k];
	const n = typeof v === 'number' ? v : typeof v === 'string' && v.trim() !== '' ? Number(v) : NaN;
	return Number.isFinite(n) ? n : null;
};
export const list = (m: Record<string, unknown> | undefined, k: string): unknown[] => {
	const v = m?.[k];
	return Array.isArray(v) ? v : [];
};
export const bool = (m: Record<string, unknown> | undefined, k: string): boolean => m?.[k] === true || m?.[k] === 'true';

export const duration = (s: number | null) => {
	if (!s || s < 0) return '';
	const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
	return h ? `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}` : `${m}:${String(sec).padStart(2, '0')}`;
};
export const fmtDate = (t: number | null | undefined) => (t ? new Date(t * 1000).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '');
export const label = (k: string) => k.replace(/[_-]+/g, ' ').replace(/^./, (c) => c.toUpperCase());
export const safeUrl = (u: unknown): string => {
	if (typeof u !== 'string') return '';
	try { const x = new URL(u); return x.protocol === 'https:' || x.protocol === 'http:' ? x.href : ''; } catch { return ''; }
};
export const isGalleryType = (t: string) => t === 'recipe' || t === 'pattern';
export const sevClass = (s: string) => {
	const v = s.toLowerCase();
	return v === 'critical' || v === 'high' ? 'bad' : v === 'medium' ? 'warn' : v === 'low' ? 'ok' : '';
};
export const lsGet = (k: string): string | null => { try { return localStorage.getItem(k); } catch { return null; } };
export const lsSet = (k: string, v: string) => { try { localStorage.setItem(k, v); } catch { /* ignore */ } };
export const sortNewest = (a: Item, b: Item) => b.first_seen - a.first_seen;
export const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** URL param `since` holds a number of days; API wants a unix timestamp. */
export const sinceTs = (days: string | null) => {
	const d = Number(days);
	return d > 0 ? Math.floor(Date.now() / 1000 - d * 86400) : undefined;
};
/** Filter keys shared by ItemFilters and pages. */
export const FILTER_KEYS = ['type', 'provider', 'curator', 'unread', 'saved', 'min_score', 'since', 'sort'] as const;

/** Watcher rules as compact "key: value" text; empty rules mean the provider defaults. */
export const describeRules = (config: Record<string, unknown> | object | null | undefined): string => {
	const parts = Object.entries(config ?? {})
		.filter(([, v]) => v !== undefined && v !== null && v !== '' && !(Array.isArray(v) && !v.length))
		.map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(', ') : String(v)}`);
	return parts.length ? parts.join(' · ') : 'default rules';
};
