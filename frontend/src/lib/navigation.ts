export type CountKey = 'discover_unread' | 'alerts_new';
export type NavEntry = {
	href: string; label: string; icon: string; countKey?: CountKey;
	active: (pathname: string) => boolean;
};
export type NavSection = { key: string; title: string; items: NavEntry[] };

const prefix = (href: string) => (p: string) => p === href || p.startsWith(href + '/');
const entry = (href: string, label: string, icon: string, countKey?: CountKey, active = prefix(href)): NavEntry => ({ href, label, icon, countKey, active });

export const TV: NavEntry = entry('/tv', 'TV', 'tv', undefined, (p) => p === '/' || p === '/tv' || p.startsWith('/category/') || p.startsWith('/channel/'));
export const DISCOVER = entry('/discover', 'Discover', 'compass', 'discover_unread');
export const LIBRARY = entry('/library', 'Library', 'library');
export const ALERTS = entry('/alerts', 'Alerts', 'alert', 'alerts_new');
export const SETTINGS = entry('/settings', 'Settings', 'settings');

export const SECTIONS: NavSection[] = [
	{ key: 'watch', title: 'Watch', items: [TV, entry('/subscriptions', 'Subscriptions', 'subs')] },
	{ key: 'explore', title: 'Explore', items: [DISCOVER, entry('/news', 'News', 'news'), entry('/knowledge', 'Knowledge', 'book'), LIBRARY, entry('/briefings', 'Briefings', 'briefing'), ALERTS] },
	{ key: 'research', title: 'Research', items: [entry('/research', 'Research', 'research'), entry('/scout-report', 'Scout Report', 'briefing')] },
	{ key: 'automation', title: 'Automation', items: [entry('/team', 'Agent Activity', 'team'), entry('/curators', 'Curators', 'curator'), entry('/sources', 'Sources', 'source'), entry('/agent', 'Video AI', 'agent'), entry('/stats', 'Statistics', 'chart')] }
];
export const ALL_ENTRIES: NavEntry[] = [...SECTIONS.flatMap((s) => s.items), SETTINGS];

export type DrawerEntry = { href: string; label: string; icon: string; active: (pathname: string, search: string) => boolean };
export const TV_DRAWER: DrawerEntry[] = [
	{ href: '/', label: 'TV Home', icon: 'home', active: (p) => p === '/' || p === '/tv' || p.startsWith('/category/') || p.startsWith('/channel/') },
	{ href: '/search', label: 'Video search', icon: 'search', active: (p) => p.startsWith('/search') },
	{ href: '/subscriptions', label: 'Subscriptions', icon: 'subs', active: (p) => p.startsWith('/subscriptions') },
	{ href: '/settings?tab=categories', label: 'Playlists', icon: 'list', active: (p, s) => p.startsWith('/settings') && s.includes('tab=categories') },
	{ href: '/discover', label: 'Hub', icon: 'compass', active: (p) => p.startsWith('/discover') },
	{ href: '/settings', label: 'Settings', icon: 'settings', active: (p, s) => p.startsWith('/settings') && !s.includes('tab=categories') }
];

export const MOBILE_PRIMARY: NavEntry[] = [TV, DISCOVER, LIBRARY, ALERTS];
export const MOBILE_MORE = { label: 'More', icon: 'more' };
export const FIND = { href: '/find', label: 'Find', icon: 'search' };

export function isHubPath(path: string): boolean {
	return ['/discover', '/news', '/knowledge', '/library', '/briefings', '/alerts', '/research', '/scout-report', '/team', '/curators', '/sources', '/find', '/item']
		.some((p) => path === p || path.startsWith(p + '/'));
}
