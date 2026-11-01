<script lang="ts">
	import { onMount, onDestroy, tick } from 'svelte';
	import { api } from '$lib/api';
	import { session } from '$lib/stores/session.svelte';
	import { toast } from '$lib/stores/toast.svelte';
	import { focusable, nav } from '$lib/nav';
	import { errMsg } from '$lib/hub/ui';
	import type { Candidate, Video, ShortsFeed } from '$lib/types';
	type Short = Candidate & {
		video: Video;
	};
	let feeds = $state<ShortsFeed[]>([]), selected = $state<ShortsFeed | null>(null);
	let items = $state<Short[]>([]), index = $state(0), loading = $state(true), error = $state(''), playbackError = $state('');
	let nextOffset = $state<number | null>(null), videoEl = $state<HTMLVideoElement>(), needsTap = $state(false), progress = $state(0), busy = $state(false);
	let alive = true, generation = 0, navigation = 0, selectionEpoch = 0, activeId: string | null = null, fetching = false;
	let touchStart: {
		y: number;
		x: number;
	} | null = null;
	const current = $derived(items[index] ?? null);
	let observedVersion = session.version;
	$effect(() => {
		const version = session.version;
		if (version !== observedVersion) {
			observedVersion = version;
			reset();
			void loadFeeds();
		}
	});
	function stop() {
		generation++;
		activeId = null;
		if (videoEl) {
			videoEl.pause();
			videoEl.removeAttribute('src');
			videoEl.load();
		}
	}
	function reset() {
		stop();
		navigation++;
		selectionEpoch++;
		selected = null;
		items = [];
		index = 0;
		nextOffset = null;
		busy = false;
		fetching = false;
		error = '';
		playbackError = '';
	}
	async function loadFeeds() {
		loading = true;
		const profile = session.version, epoch = selectionEpoch;
		try {
			const result = await api.shortsFeeds();
			if (alive && profile === session.version && epoch === selectionEpoch) {
				feeds = result;
				error = '';
			}
		}
		catch (e) {
			if (alive && profile === session.version && epoch === selectionEpoch)
				error = errMsg(e);
		}
		finally {
			if (alive && profile === session.version && epoch === selectionEpoch)
				loading = false;
		}
	}
	async function load(append = false) {
		if (!selected || fetching)
			return;
		const feed = selected.id, profile = session.version, epoch = selectionEpoch;
		fetching = true;
		try {
			const page = await api.shorts({ category_id: feed, limit: 10, offset: 0, exclude_ids: append ? items.map(i => i.video.id) : [] });
			if (!alive || selected?.id !== feed || profile !== session.version || epoch !== selectionEpoch)
				return;
			items = append ? [...items, ...page.items.filter(i => !items.some(old => old.video.id === i.video.id))] : page.items;
			nextOffset = page.next_offset;
			error = '';
		}
		catch (e) {
			if (alive && selected?.id === feed && profile === session.version && epoch === selectionEpoch)
				error = errMsg(e);
		}
		finally {
			if (alive && selected?.id === feed && profile === session.version && epoch === selectionEpoch) {
				fetching = false;
				loading = false;
			}
		}
	}
	async function choose(feed: ShortsFeed) {
		reset();
		const epoch = selectionEpoch;
		selected = feed;
		loading = true;
		await load();
		if (alive && epoch === selectionEpoch && selected?.id === feed.id && items.length)
			void play(0);
	}
	async function play(i: number) {
		stop();
		const request = generation, item = items[i];
		needsTap = false;
		playbackError = '';
		progress = 0;
		await tick();
		if (!alive || request !== generation || !item || !videoEl)
			return;
		const el = videoEl;
		activeId = item.video.id;
		el.src = api.fileUrl(item.video.id);
		el.load();
		try {
			await el.play();
		}
		catch {
			if (alive && request === generation)
				needsTap = true;
		}
	}
	function record() {
		const item = current;
		if (!item || !videoEl || activeId !== item.video.id)
			return;
		const duration = videoEl.duration || item.video.duration_s || 0, position = videoEl.currentTime || 0;
		void api.saveHistory({ video_id: item.video.id, category_id: item.category_id, position_s: position, duration_s: duration, finished: duration > 0 && position >= duration - 1 }).catch(() => {
		});
	}
	async function go(delta: number) {
		if (!selected)
			return;
		const action = ++navigation, origin = current?.video.id, next = index + delta;
		if (next < 0)
			return;
		if (next >= items.length) {
			if (nextOffset === null)
				return;
			await load(true);
			if (!alive || action !== navigation || origin !== current?.video.id || next >= items.length)
				return;
		}
		record();
		index = next;
		void play(index);
	}
	async function retry() {
		const epoch = ++selectionEpoch;
		navigation++;
		fetching = false;
		record();
		stop();
		items = [];
		index = 0;
		loading = true;
		await load();
		if (alive && epoch === selectionEpoch && items.length)
			void play(0);
	}
	async function picker() {
		record();
		reset();
		await loadFeeds();
	}
	async function rate(rating: 1 | -1) {
		const item = current, feed = selected?.id, profile = session.version, epoch = selectionEpoch;
		if (!item || busy)
			return;
		busy = true;
		try {
			await api.feedback({ video_id: item.video.id, category_id: item.category_id, rating });
			if (!alive || selected?.id !== feed || profile !== session.version || epoch !== selectionEpoch)
				return;
			if (rating > 0) {
				toast.success('More like this');
				if (current?.video.id === item.video.id)
					void go(1);
			}
			else {
				const retainedId = current?.video.id, wasCurrent = retainedId === item.video.id;
				items = items.filter(i => i.video.id !== item.video.id);
				if (!wasCurrent)
					index = Math.max(0, items.findIndex(i => i.video.id === retainedId));
				if (wasCurrent) {
					stop();
					index = Math.min(index, Math.max(0, items.length - 1));
					if (!items.length && nextOffset !== null)
						await load(true);
					if (alive && selected?.id === feed && profile === session.version && epoch === selectionEpoch)
						void play(index);
				}
				toast.success('Noted — fewer like this');
			}
		}
		catch {
			if (alive && selected?.id === feed && epoch === selectionEpoch)
				toast.error('Could not save that.');
		}
		finally {
			if (alive && selected?.id === feed && profile === session.version && epoch === selectionEpoch)
				busy = false;
		}
	}
	function onKey(e: KeyboardEvent) {
		if (!selected)
			return;
		if (e.key === 'ArrowDown' || e.key === 'j') {
			e.preventDefault();
			void go(1);
		}
		else if (e.key === 'ArrowUp' || e.key === 'k') {
			e.preventDefault();
			void go(-1);
		}
		else if (e.key === 'Escape') {
			e.preventDefault();
			void picker();
		}
	}
	function onTouchStart(e: TouchEvent) {
		if ((e.target as HTMLElement)?.closest('button,a,input,select,textarea')) {
			touchStart = null;
			return;
		}
		const t = e.touches[0];
		touchStart = { y: t.clientY, x: t.clientX };
	}
	function onTouchEnd(e: TouchEvent) {
		if (!touchStart)
			return;
		const t = e.changedTouches[0], dy = t.clientY - touchStart.y, dx = t.clientX - touchStart.x;
		touchStart = null;
		if (Math.abs(dy) > 40 && Math.abs(dy) > Math.abs(dx))
			void go(dy < 0 ? 1 : -1);
	}
	function onTime() {
		if (videoEl && activeId === current?.video.id)
			progress = videoEl.duration > 0 ? Math.min(1, videoEl.currentTime / videoEl.duration) : 0;
	}
	function onEnded() {
		if (activeId === current?.video.id) {
			record();
			void go(1);
		}
	}
	let offRaw: (() => void) | null = null;
	onMount(() => {
		offRaw = nav.onRawKey(onKey);
		void loadFeeds();
	});
	onDestroy(() => {
		record();
		alive = false;
		navigation++;
		stop();
		offRaw?.();
	});
</script>
<svelte:head><title>Shorts · WatchCraft</title></svelte:head>
<div class="feed" role="region" aria-label="Shorts feed" ontouchstart={onTouchStart} ontouchend={onTouchEnd} ontouchcancel={()=>touchStart=null}>
	{#if !selected}
		<div class="state picker"><h1>Choose a feed</h1><p class="muted">Only videos stored locally are played.</p>
		{#if loading}<p>Loading feeds…</p>{:else if error}<p class="err" role="alert">{error}</p><button class="btn" onclick={loadFeeds} use:focusable>Try again</button>{:else}
			{#each feeds as feed (feed.id)}<button class="feed-choice" use:focusable onclick={()=>choose(feed)}><strong>{feed.emoji} {feed.name}</strong><span class="small muted">{feed.ready_count} ready locally · {feed.pending_count} waiting for local files{feed.error_count ? ` · ${feed.error_count} download errors` : ''}{!feed.enabled ? ' · Collection paused' : ''}</span></button>{/each}
			{#if !feeds.length}<p>No feeds yet. Give your curator a brief to start collecting local clips.</p>{/if}
		{/if}
		<a class="btn primary" href="/settings?tab=feeds&new=1" use:focusable>New feed</a><a class="btn" href="/settings?tab=feeds" use:focusable>Manage feeds</a><a class="btn ghost" href="/" use:focusable>Leave Shorts</a></div>
	{:else}
		<button class="btn back" use:focusable onclick={picker}>← Feeds</button>
		{#if loading}<div class="state"><p>Checking local videos…</p></div>
		{:else if !current}<div class="state"><h1>{selected.name}</h1>{#if error}<p role="alert" class="err">{error}</p>{:else}<p>No local clips ready yet.</p>{#if !session.settings?.cache.enabled}<p class="muted">Enable Storage → Offline cache in Settings so accepted clips can download.</p>{/if}{#if !session.settings?.agent.enabled}<p class="muted">Enable the AI agent in Settings to collect new clips.</p>{/if}<p class="muted">{selected.pending_count} waiting for local files{selected.error_count ? `; ${selected.error_count} download errors` : ''}. Refresh once downloads finish.</p>{/if}<button class="btn primary" use:focusable onclick={retry}>Refresh availability</button><a class="btn" href={`/settings?tab=feeds&edit=${selected.id}`} use:focusable>Edit feed</a></div>
		{:else}
		<video bind:this={videoEl} class="stage" playsinline preload="metadata" ontimeupdate={onTime} onended={onEnded} onerror={()=>{if(activeId===current?.video.id)playbackError='This local file is unavailable or cannot be played. Refresh availability to check stored clips.';}} onclick={()=>videoEl?.paused && videoEl.play().catch(()=>{})}></video>
		{#if playbackError}<div class="playback-error" role="alert"><p>{playbackError}</p><button class="btn primary" use:focusable onclick={retry}>Refresh availability</button></div>
		{:else if error}<div class="playback-error" role="alert"><p>{error}</p><button class="btn" use:focusable onclick={()=>go(1)}>Retry next short</button></div>
		{:else if needsTap}<button class="tap" use:focusable onclick={()=>videoEl?.play().then(()=>needsTap=false).catch(()=>{})}>▶ Tap to play</button>{/if}
		<div class="bar" aria-hidden="true"><span style:width={`${progress*100}%`}></span></div>
		<div class="meta"><p class="small muted">{selected.name} · Stored locally</p><h1>{current.video.title}</h1><p class="small muted">{current.video.channel_name||current.video.channel_id}{current.score!==null ? ` · ${current.score}% match` : ''}</p>{#if current.summary}<p class="small why">{current.summary}</p>{/if}</div>
		<div class="acts"><button class="btn round" aria-label="Not for me" disabled={busy} use:focusable={{group:'shorts',onSelect:()=>rate(-1)}}>👎</button><button class="btn round" aria-label="More like this" disabled={busy} use:focusable={{group:'shorts',onSelect:()=>rate(1)}}>👍</button></div>
		<p class="hint small faint">Swipe up, or press ↓</p>
		{/if}
	{/if}
</div>
<style>
	.picker { width: min(100%, 40rem); max-height: 100%; overflow-y:auto; touch-action:pan-y; }.feed-choice {display:grid;gap:.4rem;text-align:left;width:100%;padding:1rem;border:1px solid var(--line);border-radius:var(--r-md);background:rgba(255,255,255,.04);color:inherit;cursor:pointer;}

	.feed { position: fixed; inset: 0; z-index: 140; background: #05040c; display: grid; place-items: center; overflow: hidden; touch-action: none; }
	.stage { width: 100%; height: 100%; object-fit: contain; background: #000; }
	.state { display: grid; gap: .8rem; justify-items: center; text-align: center; padding: 2rem; max-width: 34rem; }
	.state h1 { margin: 0; font-size: 1.4rem; }
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
