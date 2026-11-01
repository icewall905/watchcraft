<script lang="ts">
	// A swipe feed of the shorts the profile's curators accepted. One video at a time, one player
	// at a time: the previous manifest is destroyed before the next loads, so a long session does
	// not accumulate decoders.
	import { onMount, onDestroy, tick } from 'svelte';
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
	let playbackError = $state('');
	let alive = true;
	let generation = 0;
	let lifecycle = Promise.resolve();
	let fetching: Promise<void> | null = null;
	let activeId: string | null = null;
	let navigation = 0;
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

	async function load(append = false) {
		if (fetching) return fetching;
		fetching = (async () => {
			try {
				const page = await api.shorts({ limit: 10, offset: 0, exclude_ids: append ? items.map((item) => item.video.id) : [] });
				if (!alive) return;
				items = append ? [...items, ...page.items.filter((item) => !items.some((old) => old.video.id === item.video.id))] : page.items;
				nextOffset = page.next_offset;
				error = '';
			} catch (e) {
				if (alive) error = errMsg(e);
			} finally {
				if (alive) loading = false;
				fetching = null;
			}
		})();
		return fetching;
	}

	async function destroyPlayer() {
		const old = player;
		player = null;
		activeId = null;
		if (old) {
			try { await old.destroy(); } catch { /* already gone */ }
		}
	}

	function play(i: number) {
		const item = items[i];
		const request = ++generation;
		needsTap = false;
		playbackError = '';
		progress = 0;
		// Serialize attach/destroy, but let destroy cancel a previous in-flight manifest load.
		lifecycle = lifecycle.then(async () => {
			await destroyPlayer();
			await tick();
			if (!alive || request !== generation || !item || !videoEl) return;
			const el = videoEl;
			try {
				const shaka = await loadShaka();
				if (!alive || request !== generation) return;
				if (!shaka.Player.isBrowserSupported()) throw new Error('This browser cannot play these videos.');
				const p = new shaka.Player();
				const profileId = session.profileId;
				p.getNetworkingEngine()?.registerRequestFilter((_type, request) => {
					if (profileId !== null && request.uris.length && request.uris.every((uri) => {
						const url = new URL(uri, location.href);
						return url.origin === location.origin && /^\/api\/video\/[^/]+\/manifest\.mpd$/.test(url.pathname);
					})) request.headers['X-Profile-Id'] = String(profileId);
				});
				// Publish before attach so every constructed player has a destruction owner.
				player = p as unknown as typeof player;
				await p.attach(el);
				if (!alive || request !== generation) { await destroyPlayer(); return; }
				p.configure({
					streaming: { bufferingGoal: 20, rebufferingGoal: 2, bufferBehind: 10 },
					abr: { enabled: true, restrictions: { maxHeight: MAX_HEIGHT } }
				});
				void (async () => {
					try {
						await p.load(api.manifestUrl(item.video.id, MAX_HEIGHT));
						if (!alive || request !== generation) return;
						activeId = item.video.id;
						await el.play().catch(() => { if (alive && request === generation) needsTap = true; });
						if (alive && request === generation) prefetchNext();
					} catch (e) {
						if (alive && request === generation) playbackError = errMsg(e);
					}
				})();
			} catch (e) {
				if (alive && request === generation) playbackError = errMsg(e);
			}
		}).catch(() => { /* a failed teardown must not poison subsequent requests */ });
		return lifecycle;
	}

	async function retryFeed() {
		await load(false);
		if (alive && items.length) await play(index = 0);
	}

	function prefetchNext() {
		const next = items[index + 1];
		if (next) void fetch(api.manifestUrl(next.video.id, MAX_HEIGHT), { cache: 'no-store', headers: session.profileId === null ? {} : { 'X-Profile-Id': String(session.profileId) } }).catch(() => {});
	}

	/** Save where the viewer got to, so a finished short leaves the feed. */
	function record() {
		const item = items[index];
		if (!item || !videoEl || activeId !== item.video.id) return;
		const duration = item.video.duration_s || videoEl.duration || 0;
		const position = videoEl.currentTime || 0;
		api.saveHistory({
			video_id: item.video.id, position_s: position, duration_s: duration,
			category_id: item.category_id, finished: duration > 0 && position >= duration - 1
		}).catch(() => {});
	}

	async function go(delta: number) {
		const action = ++navigation;
		const origin = current?.video.id;
		const next = index + delta;
		if (next < 0) return;
		if (next >= items.length) {
			if (nextOffset === null) return;
			await load(true);
			if (!alive || action !== navigation || current?.video.id !== origin || next >= items.length) return;
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
			if (!alive) return;
			if (rating < 0) {
				const selected = current?.video.id;
				items = items.filter((entry) => entry.video.id !== item.video.id);
				if (!items.length) {
					// Tear down immediately, including while a replacement page is in flight.
					void play(0);
					if (nextOffset !== null) await load(true);
					if (!alive) return;
				}
				const retained = items.findIndex((entry) => entry.video.id === selected);
				index = retained >= 0 ? retained : Math.min(index, Math.max(0, items.length - 1));
				toast.success('Noted — fewer like this');
				if (selected === item.video.id || !items.length) {
					navigation++;
					void play(index);
				}
			} else {
				toast.success('More like this');
				if (current?.video.id === item.video.id) void go(1);
			}
		} catch {
			if (alive) toast.error('Could not save that.');
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
		if (!videoEl || activeId !== current?.video.id) return;
		const d = videoEl.duration || 0;
		progress = d > 0 ? Math.min(1, videoEl.currentTime / d) : 0;
	}
	function onEnded() {
		if (!current || activeId !== current.video.id) return;
		record();
		go(1);
	}

	onMount(async () => {
		offRaw = nav.onRawKey(onKey);
		await load(false);
		await tick();
		if (alive && items.length) await play(0);
	});
	onDestroy(() => {
		offRaw?.();
		record();
		alive = false;
		generation++;
		navigation++;
		lifecycle = lifecycle.then(destroyPlayer);
	});
</script>

<svelte:head><title>Shorts · WatchCraft</title></svelte:head>

<div class="feed" role="region" aria-label="Shorts feed"
	ontouchstart={onTouchStart} ontouchend={onTouchEnd} ontouchcancel={onTouchCancel}>
	{#if loading}
		<div class="state"><p class="muted">Loading shorts…</p></div>
	{:else if error && !items.length}
		<div class="state"><p class="err" role="alert">{error}</p><button class="btn primary" use:focusable onclick={retryFeed}>Try again</button></div>
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

		{#if playbackError}
			<div class="playback-error" role="alert"><p>{playbackError}</p><button class="btn primary" use:focusable onclick={() => play(index)}>Retry playback</button></div>
		{:else if error}
			<div class="playback-error" role="alert"><p>{error}</p><button class="btn primary" use:focusable onclick={() => go(1)}>Retry next short</button></div>
		{/if}
		{#if needsTap && !playbackError}
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
	.feed { position: fixed; inset: 0; z-index: 140; background: #05040c; display: grid; place-items: center; overflow: hidden; touch-action: none; }
	.stage { width: 100%; height: 100%; object-fit: contain; background: #000; }
	.state { display: grid; gap: .8rem; justify-items: center; text-align: center; padding: 2rem; max-width: 34rem; }
	.state h1 { margin: 0; font-size: 1.4rem; }
	.big { font-size: 3rem; color: var(--accent); }
	.err { color: var(--bad); }
	.playback-error { position: absolute; z-index: 3; top: 20%; padding: 1rem; max-width: 90%; background: #201528; color: #fff; text-align: center; }
	.tap { position: absolute; inset: 0; z-index: 1; display: grid; place-items: center; gap: .5rem; background: rgba(5, 4, 12, .55); color: #fff; border: 0; font-size: 1.1rem; cursor: pointer; }
	.back { position: absolute; top: 1rem; left: 1rem; z-index: 2; }
	:global(html[data-modality='keys']) .back:focus { position: absolute; z-index: 2; }
	:global(html[data-modality='keys']) .tap:focus { position: absolute; z-index: 1; }
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
