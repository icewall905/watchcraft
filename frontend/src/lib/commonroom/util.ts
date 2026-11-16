export const PLATFORMS: Record<string, {label: string; icon: string}> = {
 reddit: {label: 'Reddit', icon: '👽'}, lemmy: {label: 'Lemmy', icon: '🐭'}, mbin: {label: 'Mbin', icon: '🧵'}, mastodon: {label: 'Mastodon', icon: '🐘'},
 bluesky: {label: 'Bluesky', icon: '🦋'}, hackernews: {label: 'Hacker News', icon: '🟧'}, hn: {label: 'Hacker News', icon: '🟧'}, lobsters: {label: 'Lobsters', icon: '🦞'},
 devto: {label: 'dev.to', icon: '👩‍💻'}, tildes: {label: 'Tildes', icon: '🌲'}, stackexchange: {label: 'Stack Exchange', icon: '📚'}, discourse: {label: 'Discourse', icon: '💬'}
};
export const platformOf = (k: string) => PLATFORMS[k] ?? {label: k, icon: '🌐'};
export function ago(ts: number | null | undefined, now = Date.now()): string {
 if (!ts) return '';
 const d = Math.max(0, now / 1000 - (ts > 1e12 ? ts / 1000 : ts));
 if (d < 60) return 'now';
 if (d < 3600) return `${Math.round(d / 60)}m`;
 if (d < 86400) return `${Math.round(d / 3600)}h`;
 if (d < 86400 * 30) return `${Math.round(d / 86400)}d`;
 if (d < 86400 * 365) return `${Math.round(d / 86400 / 30)}mo`;
 return `${Math.round(d / 86400 / 365)}y`;
}
export const compact = (n: number | null | undefined) => n == null ? '' : n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
export const REASONS = ['off-topic', 'low effort', 'seen it', 'too basic', 'more like this'];
