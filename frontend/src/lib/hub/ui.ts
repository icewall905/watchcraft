// Shared helpers for the automation + knowledge pages.
import type { Curator } from './types';

export type CuratorStatus = 'Active' | 'Paused' | 'Error' | 'Running';
export const statusOf = (c: Pick<Curator, 'enabled' | 'last_status' | 'error'>): CuratorStatus =>
	c.last_status === 'running' || c.last_status === 'queued' ? 'Running' : !c.enabled ? 'Paused' : c.last_status === 'failed' || c.last_status === 'partial' || c.error ? 'Error' : 'Active';

export const scheduleLabel = (h: number) =>
	!h ? 'Manual' : h === 24 ? 'Daily' : h === 168 ? 'Weekly' : h % 24 === 0 ? `Every ${h / 24} days` : `Every ${h}h`;

export const SCHEDULES = [
	{ value: 0, label: 'Manual' },
	{ value: 1, label: 'Every hour' },
	{ value: 3, label: 'Every 3 hours' },
	{ value: 6, label: 'Every 6 hours' },
	{ value: 12, label: 'Every 12 hours' },
	{ value: 24, label: 'Daily' },
	{ value: 168, label: 'Weekly' }
];
export const scheduleOptions = (current: number) =>
	SCHEDULES.some((s) => s.value === current) ? SCHEDULES : [...SCHEDULES, { value: current, label: scheduleLabel(current) }];

export const emptyCurator = (kind: 'watch' | 'research' = 'watch'): import('./types').CuratorInput => ({
	name: '', description: '', icon: '🔭', color: '#7c5cff', kind, tone: 'friendly', instructions: '', queries: [], keywords: [], exclusions: [],
	content_types: [], source_ids: [], interval_hours: 24, enabled: true, ai_enabled: true, min_score: 30, max_per_run: 20,
	collection_id: null, topic_id: null, alert_enabled: false, alert_keywords: [], alert_min_severity: 'high',
	subject: '', subject_type: 'topic', aliases: [], tuned_up: [], tuned_down: []
});

export const toneOptions = [
	{ value: 'professional', label: 'Professional', hint: 'Plain, neutral progress notes.' },
	{ value: 'friendly', label: 'Friendly', hint: 'Warm, everyday language. Default.' },
	{ value: 'playful', label: 'Playful', hint: 'Light and fun — always honest about what really happened.' }
];

export const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));
export const duration = (a: number, b: number | null) => {
	if (!b) return '…';
	const s = Math.max(0, Math.round(b - a));
	return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
};
