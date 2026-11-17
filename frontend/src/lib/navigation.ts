export type CountKey = 'discover_unread' | 'alerts_new' | 'boards_shared_new' | 'commonroom';
export type NavEntry = {
	href: string; label: string; icon: string; countKey?: CountKey;
	active: (pathname: string) => boolean;
};
/** `quiet` sections render smaller and start collapsed until the user opens them. */
export type NavSection = { key: string; title: string; items: NavEntry[]; quiet?: boolean };

const prefix = (href: string) => (p: string) => p === href || p.startsWith(href + '/');
const entry = (href: string, label: string, icon: string, countKey?: CountKey, active = prefix(href)): NavEntry => ({ href, label, icon, countKey, active });

export const TV: NavEntry = entry('/tv', 'The Screen', 'tv', undefined, (p) => p === '/' || p === '/tv' || p.startsWith('/category/') || p.startsWith('/channel/'));
export const DISCOVER = entry('/discover', 'The Finds', 'compass', 'discover_unread');
export const LIBRARY = entry('/library', 'The Stacks', 'library');
export const SHORTS = entry('/shorts', 'The Scroll', 'play');
export const MUSIC = entry('/music', 'The Jukebox', 'music');
export const CINEMA = entry('/jellyfin', 'The Cinema', 'tv');
export const ALERTS = entry('/alerts', 'Alerts', 'alert', 'alerts_new');
export const SETTINGS = entry('/settings', 'Settings', 'settings');

export const SECTIONS: NavSection[] = [
	{ key: 'watch', title: 'Watch', items: [TV, SHORTS, MUSIC, CINEMA, entry('/subscriptions', 'Subscriptions', 'subs')] },
	{ key: 'explore', title: 'Explore', items: [entry('/today', 'The Front Page', 'news'), DISCOVER, entry('/news', 'The Wire', 'news'), entry('/boards', 'The Boards', 'library', 'boards_shared_new'), entry('/common-room', 'The Commons', 'team', 'commonroom', (p) => prefix('/common-room')(p) || prefix('/memes')(p)), LIBRARY, entry('/knowledge', 'The Almanac', 'book'), entry('/briefings', 'The Dispatch', 'briefing'), ALERTS] },
	{ key: 'research', title: 'Research', items: [entry('/research', 'The Study', 'research'), entry('/scout-report', 'The Scout', 'briefing')] },
	{ key: 'workshop', title: 'Workshop', quiet: true, items: [entry('/team', 'Agent Activity', 'team'), entry('/curators', 'Curators', 'curator'), entry('/sources', 'Sources', 'source'), entry('/agent', 'Video AI', 'agent'), entry('/stats', 'Statistics', 'chart')] }
];
/** Persisted collapse keys from before the Automation section was renamed. */
export const LEGACY_SECTION_KEYS: Record<string, string> = { automation: 'workshop' };
export const ALL_ENTRIES: NavEntry[] = [...SECTIONS.flatMap((s) => s.items), SETTINGS];

/** Pages a device may open on. `/` is The Screen (TV home); the rest are sidebar destinations. */
export const LANDING_CHOICES: { href: string; label: string }[] = ALL_ENTRIES
	.map((e) => ({ href: e.href === '/tv' ? '/' : e.href, label: e.label }));
export const landingPath = (saved: string | null | undefined): string =>
	LANDING_CHOICES.some((c) => c.href === saved) ? saved! : '/';
/** The Android app chooses its own start page and loads it directly. */
export const inAndroidApp = (): boolean => typeof navigator !== 'undefined' && navigator.userAgent.includes('WatchCraftAndroid/');

export type DrawerEntry = { href: string; label: string; icon: string; active: (pathname: string, search: string) => boolean };
export const TV_DRAWER: DrawerEntry[] = [
	{ href: '/', label: 'TV Home', icon: 'home', active: (p) => p === '/' || p === '/tv' || p.startsWith('/category/') || p.startsWith('/channel/') },
	{ href: '/search', label: 'Video search', icon: 'search', active: (p) => p.startsWith('/search') },
	{ href: '/subscriptions', label: 'Subscriptions', icon: 'subs', active: (p) => p.startsWith('/subscriptions') },
	{ href: '/music', label: 'The Jukebox', icon: 'music', active: (p) => p === '/music' || p.startsWith('/music/') },
	{ href: '/jellyfin', label: 'The Cinema', icon: 'tv', active: (p) => p === '/jellyfin' || p.startsWith('/jellyfin/') },
	{ href: '/settings?tab=categories', label: 'Playlists', icon: 'list', active: (p, s) => p.startsWith('/settings') && s.includes('tab=categories') },
	{ href: '/today', label: 'Today', icon: 'compass', active: (p) => p === '/today' || p.startsWith('/today/') },
	{ href: '/settings', label: 'Settings', icon: 'settings', active: (p, s) => p.startsWith('/settings') && !s.includes('tab=categories') }
];

export const MOBILE_PRIMARY: NavEntry[] = [TV, entry('/today', 'The Front Page', 'news'), LIBRARY, ALERTS];
export const MOBILE_MORE = { label: 'More', icon: 'more' };
export const FIND = { href: '/find', label: 'Find', icon: 'search' };

export function isHubPath(path: string): boolean {
	return ['/today', '/discover', '/news', '/knowledge', '/library', '/memes', '/boards', '/common-room', '/music', '/briefings', '/alerts', '/research', '/scout-report', '/team', '/curators', '/sources', '/find', '/item']
		.some((p) => path === p || path.startsWith(p + '/'));
}
