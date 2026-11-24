export const PLATFORMS: Record<string, {label: string; icon: string}> = {
 reddit: {label: 'Reddit', icon: '👽'}, lemmy: {label: 'Lemmy', icon: '🐭'}, mbin: {label: 'Mbin', icon: '🧵'}, mastodon: {label: 'Mastodon', icon: '🐘'},
 bluesky: {label: 'Bluesky', icon: '🦋'}, hackernews: {label: 'Hacker News', icon: '🟧'}, hn: {label: 'Hacker News', icon: '🟧'}, lobsters: {label: 'Lobsters', icon: '🦞'},
 devto: {label: 'dev.to', icon: '👩‍💻'}, tildes: {label: 'Tildes', icon: '🌲'}, stackexchange: {label: 'Stack Exchange', icon: '📚'}, discourse: {label: 'Discourse', icon: '💬'},
 rss: {label: 'Blog / feed', icon: '📰'}, web: {label: 'Website', icon: '🌐'}
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
export const KINDS: {key: import('$lib/hub/commonroom').Kind; label: string; icon: string}[] = [
 {key: 'humor', label: 'Humor', icon: '😂'}, {key: 'showcase', label: 'Showcase', icon: '🛠'}, {key: 'question', label: 'Questions', icon: '❓'},
 {key: 'discussion', label: 'Discussion', icon: '💬'}, {key: 'news', label: 'News', icon: '📰'}, {key: 'deepdive', label: 'Deep dives', icon: '🔍'},
 {key: 'oddity', label: 'Oddities', icon: '🦄'}
];
export const kindOf = (k: string | undefined) => KINDS.find((x) => x.key === k);
export const MODES: {key: import('$lib/hub/commonroom').Mode; label: string; hint: string}[] = [
 {key: 'today', label: 'Today', hint: 'The best of the day, in one short read'}, {key: 'hot', label: 'Hot', hint: 'Buzzing right now'},
 {key: 'new', label: 'New', hint: 'Everything since your last visit, then deeper picks'}, {key: 'top', label: 'Top', hint: 'Best of the week'},
 {key: 'saved', label: 'Saved', hint: 'Posts you starred'}
];
export const BADGES: Record<string, {label: string; icon: string}> = {
 hot: {label: 'Hot', icon: '🔥'}, rising: {label: 'Rising', icon: '📈'}, fresh: {label: 'Fresh', icon: '🌱'}
};

/** The YouTube video id of a watch/short/youtu.be link, or ''. */
export function youtubeId(url: string | null | undefined): string {
	try {
		const u = new URL(url || '');
		const host = u.hostname.replace(/^(www|m)\./, '');
		let id = '';
		if (host === 'youtu.be') id = u.pathname.slice(1);
		else if (host === 'youtube.com' || host === 'youtube-nocookie.com') id = u.searchParams.get('v') || (u.pathname.match(/^\/(?:shorts|embed|live)\/([^/?#]+)/)?.[1] ?? '');
		return /^[A-Za-z0-9_-]{11}$/.test(id) ? id : '';
	} catch { return ''; }
}
