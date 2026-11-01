<script lang="ts">
	// A swipe feed of the shorts the profile's curators accepted. One video at a time, one player
	// at a time: the previous manifest is destroyed before the next loads, so a long session does
	// not accumulate decoders.
	import { onMount, onDestroy } from 'svelte';
	import { api } from '$lib/api';
	import { toast } from '$lib/stores/toast.svelte';
	import { session } from '$lib/stores/session.svelte';
	import { focusable, nav } from '$lib/nav';
	import { loadShaka } from '$lib/player/shaka';
	import { errMsg } from '$lib/hub/ui';
	import type { Candidate, Video } from '$lib/types';

	type Short = Candidate & { video: Video };

	const MAX_HEIGHT = 720;
	let items = $state<Short[]>([]);
	let index = $state(0);
	let loading = $state(true);
	let error = $state('');
	let nextOffset = $state<number | null>(null);
	let videoEl = $state<HTMLVideoElement>();
	let player: { destroy: () => Promise<void>; load: (u: string) => Promise<void>; configure: (c: unknown) => void } | null = null;
	let needsTap = $state(false);
	let progress = $state(0);
	let busy = $state(false);
	let touchStart: { y: number; x: number } | null = null;

	const current = $derived(items[index] ?? null);
	// Shorts are hidden by default, so an empty feed is usually a setting rather than a bug.
	const hidden = $derived(session.settings?.extras.hide_shorts ?? true);

	async function load(offset = 0) {
		try {
			const page = await api.shorts({ limit: 10, offset });
			items = offset === 0 ? page.items : [...items, ...page.items];
			nextOffset = page.next_offset;
			error = '';
		} catch (e) {
			error = errMsg(e);
		} finally {
			loading = false;
		}
	}

	async function destroyPlayer() {
		if (!player) return;
		const old = player;
		player = null;
		try {
			await old.destroy();
		} catch {
			/* already gone */
		}
	}

	async function play(i: number) {
		const item = items[i];
		if (!item || !videoEl) return;
		await destroyPlayer();
		needsTap = false;
		progress = 0;
		try {
			const shaka = await loadShaka();
			if (!shaka.Player.isBrowserSupported()) throw new Error('This browser cannot play these videos.');
			const p = new shaka.Player();
			await p.attach(videoEl);
			p.configure({
				streaming: { bufferingGoal: 20, rebufferingGoal: 2, bufferBehind: 10 },
				abr: { enabled: true, restrictions: { maxHeight: MAX_HEIGHT } }
			});
			player = p as unknown as typeof player;
			await p.load(api.manifestUrl(item.video.id, MAX_HEIGHT));
			await videoEl.play().catch(() => (needsTap = true));
		} catch (e) {
			// Autoplay refusal is the common case; anything else is worth showing.
			needsTap = true;
			if (!(e instanceof DOMException)) error = errMsg(e);
		}
		prefetchNext();
	}

	function prefetchNext() {
		const next = items[index + 1];
		if (next) void fetch(api.manifestUrl(next.video.id, MAX_HEIGHT), { cache: 'no-store' }).catch(() => {});
	}

	/** Save where the viewer got to, so a finished short leaves the feed. */
	function record() {
		const item = items[index];
		if (!item || !videoEl) return;
		const duration = item.video.duration_s || videoEl.duration || 0;
		const position = videoEl.currentTime || 0;
		api.saveHistory({
			video_id: item.video.id, position_s: position, duration_s: duration,
			category_id: item.category_id, finished: duration > 0 && position >= duration - 1
		}).catch(() => {});
	}

	function go(delta: number) {
		const next = index + delta;
		if (next < 0) return;
		if (next >= items.length) {
			if (nextOffset !== null) void load(nextOffset);
			return;
		}
		record();
		index = next;
		void play(next);
	}

	async function rate(rating: 1 | -1) {
		const item = items[index];
		if (!item || busy) return;
		busy = true;
		try {
			await api.feedback({ video_id: item.video.id, category_id: item.category_id, rating });
			if (rating < 0) {
				// A dislike rejects the candidate, so it leaves the feed: drop it and show the next.
				items = items.filter((_, i) => i !== index);
				index = Math.min(index, Math.max(0, items.length - 1));
				toast.success('Noted — fewer like this');
				void play(index);
			} else {
				toast.success('More like this');
				go(1);
			}
		} catch {
			toast.error('Could not save that.');
		} finally {
			busy = false;
		}
	}

	function onKey(e: KeyboardEvent) {
		if (e.key === 'ArrowDown' || e.key === 'j') { e.preventDefault(); go(1); }
		else if (e.key === 'ArrowUp' || e.key === 'k') { e.preventDefault(); go(-1); }
	}
	// The nav engine listens in the capture phase, so a plain window listener never sees the
	// arrows: register as a raw listener instead.
	let offRaw: (() => void) | null = null;
	function onTouchStart(e: TouchEvent) {
		// A tap on a control is a tap, not a swipe.
		if ((e.target as HTMLElement | null)?.closest('button, a, input, select, textarea')) {
			touchStart = null;
			return;
		}
		const t = e.touches[0];
		touchStart = { y: t.clientY, x: t.clientX };
	}
	function onTouchEnd(e: TouchEvent) {
		if (!touchStart) return;
		const t = e.changedTouches[0];
		const dy = t.clientY - touchStart.y;
		const dx = t.clientX - touchStart.x;
		touchStart = null;
		// Vertical intent only: a sideways drag is not a swipe between videos.
		if (Math.abs(dy) < 40 || Math.abs(dy) < Math.abs(dx)) return;
		go(dy < 0 ? 1 : -1);
	}
	function onTouchCancel() { touchStart = null; }
	function onTime() {
		if (!videoEl) return;
		const d = videoEl.duration || 0;
		progress = d > 0 ? Math.min(1, videoEl.currentTime / d) : 0;
	}
	function onEnded() {
		record();
		go(1);
	}

	onMount(async () => {
		offRaw = nav.onRawKey(onKey);
		await load(0);
		if (items.length) await play(0);
	});
	onDestroy(() => {
		offRaw?.();
		record();
		void destroyPlayer();
	});
</script>

<svelte:head><title>Shorts · WatchCraft</title></svelte:head>

<div class="feed" role="region" aria-label="Shorts feed"
	ontouchstart={onTouchStart} ontouchend={onTouchEnd} ontouchcancel={onTouchCancel}>
	{#if loading}
		<div class="state"><p class="muted">Loading shorts…</p></div>
	{:else if error}
		<div class="state"><p class="err" role="alert">{error}</p><button class="btn primary" use:focusable onclick={() => load(0)}>Try again</button></div>
	{:else if !items.length}
		<div class="state">
			<span class="big" aria-hidden="true">▶</span>
			{#if hidden}
				<h1>Shorts are hidden</h1>
				<p class="muted">"Hide Shorts" is on, so curators skip vertical clips under a minute and this feed stays empty. Turn it off to let them collect shorts.</p>
				<a class="btn primary" href="/settings?tab=extras" use:focusable>Open Settings → Extras</a>
			{:else}
				<h1>No shorts yet</h1>
				<p class="muted">Shorts appear here once a curator accepts some. A playlist also needs a minimum duration that allows clips under a minute.</p>
				<a class="btn primary" href="/curators/new" use:focusable>Create a curator</a>
				<a class="btn ghost" href="/settings?tab=categories" use:focusable>Check playlist durations</a>
			{/if}
		</div>
	{:else if current}
		<video
			bind:this={videoEl}
			class="stage"
			playsinline
			preload="metadata"
			ontimeupdate={onTime}
			onended={onEnded}
			onclick={() => videoEl?.paused && videoEl.play().catch(() => {})}
		></video>

		{#if needsTap}
			<button class="tap" use:focusable onclick={() => videoEl?.play().then(() => (needsTap = false)).catch(() => {})}>
				<span aria-hidden="true">▶</span> Tap to play
			</button>
		{/if}

		<a class="btn round back" href="/" aria-label="Leave shorts" use:focusable={{ group: 'shorts' }}>←</a>

		<div class="bar" aria-hidden="true"><span style:width={`${progress * 100}%`}></span></div>

		<div class="meta">
			<h1>{current.video.title}</h1>
			<p class="small muted">
				{current.video.channel_name || current.video.channel_id}
				{#if current.score !== null} · {current.score}% match{/if}
			</p>
			{#if current.summary}<p class="small why">{current.summary}</p>{/if}
		</div>

		<div class="acts">
			<button class="btn round" aria-label="Not for me" disabled={busy} use:focusable={{ group: 'shorts', onSelect: () => rate(-1) }}>👎</button>
			<button class="btn round" aria-label="More like this" disabled={busy} use:focusable={{ group: 'shorts', onSelect: () => rate(1) }}>👍</button>
			<a class="btn round" href={`/watch/${current.video.id}`} aria-label="Open the full page" use:focusable={{ group: 'shorts' }}>⤢</a>
		</div>

		<p class="hint small faint">Swipe up, or press ↓</p>
	{/if}
</div>

<style>
	.feed { position: fixed; inset: 0; background: #05040c; display: grid; place-items: center; overflow: hidden; touch-action: none; }
	.stage { width: 100%; height: 100%; object-fit: contain; background: #000; }
	.state { display: grid; gap: .8rem; justify-items: center; text-align: center; padding: 2rem; max-width: 34rem; }
	.state h1 { margin: 0; font-size: 1.4rem; }
	.big { font-size: 3rem; color: var(--accent); }
	.err { color: var(--bad); }
	.tap { position: absolute; inset: 0; z-index: 1; display: grid; place-items: center; gap: .5rem; background: rgba(5, 4, 12, .55); color: #fff; border: 0; font-size: 1.1rem; cursor: pointer; }
	.back { position: absolute; top: 1rem; left: 1rem; z-index: 2; }
	.meta, .acts, .hint, .bar { z-index: 2; }
	.bar { position: absolute; top: 0; left: 0; right: 0; height: 3px; background: rgba(255, 255, 255, .15); }
	.bar span { display: block; height: 100%; background: var(--accent); transition: width .2s linear; }
	.meta { position: absolute; left: 0; right: 0; bottom: 5.5rem; padding: 0 1.2rem; display: grid; gap: .25rem; text-shadow: 0 1px 6px rgba(0, 0, 0, .9); }
	.meta h1 { margin: 0; font-size: 1.15rem; line-height: 1.3; color: #fff; }
	.meta .muted { color: rgba(255, 255, 255, .75); }
	.why { color: rgba(255, 255, 255, .8); max-height: 4.5rem; overflow: hidden; }
	.acts { position: absolute; right: 1rem; bottom: 5.5rem; display: grid; gap: .6rem; }
	.btn.round { width: 3rem; height: 3rem; border-radius: 50%; display: grid; place-items: center; font-size: 1.2rem; background: rgba(20, 16, 40, .8); border: 1px solid var(--line-strong); color: #fff; }
	.hint { position: absolute; left: 0; right: 0; bottom: 1rem; text-align: center; color: rgba(255, 255, 255, .5); }
	@media (max-width: 760px) { .meta { bottom: 6.5rem; } .acts { bottom: 6.5rem; } }
</style>
