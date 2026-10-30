// Shapes returned by the WatchCraft backend (see docs/CONTRACT.md, backend/bt/api/*.py).

export interface Profile {
	id: number;
	name: string;
	emoji: string;
	color: string;
	prefs: Record<string, unknown>;
	created_at?: string;
	pin_required?: boolean;
	pin_locked?: boolean;
}

export interface CategoryStats {
	found?: number;
	enriched?: number;
	accepted?: number;
	rejected?: number;
	error?: number;
	[k: string]: number | undefined;
}

export interface Category {
	id: number;
	profile_id: number;
	name: string;
	emoji: string;
	color: string;
	brief: string;
	seed_queries: string[];
	seed_channels: string[];
	include_keywords: string[];
	exclude_keywords: string[];
	min_duration_s: number;
	max_duration_s: number;
	language: string;
	max_age_days: number;
	target_queue: number;
	threshold: number;
	priority: number;
	kid_safe: boolean;
	enabled: boolean;
	position: number;
	created_at?: string;
	last_worked_at?: string | null;
	stats?: CategoryStats;
}

export type CategoryInput = Omit<Category, 'id' | 'profile_id' | 'position' | 'created_at' | 'last_worked_at' | 'stats'>;

export interface Chapter {
	start_time: number;
	end_time?: number;
	title: string;
}

export interface Video {
	id: string;
	title: string;
	channel_id: string;
	channel_name: string;
	duration_s: number;
	published_at: string | null;
	view_count: number;
	like_count: number;
	thumbnail: string;
	is_short: boolean;
	is_live: boolean;
	/** A finished local copy exists (Ticket D cache). */
	offline?: boolean;
	has_transcript?: boolean;
	comment_count?: number;
	description?: string;
	tags?: string[];
	chapters?: Chapter[];
}

export interface VideoDetail extends Video {
	channel?: { id: string; name: string; avatar?: string; subscribed: boolean; blocked: boolean };
	dislikes?: { likes?: number; dislikes?: number; rating?: number; viewCount?: number } | null;
	dearrow?: { title?: string | null; thumb?: string | null } | null;
	in_categories?: { category_id: number; candidate_id: number; status: CandidateStatus; score: number | null; summary: string; reasons: string[] }[];
	has_captions?: boolean;
	history?: { position_s: number; finished: boolean } | null;
	download?: DownloadSummary | null;
}

export type CandidateStatus = 'found' | 'enriched' | 'accepted' | 'rejected' | 'error';

export interface Candidate {
	id: number;
	category_id: number;
	status: CandidateStatus;
	source: string;
	score: number | null;
	summary: string;
	reasons: string[];
	red_flags: string[];
	tags: string[];
	error: string;
	created_at: string;
	judged_at: string | null;
	video: Video | { id: string };
}

export interface CandidateDetail extends Candidate {
	profile_id?: number | null;
	category_name?: string | null;
	profile_name?: string | null;
	transcript_excerpt?: string;
	comments?: { author: string; text: string; likes: number }[];
	events?: AgentEvent[];
}

export interface HomeItem {
	video?: Video;
	position_s?: number;
	category_id?: number | null;
	// candidate fields (category rows)
	id?: number;
	score?: number | null;
	summary?: string;
	reasons?: string[];
	status?: CandidateStatus;
}

export interface HomeRow {
	kind: 'continue' | 'category' | 'subscriptions';
	title: string;
	emoji: string;
	color?: string;
	category_id?: number;
	items: HomeItem[];
}

export interface ChannelEntry {
	id: string;
	name: string;
	handle: string;
	avatar: string;
	subscribed: boolean;
	blocked: boolean;
	trust: number;
}

export interface ChannelInfo {
	id: string;
	name: string;
	handle?: string;
	avatar?: string;
	avatar_src?: string;
	subscribed?: boolean;
	blocked?: boolean;
	description?: string;
	subscriber_count?: number;
}

export interface Feedback {
	id: number;
	video_id: string;
	category_id: number | null;
	rating: number;
	chips: string[];
	text: string;
	processed: boolean;
	created_at: string;
	video?: Video | { id: string };
}

export interface Lesson {
	evidence?: { kind: string; support: number; opposition: number };
	id: number;
	profile_id: number;
	category_id: number | null;
	channel_id: string | null;
	text: string;
	weight: number;
	active: boolean;
	created_at: string;
	updated_at: string;
}

export interface HistoryEntry {
	video_id: string;
	position_s: number;
	duration_s?: number;
	finished: boolean;
	updated_at?: string;
	category_id?: number | null;
}

export type AgentStateName =
	| 'offline'
	| 'idle'
	| 'paused'
	| 'waiting'
	| 'sleeping'
	| 'backoff'
	| 'planning'
	| 'searching'
	| 'fetching'
	| 'transcript'
	| 'comments'
	| 'judging'
	| 'learning'
	| 'error';

export interface AgentState {
	state: AgentStateName | string;
	detail?: string;
	until?: string | null;
	until_end?: string | null;
	category_id?: number | null;
	video_id?: string | null;
	title?: string | null;
	category_name?: string | null;
	backoff_n?: number;
	backoff_until?: string | null;
	llm_backoff_until?: string | null;
	query?: string;
	updated_at?: string;
	[k: string]: unknown;
}

export interface AgentControl {
	paused: boolean;
	run_now: unknown;
	playing_until: string | null;
	backoff_reset?: boolean;
}

export interface AgentBudget {
	hour: number;
	day: number;
	max_hour: number;
	max_day: number;
}

export interface AgentQueue {
	category_id: number;
	name: string;
	emoji: string;
	color: string;
	enabled: boolean;
	target: number;
	last_worked_at: string | null;
	accepted: number;
	pending: number;
	rejected: number;
}

export interface AgentStatus {
	state: AgentState;
	downloader?: DownloaderState;
	control: AgentControl;
	budget: AgentBudget;
	queues?: AgentQueue[];
}

export interface AgentEvent {
	id: number;
	ts: string;
	level: 'debug' | 'info' | 'warn' | 'error' | 'success' | string;
	kind: string;
	message: string;
	profile_id: number | null;
	category_id: number | null;
	video_id: string | null;
	payload: Record<string, unknown>;
}

export type DownloadStatus = 'queued' | 'downloading' | 'done' | 'error' | 'evicted';

export interface DownloadSummary {
	status: DownloadStatus;
	progress: number;
	height: number;
	size_bytes: number;
	pinned: boolean;
}

export interface DownloadItem extends DownloadSummary {
	video_id: string;
	path?: string;
	vcodec?: string;
	error?: string;
	attempts?: number;
	requested_by_profile?: number | null;
	created_at?: string;
	finished_at?: string | null;
	last_played_at?: string | null;
	video?: Video | { id: string };
	title?: string;
}

export interface DownloaderState {
	state: 'offline' | 'idle' | 'downloading' | 'sleeping' | 'full' | 'paused' | 'disabled' | 'backoff' | string;
	detail?: string;
	video_id?: string | null;
	title?: string;
	progress?: number;
	/** bytes per second */
	speed?: number | null;
	/** seconds */
	eta?: number | null;
	used_bytes?: number;
	max_bytes?: number;
	queue?: number;
	updated_at?: string;
}

export interface DownloadsResponse {
	items: DownloadItem[];
	reencode?: { queued: number; failed: number };
	totals: { used_bytes: number; max_bytes: number; counts?: Record<string, number> };
	downloader: DownloaderState | null;
}

export type SbAction = 'skip' | 'ask' | 'show' | 'off';

export interface Settings {
	setup?: { completed: boolean };
	llm: { base_url: string; model: string; api_key: string; timeout_s: number; vision: boolean; temperature: number };
	throttle: {
		min_delay_s: number;
		max_delay_s: number;
		max_per_hour: number;
		max_per_day: number;
		backoff_min_s: number;
		backoff_max_s: number;
		pause_while_playing: boolean;
		quiet_hours: string;
		proxy: string;
		search_results: number;
		max_comments: number;
		transcript_max_chars: number;
	};
	agent: { enabled: boolean };
	appearance: { theme: string };
	memory: { enabled: boolean; time: string; timezone: string; max_core_tokens: number };
	player: { max_height: number; prefer_codec: string; autoplay_next: boolean; default_speed: number; captions_lang: string };
	sponsorblock: { enabled: boolean; categories: Record<string, SbAction>; min_segment_s: number };
	extras: { dearrow: boolean; dearrow_thumbnails: boolean; ryd: boolean; hide_shorts: boolean };
	cache: { enabled: boolean; auto: 'accepted' | 'manual'; max_gb: number; max_height: number; rate_limit: number; min_gap_s: number; hw_transcode: boolean };
	cookies?: { present: boolean };
}

export interface SbSegment {
	segment: [number, number];
	category: string;
	actionType: string;
	UUID: string;
}

export interface CaptionTrack {
	lang: string;
	key: string;
	name: string;
	auto: boolean;
	url: string;
}

export interface LlmTestResult {
	ok: boolean;
	models?: string[];
	reply?: string;
	error?: string;
}

export interface ProxyRegion {
	id: string;
	name: string;
}

export interface ProxyProvider {
	id: string;
	label: string;
	hint?: string;
	scheme?: string;
	schemes?: string[];
	port?: number;
	default_port?: number;
	host?: string;
	regions?: ProxyRegion[];
}

export interface ProxyTestResult {
	ok: boolean;
	ms?: number;
	detail?: string;
	error?: string;
	hint?: string;
}

export interface CookieTestResult {
	present: boolean;
	logged_in?: boolean;
	history_items?: number;
	error?: string;
	blocked?: boolean;
}

export interface ImportSubscriptionsResult {
	imported: number;
	already_known: number;
	total: number;
}

export interface ImportFeedResult {
	target_queue: number;
	category_id: number;
	created: boolean;
	brief: string;
	found: number;
	added: number;
	skipped: number;
	skip_reasons: Record<string, number>;
	category: string;
}

export interface StatsCategoryRow {
	profile_id: number;
	profile_name: string;
	id: number;
	name: string;
	emoji: string;
	color: string;
	enabled: boolean;
	target: number;
	threshold: number;
	avg_score: number | null;
	watch_seconds: number;
	counts: {
		found: number;
		enriched: number;
		accepted: number;
		in_queue: number;
		rejected_ai: number;
		rejected_family: number;
		dismissed: number;
		error: number;
		watched: number;
	};
}

export interface StatsDay {
	date: string;
	found: number;
	accepted: number;
	rejected_ai: number;
	rejected_family: number;
	feedback: number;
	watched: number;
	watched_by_category: Record<string, number>;
	downloads: number;
	yt_requests: number;
}

export interface StatsPageData {
	context: LlmContextStats;
	decisions: Record<string, number>;
	library: {
		duration_s: number; duration_known: number; duration_unknown: number;
		average_duration_s: number; average_size_bytes: number; channels: number;
		quality: { label: string; count: number }[];
		longest: { id: string; title: string; duration_s: number } | null;
	};
	reencode: {
		queued: number; kept_not_smaller: number; job_errors: number; encoded_copies: number;
		recorded_saved: number; recorded_not_smaller: number; recorded_failed: number;
		original_bytes: number; saved_bytes: number;
	};
	overview: {
		videos: number;
		candidates: {
			waiting_judge: number;
			accepted: number;
			in_queue: number;
			rejected_ai: number;
			rejected_family: number;
			dismissed: number;
			error: number;
		};
		downloads: Record<string, number> & { waiting?: number };
		storage_bytes: number;
		feedback: { love: number; like: number; note: number; dislike: number };
		lessons: { active: number; inactive: number };
		watched: number;
		watch_seconds: number;
		channels: { subscribed: number; blocked: number };
		budget: { hour: number; day: number; max_hour: number; max_day: number };
	};
	categories: StatsCategoryRow[];
	daily: StatsDay[];
}

export interface ContextSummary {
	requests: number; total_input_tokens: number; average_input_tokens: number;
	median_input_tokens: number; p95_input_tokens: number; max_input_tokens: number;
	cache_hit_percent: number | null;
}
export interface LlmContextStats extends ContextSummary {
	failed: number; estimated_requests: number; measured_requests: number; cached_tokens: number;
	average_core_chars: number; average_correction_chars: number; average_other_chars: number; window_hours: number;
	by_task: (ContextSummary & { task: string })[];
	by_model: (ContextSummary & { model: string })[];
	hourly: (ContextSummary & { hour: string })[];
	distribution: { label: string; requests: number }[];
	memory_profiles: { profile_id: number; profile: string; core_valid: boolean; core_tokens_estimate: number;
		stored_core_tokens_estimate: number; core_budget_tokens: number | null; raw_rule_tokens_estimate: number;
		correction_tokens_estimate: number; active_rules: number; corrections: number }[];
	rule_strengths: Record<string, number>; revisions_24h: number; configured_core_budget_tokens: number;
}

export interface MemoryStatus {
	run: { state: string; started_at?: string; finished_at?: string; error?: string;
		profiles?: { profile_id: number; profile?: string; status: string; lessons?: number; before_chars?: number; core_chars?: number }[] };
	core: string; core_valid: boolean; core_updated_at: string | null;
	core_budget_tokens: number | null;
	versions: number; active_rules: number; corrections: number; context: LlmContextStats;
}

export interface StatsExploreFilter {
	metric: string; category_id?: number; day?: string; height?: number;
}

export interface StatsExploreItem {
	key: string; video_id: string | null; channel_id: string | null; title: string; channel_name: string; duration_s: number;
	offline: boolean; candidate_id: number | null; category_id: number | null;
	category_name: string; profile_id: number | null; profile_name: string; detail: string;
	score: number | null; size_bytes: number; height: number; rating: number | null;
	original_bytes: number; output_bytes: number;
}

export interface StatsExploreData {
	items: StatsExploreItem[]; total: number; offset: number; limit: number;
}
