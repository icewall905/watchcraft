<script lang="ts">
	import { goto } from '$app/navigation';
	import { api } from '$lib/api';
	import { preview } from '$lib/preview';
	import { dialogs } from '$lib/stores/dialogs.svelte';
	import { session } from '$lib/stores/session.svelte';
	import { toast } from '$lib/stores/toast.svelte';
	import { focusable, scrollByKeys } from '$lib/nav';
	import { fmtDuration, fmtCount, timeAgo, isVideo } from '$lib/util';
	import type { CandidateDetail } from '$lib/types';
	import Modal from './Modal.svelte';
	import OfflineButton from './OfflineButton.svelte';

	let data = $state<CandidateDetail | null>(null);
	let failed = $state(false);
	let accepting = $state(false);
	const close = () => (dialogs.candidate = null);

	async function load(id: number) {
		data = null;
		failed = false;
		try {
			const d = await api.candidate(id);
			if ('status' in d) data = d;
			else failed = true;
		} catch {
			failed = true;
		}
	}

	$effect(() => {
		const id = dialogs.candidate;
		if (id != null) load(id);
	});

	const v = $derived(data && isVideo(data.video) ? data.video : null);
	const cat = $derived(session.categoryById(data?.category_id));
	const STATUS: Record<string, string> = { found: 'Found, waiting for details', enriched: 'Read, waiting for a verdict', accepted: 'Picked', rejected: 'Rejected', error: 'Failed' };

	async function accept() {
		if (!data || accepting) return;
		if (!data.profile_id) {
			toast.error('The original playlist no longer exists.');
			return;
		}
		accepting = true;
		try {
			const result = await api.acceptCandidate(data.id, data.profile_id);
			toast.success(result.learn_from_override ? 'Added. The AI will learn from your approval.' : 'Added to the playlist');
			close();
		} catch {
			// The API client shows the error; keep the verdict open for retry.
		} finally {
			accepting = false;
		}
	}
	async function dismiss() {
		if (!data) return;
		await api.dismissCandidate(data.id);
		toast.success('Removed from the playlist');
		close();
	}
	async function play() {
		const target = data;
		if (!target) return;
		if (target.profile_id && target.profile_id !== session.profileId) await session.switchProfile(target.profile_id);
		close();
		goto(`/watch/${target.video.id}?c=${target.category_id}`);
	}

	// Shape the AI's future picks: feedback rides the normal pipeline (lessons, channel trust),
	// and a dislike also flips this candidate to rejected so the judge/planner see it with the reason.
	function openFeedback() {
		if (!data) return;
		const id = data.id;
		dialogs.openFeedback({
			videoId: data.video.id,
			categoryId: data.category_id,
			title: v?.title,
			heading: 'Feedback on this pick',
			onDone: (rating) => {
				if (rating < 0) close();
				else load(id);
			}
		});
	}
</script>

<Modal size="drawer" title={v?.title ?? 'Candidate'} subtitle={data?.category_name ? `${data.profile_name ?? ''} · ${data.category_name}` : cat ? `${cat.emoji} ${cat.name}` : undefined} onClose={close}>
	{#if failed}
		<p class="muted">This candidate could not be loaded. It may have been removed.</p>
	{:else if !data}
		<div class="skeleton" style="aspect-ratio:16/9"></div>
		<div class="skeleton" style="height:6rem;margin-top:1rem"></div>
	{:else}
		<div class="stack">
			<div class="thumb" use:preview={{ id: data.video.id, offline: !!v?.offline }}>
				<img src={api.thumb(data.video.id)} alt="" />
				{#if data.score != null}<span class="big-score" class:low={data.status === 'rejected'}>{data.score}</span>{/if}
			</div>
			<div class="hstack small muted">
				<span class="status {data.status}">{STATUS[data.status] ?? data.status}</span>
				{#if v}<span>{v.channel_name}</span><span>{fmtDuration(v.duration_s)}</span><span>{fmtCount(v.view_count)} views</span>{/if}
				{#if data.source}<span class="faint">via {data.source}</span>{/if}
			</div>
			{#if data.summary}
				<div class="sum"><span class="k">✦ AI summary</span><p>{data.summary}</p></div>
			{/if}
			{#if data.reasons?.length}
				<div>
					<h3>Why it fits</h3>
					<ul class="list ok">{#each data.reasons as r}<li>{r}</li>{/each}</ul>
				</div>
			{/if}
			{#if data.red_flags?.length}
				<div>
					<h3>Red flags</h3>
					<ul class="list bad">{#each data.red_flags as r}<li>{r}</li>{/each}</ul>
				</div>
			{/if}
			{#if data.error}<p class="small" style="color:var(--bad)">Error: {data.error}</p>{/if}
			{#if data.tags?.length}<div class="hstack">{#each data.tags as t}<span class="chip">{t}</span>{/each}</div>{/if}
			{#if data.transcript_excerpt}
				<div>
					<h3>Transcript excerpt</h3>
					<p class="transcript small" aria-label="Transcript excerpt" use:focusable={{ scroll: 'nearest', onKey: scrollByKeys }}>{data.transcript_excerpt}…</p>
				</div>
			{/if}
			{#if data.comments?.length}
				<div>
					<h3>Top comments the AI read</h3>
					<ul class="comments">
						{#each data.comments as c}
							<li><span class="small faint">{c.author} · 👍 {fmtCount(c.likes)}</span><p class="small">{c.text}</p></li>
						{/each}
					</ul>
				</div>
			{/if}
			{#if data.events?.length}
				<div>
					<h3>What the AI did</h3>
					<ul class="events small">
						{#each data.events as e}<li><span class="faint">{timeAgo(e.ts)}</span> {e.message}</li>{/each}
					</ul>
				</div>
			{/if}
		</div>
	{/if}
	{#snippet footer()}
		{#if data}
			<button class="btn primary" use:focusable={{ autofocus: true, onSelect: play }}>▶ Play</button>
			<OfflineButton videoId={data.video.id} offline={!!v?.offline} status={v?.download_status ?? null} size="normal" />
			{#if data.status === 'accepted'}
				<button class="btn" use:focusable={{ onSelect: dismiss }}>Remove from playlist</button>
			{:else}
				<button class="btn" disabled={accepting} use:focusable={{ onSelect: accept }}>{accepting ? 'Adding…' : 'Add anyway'}</button>
			{/if}
			<button class="btn" title="Tell the AI what to pick instead" use:focusable={{ onSelect: openFeedback }}>💬 Feedback</button>
		{/if}
		<button class="btn ghost" use:focusable={{ onSelect: close }}>Close</button>
	{/snippet}
</Modal>

<style>
	.thumb {
		position: relative;
		border-radius: var(--r-md);
		overflow: hidden;
		aspect-ratio: 16/9;
	}
	.thumb img {
		width: 100%;
		height: 100%;
		object-fit: cover;
	}
	.big-score {
		position: absolute;
		right: 0.8rem;
		top: 0.8rem;
		font-size: 1.8rem;
		font-weight: 800;
		padding: 0.2rem 0.8rem;
		border-radius: var(--r-md);
		background: rgba(10, 8, 25, 0.8);
		color: var(--ok);
	}
	.big-score.low {
		color: var(--bad);
	}
	.status {
		font-weight: 700;
		color: var(--text);
	}
	.status.accepted {
		color: var(--ok);
	}
	.status.rejected {
		color: var(--bad);
	}
	.sum {
		padding: 0.9rem 1rem;
		border-radius: var(--r-md);
		background: rgba(157, 134, 255, 0.12);
		border: 1px solid rgba(157, 134, 255, 0.3);
		display: grid;
		gap: 0.3rem;
	}
	.k {
		font-size: 0.78rem;
		font-weight: 700;
		color: var(--accent);
	}
	h3 {
		font-size: 0.95rem;
		margin-bottom: 0.4rem;
		color: var(--muted);
	}
	.list {
		margin: 0;
		padding: 0;
		list-style: none;
		display: grid;
		gap: 0.35rem;
	}
	.list li {
		padding-left: 1.6rem;
		position: relative;
	}
	.list.ok li::before {
		content: '✓';
		position: absolute;
		left: 0.2rem;
		color: var(--ok);
		font-weight: 800;
	}
	.list.bad li::before {
		content: '⚠';
		position: absolute;
		left: 0.1rem;
		color: var(--bad);
	}
	.transcript {
		--focus-scale: 1;
		max-height: 12rem;
		overflow: auto;
		padding: 0.8rem;
		border-radius: var(--r-md);
		background: rgba(0, 0, 0, 0.25);
		color: var(--muted);
		white-space: pre-wrap;
	}
	.comments,
	.events {
		margin: 0;
		padding: 0;
		list-style: none;
		display: grid;
		gap: 0.6rem;
	}
	.comments li {
		padding: 0.6rem 0.8rem;
		border-radius: var(--r-md);
		background: rgba(255, 255, 255, 0.04);
	}
</style>
