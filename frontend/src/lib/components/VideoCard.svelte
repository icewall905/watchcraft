<script lang="ts">
	import type { Snippet } from 'svelte';
	import { focusable, type NavKey } from '$lib/nav';
	import { api } from '$lib/api';
	import { preview } from '$lib/preview';
	import { fmtDuration, fmtCount, timeAgo } from '$lib/util';
	import { warmVideo } from '$lib/prefetch';
	import type { Video } from '$lib/types';
	import { dialogs } from '$lib/stores/dialogs.svelte';

	interface Props {
		video: Video | { id: string; title?: string };
		href?: string;
		score?: number | null;
		summary?: string;
		/** 0..1 watched fraction */
		progress?: number;
		color?: string;
		group?: string;
		id?: string;
		autofocus?: boolean;
		size?: 'row' | 'grid';
		onSelect?: () => void;
		actions?: Snippet;
		badge?: Snippet;
		/** Show the "Save to playlist" action (default on: every video can go to a hand-picked list). */
		save?: boolean;
		/** Nav group for the action strip; pass the one the `actions` buttons use so Left/Right moves between them. */
		actionsGroup?: string;
	}
	let { video, href, score = null, summary = '', progress = 0, color, group, id, autofocus, size = 'row', onSelect, actions, badge, save = true, actionsGroup }: Props = $props();
	const actsGroup = $derived(actionsGroup ?? `card-acts-${group ?? ''}-${(video as { id: string }).id}`);
	const v = $derived(video as Partial<Video> & { id: string });
	const link = $derived(href ?? `/watch/${v.id}`);
	let broken = $state(false);
	let actionsEl = $state<HTMLDivElement | null>(null);
	let hitEl = $state<HTMLElement | null>(null);
	let thumbEl = $state<HTMLElement | null>(null);

	/** The action buttons sit inside the stretched link's rect, so the engine's spatial move
	 *  cannot reach them; Down from the card dives into the strip explicitly. */
	function dive(key: NavKey): boolean {
		if (key === 'down' && actionsEl) {
			const b = actionsEl.querySelector<HTMLElement>('button, a');
			if (b) {
				b.focus({ preventScroll: true });
				b.scrollIntoView({ block: 'nearest' });
				return true;
			}
		}
		return false;
	}
</script>

<div class="card {size}" style:--cat={color ?? 'var(--ai)'}>
	<!-- Stretched link: the whole card is one click target; hover/focus effects key off it.
	     Hovering or keyboard focus arms the silent thumbnail preview. -->
	<a
		class="hit"
		bind:this={hitEl}
		href={link}
		aria-label={v.title ?? v.id}
		onpointerenter={() => warmVideo(v.id, { offline: !!v.offline })}
		use:preview={{ id: v.id, offline: !!v.offline, listenOn: hitEl, host: thumbEl }}
		use:focusable={{ group, id: id ?? `v-${group ?? ''}-${v.id}`, autofocus, onKey: dive, onFocus: () => warmVideo(v.id, { offline: !!v.offline }), onSelect: onSelect ? (e) => (e?.preventDefault(), onSelect()) : undefined }}
	></a>
	<div class="thumb" bind:this={thumbEl}>
		{#if !broken}
			<img src={api.thumb(v.id)} alt="" loading="lazy" decoding="async" onerror={() => (broken = true)} />
		{:else}
			<div class="noimg">▶</div>
		{/if}
		{#if score != null}<span class="score ai">✦ {score}</span>{/if}
		{#if badge}{@render badge()}{/if}
		{#if v.offline}<span class="offline" title="Saved offline">⤓ Offline</span>{/if}
		{#if v.duration_s}<span class="dur">{fmtDuration(v.duration_s)}</span>{/if}
		{#if progress > 0.01}<span class="prog"><span style:width="{Math.min(100, progress * 100)}%"></span></span>{/if}
		{#if summary}<div class="sum"><p>{summary}</p></div>{/if}
	</div>
	{#if actions || save}
		<!-- Small hover actions under the thumbnail; invisible and unclickable until the card is hovered/focused. -->
		<div class="actions" bind:this={actionsEl}>
			{#if actions}{@render actions()}{/if}
			{#if save}<button class="btn why" use:focusable={{ group: actsGroup, onSelect: () => dialogs.openAddTo(v.id, v.title) }}>＋ Save to playlist</button>{/if}
		</div>
	{/if}
	<div class="meta">
		<h3 class="title">{v.title || 'Untitled video'}</h3>
		<p class="sub">
			<span class="ch">{v.channel_name ?? ''}</span>
			{#if v.view_count}<span class="dot">{fmtCount(v.view_count)} views</span>{/if}
			{#if v.published_at}<span class="dot">{timeAgo(v.published_at)}</span>{/if}
		</p>
	</div>
</div>

<style>
	.card {
		position: relative;
		width: 100%;
		min-width: 0;
		display: grid;
		gap: 0.5rem;
		align-content: start;
	}
	.card.row {
		width: var(--card-w);
		flex: none;
	}
	/* Stretched link over the whole card; the visual layers pass clicks through to it. */
	.hit {
		position: absolute;
		inset: 0;
		z-index: 0;
		border-radius: var(--r-md);
	}
	/* app.css keys-modality .focusable:focus sets position:relative; keep the stretched overlay. */
	:global(html[data-modality='keys']) .card .hit:focus {
		box-shadow: none;
		position: absolute;
		z-index: 5;
	}
	.thumb,
	.meta {
		order: 0;
		pointer-events: none;
	}
	.meta {
		order: 2;
	}
	.thumb {
		position: relative;
		aspect-ratio: 16 / 9;
		border-radius: var(--r-md);
		overflow: hidden;
		background: linear-gradient(135deg, color-mix(in srgb, var(--cat) 30%, var(--bg-2)), var(--bg-1));
		box-shadow: 0 10px 30px -14px rgba(0, 0, 0, 0.8);
		transition: box-shadow 0.22s var(--ease);
	}
	:global(html[data-modality='keys']) .hit:focus + .thumb {
		box-shadow:
			0 0 0 3px var(--accent),
			0 0 36px 6px var(--accent-glow),
			0 22px 44px -10px rgba(0, 0, 0, 0.85);
	}
	.hit:hover + .thumb img {
		transform: scale(1.03);
	}
	.thumb img {
		width: 100%;
		height: 100%;
		object-fit: cover;
		transition: transform 0.5s var(--ease);
	}
	.noimg {
		display: grid;
		place-items: center;
		height: 100%;
		font-size: 2rem;
		color: var(--faint);
	}
	.score.ai {
		position: absolute;
		top: 0.55rem;
		left: 0.55rem;
		background: rgba(10, 8, 25, 0.72);
		border-color: color-mix(in srgb, var(--cat) 70%, transparent);
		color: #fff;
		backdrop-filter: blur(6px);
	}
	.offline {
		position: absolute;
		top: 0.55rem;
		right: 0.55rem;
		padding: 0.1rem 0.5rem;
		border-radius: var(--r-pill);
		font-size: 0.72rem;
		font-weight: 700;
		color: #b9f5d8;
		background: rgba(10, 30, 22, 0.8);
		border: 1px solid rgba(61, 220, 151, 0.5);
		backdrop-filter: blur(6px);
	}
	.dur {
		position: absolute;
		right: 0.5rem;
		bottom: 0.5rem;
		z-index: 2; /* above the hover summary overlay */
		padding: 0.1rem 0.45rem;
		border-radius: 0.4rem;
		background: rgba(0, 0, 0, 0.78);
		font-size: 0.78rem;
		font-weight: 700;
		font-variant-numeric: tabular-nums;
		transition: bottom 0.25s var(--ease);
	}
	/* The hover summary covers the lower thumbnail; lift the badge above it so both stay readable. */
	:global(html[data-modality='keys']) .hit:focus + .thumb .dur,
	.hit:hover + .thumb .dur {
		bottom: calc(100% - 2rem);
	}
	:global(html[data-modality='keys']) .hit:focus + .thumb:has(.offline) .dur,
	.hit:hover + .thumb:has(.offline) .dur {
		bottom: calc(100% - 3.6rem); /* below the Offline badge */
	}
	.prog {
		position: absolute;
		left: 0;
		right: 0;
		bottom: 0;
		height: 0.28rem;
		background: rgba(255, 255, 255, 0.25);
	}
	.prog span {
		display: block;
		height: 100%;
		background: var(--accent);
	}
	.sum {
		position: absolute;
		inset: auto 0 0 0;
		padding: 2.2rem 0.85rem 0.75rem;
		background: linear-gradient(180deg, transparent, rgba(8, 6, 22, 0.94) 45%);
		font-size: 0.82rem;
		line-height: 1.4;
		opacity: 0;
		transform: translateY(12px);
		transition: 0.25s var(--ease);
	}
	.sum p {
		display: -webkit-box;
		-webkit-line-clamp: 3;
		line-clamp: 3;
		-webkit-box-orient: vertical;
		overflow: hidden;
	}
	.sum::before {
		content: '✦ Why the AI picked it';
		display: block;
		font-size: 0.72rem;
		font-weight: 700;
		color: var(--accent);
		margin-bottom: 0.2rem;
	}
	:global(html[data-modality='keys']) .hit:focus + .thumb .sum,
	.hit:hover + .thumb .sum {
		opacity: 1;
		transform: none;
	}
	/* The duration badge stays visible on hover/focus: hovering is how the action strip is reached. */
	.meta {
		display: grid;
		gap: 0.25rem;
		padding: 0 0.15rem;
	}
	.title {
		font-size: 1rem;
		font-weight: 650;
		line-height: 1.3;
		letter-spacing: -0.01em;
		display: -webkit-box;
		-webkit-line-clamp: 2;
		line-clamp: 2;
		-webkit-box-orient: vertical;
		overflow: hidden;
		min-height: 2.6em;
	}
	.sub {
		display: flex;
		flex-wrap: wrap;
		gap: 0 0.6rem;
		font-size: 0.84rem;
		color: var(--muted);
	}
	.ch {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		max-width: 100%;
	}
	.dot {
		color: var(--faint);
	}
	.actions {
		order: 1; /* visually right under the thumbnail */
		position: relative;
		z-index: 1; /* above the stretched link, so the buttons can be clicked */
		display: flex;
		gap: 0.35rem;
		flex-wrap: wrap;
		opacity: 0;
		pointer-events: none; /* the strip itself passes clicks through to the card */
		transition: opacity 0.15s var(--ease);
	}
	.card:hover .actions,
	.card:focus-within .actions {
		opacity: 1;
	}
	.actions :global(.btn) {
		min-height: 1.9rem;
		padding: 0.1rem 0.7rem;
		font-size: 0.78rem;
		border-radius: var(--r-sm);
	}
	/* Buttons only accept the pointer once revealed — no accidental clicks on invisible controls. */
	.card:hover .actions :global(.btn),
	.card:focus-within .actions :global(.btn) {
		pointer-events: auto;
	}
	/* Touch has no hover to reveal the strip: show it there, small, always on. */
	@media (hover: none) {
		.actions {
			opacity: 1;
		}
		.actions :global(.btn) {
			pointer-events: auto;
		}
	}
</style>
