// WatchCraft Hub types. Contract: docs/HUB.md.
export type ItemState = {
	saved: boolean; favorite: boolean; read_later: boolean; archived: boolean; dismissed: boolean;
	read_at: number | null; notes: string; tags: string[]; rating: number;
};
export type Item = {
	id: number; url: string; type: string; title: string; summary: string; author: string; publisher: string;
	image: string; published_at: number | null; first_seen: number; provider: string;
	meta: Record<string, unknown>; enrichment: Record<string, unknown>;
	state: ItemState; score: number | null; reason: string; curator_names: string[];
};
export type ItemDetail = Item & {
	curators: { id: number; name: string; found_at: number; score: number | null; reason: string }[];
	collections: number[]; related: Item[];
};
export type Page<T> = { items: T[]; total: number };
export type TypeInfo = { key: string; label: string; icon: string };
export type ProviderInfo = {
	key: string; label: string; mode: 'feed' | 'query' | 'url'; needs_url: boolean;
	capabilities: string[]; available: boolean; reason: string; description: string;
};
export type Registry = { types: TypeInfo[]; providers: ProviderInfo[] };
export type Counts = { discover_unread: number; alerts_new: number; news_today: number };
export type Collection = { id: number; name: string; description: string; icon: string; color: string; view: string; pinned: boolean; count: number };
export type Source = {
	id: number; provider: string; name: string; url: string; enabled: boolean; last_success: number | null;
	last_error: string; last_checked: number | null; item_count: number; curator_ids: number[];
};
export type ScoutPlan = { id: number; query: string; interval_hours: number; enabled: boolean; next_run: number; created_at: number };
export type ScoutSuggestion = { provider: 'rss' | 'html_listing' | 'sitemap' | 'page_watch'; url: string; name: string; count: number; sample: { title: string; url: string }[]; validated: boolean };
export type ScoutRun = { id: number; query: string; status: 'running' | 'complete' | 'failed'; created_at: number; finished_at: number | null; events: { kind: string; message: string; at: number }[]; suggestions: ScoutSuggestion[]; sitemaps: string[]; error: string };
export type Run = { id: number; started_at: number; finished_at: number | null; status: string; trigger: string; new_items: number; seen_items: number; errors: { source: string; message: string }[] };
export type Curator = {
	id: number; name: string; description: string; icon: string; color: string; kind: 'watch' | 'research';
	instructions: string; queries: string[]; keywords: string[]; exclusions: string[]; content_types: string[];
	source_ids: number[]; interval_hours: number; enabled: boolean; ai_enabled: boolean; min_score: number;
	max_per_run: number; collection_id: number | null; topic_id: number | null; alert_enabled: boolean;
	alert_keywords: string[]; alert_min_severity: string; subject: string; subject_type: string; aliases: string[];
	report: string; report_at: number | null; report_refs: number[]; next_run: number | null; last_run: number | null; last_success: number | null;
	last_status: string; error: string; discovery_count: number; created_at: number;
};
export type CuratorDetail = Curator & { runs: Run[]; recent: Item[] };
export type CuratorInput = Partial<Omit<Curator, 'id' | 'report' | 'report_at' | 'next_run' | 'last_run' | 'last_success' | 'last_status' | 'error' | 'discovery_count' | 'created_at'>> & { name: string };
export type Template = { key: string; name: string; description: string; icon: string; kind: string; queries: string[]; keywords: string[]; provider_keys: string[]; content_types: string[]; interval_hours: number };
export type Topic = { id: number; name: string; description: string; keywords: string[]; notes: string; kind: 'knowledge' | 'news'; muted: boolean; pinned: boolean; count: number };
export type TopicDetail = Topic & { items: Item[]; recent: Item[]; curators: { id: number; name: string }[]; related: Topic[] };
export type Briefing = { id: number; name: string; curator_ids: number[]; topic_ids: number[]; frequency: 'manual' | 'daily' | 'weekly'; length: 'concise' | 'detailed'; last_generated: number | null; next_run: number | null };
export type Issue = { id: number; created_at: number; content: string; item_ids: number[]; ai: boolean };
export type Alert = { id: number; severity: string; state: string; title: string; reason: string; curator_id: number | null; item: Item; created_at: number; snooze_until?: number | null };
export type NewsGroup = { key: string; title: string; items: Item[]; latest: number };
export type SearchResult = Page<Item> & {
	videos: { id: string; title: string; channel_name: string; thumbnail: string }[];
	curators: { id: number; name: string; kind: string }[];
	collections: { id: number; name: string }[];
	topics: { id: number; name: string }[];
};
export type WebResult = { title: string; url: string; snippet: string; provider: string; type: string };
export type SavedSearch = { id: number; name: string; query: string; filters: Record<string, string>; pinned: boolean };

export type TeamStatus = 'running' | 'waiting' | 'idle' | 'attention' | 'paused';
export type TeamAgent = {
	key: string; kind: 'curator' | 'scout' | 'briefing'; id: number | null; name: string; icon: string; color: string; mission: string;
	status: TeamStatus; since: number | null; next_run: number | null; last_run?: number | null; doing?: string | null; query?: string;
	role?: 'watch' | 'research'; error?: string; enabled?: boolean; found_today?: number; plans?: number;
	finds?: { id: number; title: string; found_at: number }[];
	timeline?: { id?: number; step: string; message: string; at: number; detail?: string }[];
	last?: { status: string; new_items?: number; seen_items?: number; suggestions?: number; query?: string; error?: string; finished_at: number | null } | null;
};
export type TeamEntry = {
	at: number; agent: string; name: string; icon: string; kind: 'curator_run' | 'scout_run' | 'briefing'; status: string;
	new_items?: number; seen_items?: number; errors?: number; query?: string; suggestions?: number; title?: string; briefing_id?: number;
};
export type Team = { limit: number; running: number; waiting: number; agents: TeamAgent[]; journal: TeamEntry[] };
