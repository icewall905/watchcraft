<script module lang="ts">
	// Memory only: never persist private feed names or briefs to browser storage.
	let feedCache: { profileId: number; version: number; savedAt: number; feeds: ShortsFeed[] } | null = null;
	const FEED_CACHE_MS = 5 * 60 * 1000;
</script>
<script lang="ts">
	import { onMount, onDestroy, tick } from 'svelte';
	import { api, profileHeaders } from '$lib/api';
	import { parseReelCaptions, type ReelCue } from '$lib/reelCaptions';
	import { session } from '$lib/stores/session.svelte';
	import { deviceMode } from '$lib/stores/deviceMode.svelte';
	import { toast } from '$lib/stores/toast.svelte';
	import { focusable, nav } from '$lib/nav';
	import { errMsg } from '$lib/hub/ui';
	import type { Candidate, Video, ShortsFeed, SubtitleStatus } from '$lib/types';
	type Short = Candidate & {
		video: Video & { subtitles?: SubtitleStatus };
	};
	// Read-ahead: parsed captions and a warmed file for the next clips, so a swipe starts at once.
	const READ_AHEAD = 2, CUE_CACHE = 8;
	let cueCache = new Map<string, Promise<ReelCue[] | null>>();
	let preloader: HTMLVideoElement | null = null, pendingLoad: Promise<void> | null = null;
	let feeds = $state<ShortsFeed[]>([]), selected = $state<ShortsFeed | null>(null);
	let switchingProfile = $state(false);
	let items = $state<Short[]>([]), index = $state(0), loading = $state(true), error = $state(''), playbackError = $state('');
	let nextOffset = $state<number | null>(null), videoEl = $state<HTMLVideoElement>(), needsTap = $state(false), progress = $state(0), busy = $state(false);
	let showControls = $state(false), muted = $state(true), paused = $state(true);
	let duration = $state(0), captionsVisible = $state(true);
	let controlsTimer: ReturnType<typeof setTimeout> | undefined;
	const CONTROLS_IDLE_MS = 4000;
 let captionState = $state<'waiting' | 'ready' | 'failed'>('waiting'), captionMessage = $state('Preparing subtitles…');
 let captionText = $state(''), cues: ReelCue[] = [], captionAbort: AbortController | null = null;
 let captionTimer: ReturnType<typeof setTimeout> | null = null;

	let alive = true, generation = 0, navigation = 0, selectionEpoch = 0, activeId: string | null = null;
	let feedRequest = 0, activeFeedRequest: number | null = null;
	let feedsLoaded = false;
	let feedsProvisional = $state(false), selectedProvisional = $state(false);
	let touchStart: {
		y: number;
		x: number;
	} | null = null;
	const current = $derived(items[index] ?? null);
	let observedVersion = session.version, observedProfileId = session.profileId;
	$effect(() => {
		const version = session.version, profileId = session.profileId;
		if (version !== observedVersion || profileId !== observedProfileId) {
			observedVersion = version;
			observedProfileId = profileId;
			reset();
			feeds = [];
			feedsLoaded = false;
			void loadFeeds();
		}
	});
	$effect(() => {
		if (session.profile?.pin_locked || session.needsLogin) {
			feedCache = null;
			reset();
			feeds = [];
			feedsLoaded = false;
			loading = false;
		}
	});
	function stop() {
		hideControls();
  captionAbort?.abort(); captionAbort = null;
  if (captionTimer) clearTimeout(captionTimer);
  captionTimer = null; cues = []; captionText = ''; captionState = 'waiting';
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
		feedRequest++;
		selected = null;
		selectedProvisional = false;
		items = [];
		index = 0;
		nextOffset = null;
		busy = false;
		pendingLoad = null;
		cueCache = new Map();
		if (preloader) { preloader.removeAttribute('src'); preloader.load(); }
		error = '';
		playbackError = '';
	}
	async function loadFeeds(background = false) {
		if (background && activeFeedRequest === feedRequest) return;
		const profileId = session.profileId;
		if (profileId == null || session.profile?.pin_locked || session.needsLogin) {
			feedCache = null;
			feeds = [];
			feedsLoaded = false;
			loading = false;
			return;
		}
		if (!feedsLoaded && feedCache?.profileId === profileId && feedCache.version === session.version && Date.now() - feedCache.savedAt < FEED_CACHE_MS) {
			feeds = feedCache.feeds;
			feedsProvisional = false;
			feedsLoaded = true;
		}
		if (!feedsLoaded) {
			const owned = session.categories.filter(category => category.kind === 'shorts' && category.profile_id === profileId);
			if (owned.length) {
				feeds = owned.map(category => ({ ...category, ready_count: 0, pending_count: 0, error_count: 0 }));
				feedsProvisional = true;
			}
		}
		if (!background) loading = !feedsLoaded && !feeds.length;
		const profile = session.version, epoch = selectionEpoch, request = ++feedRequest;
		activeFeedRequest = request;
		try {
			const result = await api.shortsFeeds();
			if (alive && request === feedRequest && profile === session.version && profileId === session.profileId && !session.profile?.pin_locked && !session.needsLogin && epoch === selectionEpoch) {
				feeds = result;
				feedsProvisional = false;
				feedsLoaded = true;
				feedCache = { profileId, version: profile, savedAt: Date.now(), feeds: result };
				error = '';
			}
		}
		catch (e) {
			if (alive && request === feedRequest && profile === session.version && profileId === session.profileId && !session.profile?.pin_locked && !session.needsLogin && epoch === selectionEpoch)
				error = errMsg(e);
		}
		finally {
			if (activeFeedRequest === request) activeFeedRequest = null;
			if (alive && request === feedRequest && profile === session.version && profileId === session.profileId && !session.profile?.pin_locked && !session.needsLogin && epoch === selectionEpoch)
				loading = false;
		}
	}
	function refreshFeeds() {
		if (alive && !selected && !switchingProfile && !document.hidden)
			void loadFeeds(true);
	}
	async function switchProfile(id: number) {
		if (switchingProfile || id === session.profileId) return;
		reset();
		feeds = [];
		feedsLoaded = false;
		feedCache = null;
		loading = true;
		switchingProfile = true;
		try {
			if (!(await session.switchProfile(id)) && alive) await loadFeeds();
		} catch (e) {
			if (alive) { error = errMsg(e); loading = false; }
		} finally {
			if (alive) switchingProfile = false;
		}
	}
	function load(append = false): Promise<void> {
		if (!selected)
			return Promise.resolve();
		if (pendingLoad)
			return pendingLoad;
		const request = fetchPage(append);
		pendingLoad = request;
		void request.finally(() => { if (pendingLoad === request) pendingLoad = null; });
		return request;
	}
	async function fetchPage(append: boolean) {
		const feed = selected!.id, profile = session.version, epoch = selectionEpoch;
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
			if (alive && selected?.id === feed && profile === session.version && epoch === selectionEpoch)
				loading = false;
		}
	}
	function trackUrl(id: string, status: SubtitleStatus | null | undefined) {
		if (status?.status !== 'ready' || !status.track) return null;
		const url = new URL(status.track.url, location.origin);
		return url.origin === location.origin && url.pathname === `/api/video/${encodeURIComponent(id)}/subtitles/generated.vtt` ? url : null;
	}
	async function fetchCues(url: URL, signal?: AbortSignal) {
		const response = await fetch(url, { headers: profileHeaders(), signal, cache: 'no-store' });
		if (!response.ok) throw new Error('The subtitle file could not be loaded.');
		return parseReelCaptions(await response.text());
	}
	/** Captions for a clip ahead of the current one. Never requests generation; failures are not cached. */
	function prepareCaptions(item: Short) {
		const id = item.video.id, hit = cueCache.get(id);
		if (hit) return hit;
		const cache = cueCache;
		const task = (async () => {
			let url = trackUrl(id, item.video.subtitles);
			if (!url) url = trackUrl(id, await api.subtitleStatus(id));
			return url ? await fetchCues(url, AbortSignal.timeout(20000)) : null;
		})().catch(() => null);
		cache.set(id, task);
		if (cache.size > CUE_CACHE) cache.delete(cache.keys().next().value!);
		void task.then(cues => { if (!cues && cache.get(id) === task) cache.delete(id); });
		return task;
	}
	function readAhead(from: number) {
		const upcoming = items.slice(from + 1, from + 1 + READ_AHEAD);
		upcoming.forEach(item => void prepareCaptions(item));
		upcoming.forEach(item => { const img = new Image(); img.src = api.thumb(item.video.id); });
		if (upcoming[0]) {
			preloader ??= Object.assign(document.createElement('video'), { muted: true, preload: 'auto' });
			const src = api.fileUrl(upcoming[0].video.id);
			if (preloader.getAttribute('src') !== src) { preloader.src = src; preloader.load(); }
		}
		if (from >= items.length - 3 && nextOffset !== null) void load(true);
	}
	async function choose(feed: ShortsFeed) {
		const provisional = feedsProvisional;
		reset();
		selectedProvisional = provisional;
		const epoch = selectionEpoch;
		selected = feed;
		loading = true;
		await load();
		if (alive && epoch === selectionEpoch && selected?.id === feed.id && items.length)
			void play(0);
	}
 async function play(i: number, retryCaptions = false) {
  stop();
  const request = generation, item = items[i], profile = session.version, profileId = session.profileId, epoch = selectionEpoch;
  const valid = () => alive && request === generation && profile === session.version && profileId === session.profileId && epoch === selectionEpoch && current?.video.id === item?.video.id;
  needsTap = false; playbackError = ''; progress = 0; duration = 0; paused = true;  // `muted` carries over: sound stays on until muted
  captionMessage = 'Preparing subtitles…';
  await tick();
  if (!valid() || !item || !videoEl) return;
  const el = videoEl, controller = new AbortController();
  captionAbort = controller;
  const started = Date.now();
  let requested = false;
  function start(parsed: ReelCue[]) {
   cues = parsed; captionState = 'ready'; activeId = item.video.id;
   el.muted = muted; el.src = api.fileUrl(item.video.id); el.load();
   updateCaption(0);
   el.play().catch(() => {
    if (!valid()) return;
    if (el.muted) { needsTap = true; return; }
    muted = el.muted = true;  // the browser refused sound without a tap: play muted rather than stop
    el.play().catch(() => { if (valid()) needsTap = true; });
   });
   readAhead(i);
  }
  async function check() {
   try {
    if (!valid()) return;
    const listed = !requested && !retryCaptions && trackUrl(item.video.id, item.video.subtitles);
    let status = listed ? item.video.subtitles! : await api.subtitleStatus(item.video.id);
    if (!valid()) return;
    if ((status.status === 'idle' || status.status === 'queued' || (retryCaptions && (status.status === 'failed' || status.status === 'ready'))) && !requested && status.enabled) {
     requested = true; status = await api.generateSubtitles(item.video.id, true, retryCaptions);
     if (!valid()) return;
    }
    if (status.status === 'ready' && !status.track) throw new Error('The subtitle file is missing.');
    if (status.status === 'ready' && status.track) {
     captionMessage = 'Loading subtitles…';
     const url = trackUrl(item.video.id, status);
     if (!url) throw new Error('The local caption link is invalid.');
     const timeout = setTimeout(() => controller.abort(), 20000);
     let parsed: ReelCue[];
     try { parsed = await fetchCues(url, controller.signal); } finally { clearTimeout(timeout); }
     if (!valid()) return;
     start(parsed);
     return;
    }
    if (status.status === 'failed') throw new Error(status.error || 'Subtitle generation failed.');
    if (!status.enabled) throw new Error('Enable speech recognition in Settings → Player to prepare subtitles.');
    if (Date.now() - started > 300000) throw new Error('Subtitles are still being prepared. Retry to check again.');
    captionMessage = status.status === 'running' ? `Generating subtitles: ${Math.round(status.progress)}%` : 'Subtitles queued…';
    captionTimer = setTimeout(check, 2000);
   } catch (e) {
    if (!valid()) return;
    el.pause(); el.removeAttribute('src'); el.load(); activeId = null;
    captionState = 'failed'; captionMessage = errMsg(e);
   }
  }
  const prepared = retryCaptions ? undefined : cueCache.get(item.video.id);
  const ready = prepared ? await prepared : null;
  if (!valid()) return;
  if (ready) start(ready);
  else void check();
 }
 function updateCaption(position: number) {
  captionText = cues.filter(cue => position >= cue.start && position < cue.end).map(cue => cue.text).join('\n');
 }

	function record() {
		const item = current;
		if (!item || !videoEl || activeId !== item.video.id)
			return;
		const duration = videoEl.duration || item.video.duration_s || 0, position = videoEl.currentTime || 0;
		void api.saveHistory({ video_id: item.video.id, category_id: item.category_id, position_s: position, duration_s: duration, finished: duration > 0 && position >= duration - 1 }).catch(() => {
		});
	}
	async function go(delta: number, savePosition = true) {
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
		if (savePosition) record();
		index = next;
		void play(index);
	}
	async function retry() {
		const epoch = ++selectionEpoch;
		navigation++;
		pendingLoad = null;
		cueCache = new Map();
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
	function hideControls() {
		clearTimeout(controlsTimer);
		showControls = false;
	}
	function keepControlsVisible() {
		clearTimeout(controlsTimer);
		showControls = true;
		controlsTimer = setTimeout(hideControls, CONTROLS_IDLE_MS);
	}
	function onStageClick() {
		if (showControls) hideControls();
		else keepControlsVisible();
	}
	async function togglePlayback() {
		const el = videoEl;
		if (!el || !current || activeId !== current.video.id) return;
		keepControlsVisible();
		if (!el.paused && !el.ended) {
			el.pause();
			return;
		}
		const request = generation;
		try {
			await el.play();
			if (request === generation) needsTap = false;
		} catch {
			if (request === generation) needsTap = true;
		}
	}
	function onDurationChange() {
		const value = videoEl?.duration ?? 0;
		duration = Number.isFinite(value) && value > 0 ? value : 0;
		onTime();
	}
	function seek(position: number) {
		if (!videoEl || !current || activeId !== current.video.id || !duration) return;
		videoEl.currentTime = Math.max(0, Math.min(duration, position));
		onTime();
		keepControlsVisible();
	}
	function onKey(e: KeyboardEvent) {
		if (!selected)
			return;
		if (showControls) keepControlsVisible();
		if ((e.target as HTMLElement)?.matches('input[type="range"]') && e.key.startsWith('Arrow'))
			return;
		if (e.key === 'ArrowDown' || e.key === 'j') {
			e.preventDefault();
			void go(1);
		}
		else if (e.key === 'ArrowUp' || e.key === 'k') {
			e.preventDefault();
			void go(-1);
		}
		else if (e.key === 'Enter' || e.key === ' ') {
			if ((e.target as HTMLElement)?.closest('button,a,input,select,textarea'))
				return;
			e.preventDefault();
			if (current) onStageClick();
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
		if (videoEl && activeId === current?.video.id) {
   updateCaption(videoEl.currentTime);
			progress = duration ? Math.min(1, videoEl.currentTime / duration) : 0;
  }
	}
	function onEnded() {
		if (!current || activeId !== current.video.id) return;
		paused = true;
		record();
		if (session.settings?.player.scroll_autoplay_next !== false) void go(1, false);
	}
	let offRaw: (() => void) | null = null;
	onMount(() => {
		offRaw = nav.onRawKey(onKey);
		void loadFeeds();
		window.addEventListener('focus', refreshFeeds);
		document.addEventListener('visibilitychange', refreshFeeds);
		const timer = setInterval(refreshFeeds, 15000);
		return () => {
			clearInterval(timer);
			window.removeEventListener('focus', refreshFeeds);
			document.removeEventListener('visibilitychange', refreshFeeds);
		};
	});
	onDestroy(() => {
		record();
		alive = false;
		navigation++;
		stop();
		offRaw?.();
		if (preloader) { preloader.removeAttribute('src'); preloader.load(); preloader = null; }
	});
</script>
<svelte:head><title>The Scroll · The Hub</title></svelte:head>
<div class="feed" class:playing={!!selected} class:beside={deviceMode.mode === 'desktop'} role="region" aria-label="Video feed" ontouchstart={onTouchStart} ontouchend={onTouchEnd} ontouchcancel={()=>touchStart=null} onpointerdown={()=>{if(showControls) keepControlsVisible();}}>
	{#if !current || captionState !== 'ready' || needsTap || playbackError || showControls}
	<nav class="shorts-navigation" aria-label="Feeds navigation">
		<a class="btn" href="/" use:focusable>← Back to The Hub</a>
		{#if selected}<button class="btn" use:focusable onclick={picker}>← Feeds</button>
			<span class="now-feed" aria-hidden="true">{selected.emoji} {selected.name}{#if items.length}<span class="count">{index + 1} / {items.length}{nextOffset !== null ? '+' : ''}</span>{/if}</span>{/if}
		{#if current}<button class="btn" disabled={captionState !== 'ready'} use:focusable onclick={()=>{if(videoEl){muted=!muted;videoEl.muted=muted;}}}>{muted ? 'Unmute' : 'Mute'}</button>{/if}
	</nav>
	{/if}
	{#if !selected}
		<div class="picker">
			<header class="picker-head">
				<p class="eyebrow">The Scroll</p>
				<h1>Choose a feed</h1>
				<p class="muted">Short clips your curator collected, played only from local storage.</p>
			</header>
			{#if session.profiles.length > 1}<div class="profile-choices" role="group" aria-label="Choose Feeds profile">
				{#each session.profiles as profile (profile.id)}<button class="chip" class:on={profile.id === session.profileId} aria-pressed={profile.id === session.profileId} disabled={switchingProfile} onclick={()=>switchProfile(profile.id)} use:focusable>{profile.emoji} {profile.name}{profile.pin_required ? ' 🔒' : ''}</button>{/each}
			</div>{:else}<p class="small muted">Feeds for <strong>{session.profile?.name ?? 'your profile'}</strong></p>{/if}
			{#if loading}
				<p class="small muted">Loading feeds…</p>
				<div class="feed-grid" aria-hidden="true">{#each [0, 1, 2] as n (n)}<div class="feed-card skeleton"></div>{/each}</div>
			{:else}
				{#if error}<div class="notice" role="alert"><p class="err">{error}</p><button class="btn" onclick={()=>loadFeeds()} use:focusable>Try again</button></div>{/if}
				{#if feeds.length}<div class="feed-grid">
					{#each feeds as feed (feed.id)}
						{@const ready = feed.ready_count}
						{@const playable = !feedsProvisional && (feed.caption_ready_count ?? ready) > 0}
						<button class="feed-card" class:paused={!feed.enabled} class:playable style:--tint={feed.color || 'var(--ai)'} use:focusable onclick={()=>choose(feed)}>
							<span class="feed-emoji" aria-hidden="true">{feed.emoji}</span>
							<span class="feed-body">
								<span class="feed-top"><strong>{feed.name}</strong><span class="play-label">{playable ? '▶ Play' : 'View feed'}</span></span>
								{#if feedsProvisional}
									<span class="stats"><span class="stat checking">Checking local availability…</span></span>
								{:else}
									<span class="stats">
										<span class="stat ok">{ready} stored locally</span>
										{#if feed.caption_ready_count !== undefined}<span class="stat">{feed.caption_ready_count} captioned</span>{/if}
										{#if feed.pending_count}<span class="stat">{feed.pending_count} waiting for local files</span>{/if}
										{#if feed.error_count}<span class="stat bad">{feed.error_count} download errors</span>{/if}
										{#if !feed.enabled}<span class="stat warn">Collection paused</span>{/if}
									</span>
									<span class="meter" aria-hidden="true"><span style:width={`${ready + feed.pending_count ? (100 * ready) / (ready + feed.pending_count) : 0}%`}></span></span>
								{/if}
							</span>
						</button>
					{/each}
				</div>{/if}
				{#if !feeds.length && !error}<div class="notice"><p>No feeds for {session.profile?.name ?? 'this profile'} yet.</p><p class="small muted">{session.profiles.length > 1 ? 'Choose another profile above to see its feeds, or create a feed for this profile.' : 'Give your curator a brief to start collecting local clips.'}</p></div>{/if}
			{/if}
			<div class="picker-actions">
				<a class="btn primary" href="/settings?tab=feeds&new=1" use:focusable>New feed</a>
				<a class="btn" href="/settings?tab=feeds" use:focusable>Manage feeds</a>
				<button class="btn ghost" disabled={loading || switchingProfile} onclick={()=>loadFeeds()} use:focusable>Refresh feeds</button>
				<a class="btn ghost" href="/" use:focusable>Leave Feeds</a>
			</div>
		</div>
	{:else}
		{#if loading}<div class="state"><span class="spinner" aria-hidden="true"></span><p>Checking local videos…</p></div>
		{:else if !current}<div class="state"><span class="state-emoji" aria-hidden="true">{selected.emoji}</span><h1>{selected.name}</h1>{#if error}<p role="alert" class="err">{error}</p>{:else}<p>No local clips ready yet.</p>{#if !session.settings?.cache.enabled}<p class="muted">Enable Storage → Offline cache in Settings so accepted clips can download.</p>{/if}{#if !session.settings?.agent.enabled}<p class="muted">Enable the AI agent in Settings to collect new clips.</p>{/if}<p class="muted">{#if selectedProvisional}Refresh once local downloads finish.{:else}{selected.pending_count} waiting for local files{selected.error_count ? `; ${selected.error_count} download errors` : ''}. Refresh once downloads finish.{/if}</p>{/if}<div class="row"><button class="btn primary" use:focusable onclick={retry}>Refresh availability</button><a class="btn" href={`/settings?tab=feeds&edit=${selected.id}`} use:focusable>Edit feed</a></div></div>
		{:else}
		{#key current.video.id}<div class="backdrop" aria-hidden="true" style:background-image={`url(${api.thumb(current.video.id)})`}></div>{/key}
		<video bind:this={videoEl} class="stage" muted playsinline preload="auto" poster={api.thumb(current.video.id)} ontimeupdate={onTime} ondurationchange={onDurationChange} onplay={()=>paused=false} onpause={()=>paused=true} onended={onEnded} onerror={()=>{if(activeId===current?.video.id)playbackError='This local file is unavailable or cannot be played. Refresh availability to check stored clips.';}} onclick={onStageClick}></video>
		{#if captionState !== 'ready'}<div class="caption-status" role={captionState === 'failed' ? 'alert' : 'status'}>{#if captionState === 'waiting'}<span class="spinner" aria-hidden="true"></span>{/if}<p>{captionMessage}</p><div class="row">{#if captionState === 'failed'}<button class="btn primary" use:focusable onclick={()=>play(index, true)}>Retry captions</button><a class="btn" href="/settings?tab=player" use:focusable>Caption settings</a>{/if}<button class="btn" use:focusable onclick={()=>go(1)}>Skip to next clip</button></div></div>{/if}
		{#if captionState === 'ready' && captionsVisible}<div class="captions" class:details={showControls} aria-label="Subtitles"><span>{captionText}</span></div>{/if}
		{#if playbackError}<div class="playback-error" role="alert"><p>{playbackError}</p><button class="btn primary" use:focusable onclick={retry}>Refresh availability</button></div>
		{:else if error}<div class="playback-error" role="alert"><p>{error}</p><button class="btn" use:focusable onclick={()=>go(1)}>Retry next short</button></div>
		{:else if needsTap}<button class="tap" use:focusable onclick={togglePlayback}><span class="tap-icon">▶ Tap to play</span></button>{/if}
		{#if showControls}
		<div class="scrim" aria-hidden="true"></div>
		<div class="meta"><p class="small muted">{selected.name} · Stored locally</p><h1>{current.video.title}</h1><p class="small muted">{current.video.channel_name||current.video.channel_id}{current.score!==null ? ` · ${current.score}% match` : ''}</p>{#if current.summary}<p class="small why">{current.summary}</p>{/if}</div>
		<div class="acts">
			<button class="btn round" aria-label="More like this" disabled={busy} use:focusable={{group:'shorts',onSelect:()=>rate(1)}}>👍</button>
			<button class="btn round" aria-label="Not for me" disabled={busy} use:focusable={{group:'shorts',onSelect:()=>rate(-1)}}>👎</button>
			<button class="btn round" aria-label="Next clip" disabled={nextOffset === null && index >= items.length - 1} use:focusable={{group:'shorts',onSelect:()=>go(1)}}>⌄</button>
		</div>
		<div class="transport" role="group" aria-label="Playback controls">
			<button class="btn" aria-label="Previous clip" disabled={index === 0} use:focusable={{group:'shorts',onSelect:()=>go(-1)}}>↑</button>
			<button class="btn" disabled={captionState !== 'ready' || !!playbackError} use:focusable onclick={togglePlayback}>{paused ? 'Play' : 'Pause'}</button>
			<input aria-label="Seek" type="range" min="0" max={duration} step="0.1" value={progress * duration} disabled={!duration || !!playbackError} oninput={(event) => seek(event.currentTarget.valueAsNumber)} use:focusable={{ onKey: (key) => {
				if (key !== 'left' && key !== 'right') return false;
				seek((videoEl?.currentTime ?? 0) + (key === 'left' ? -1 : 1));
				return true;
			} }} />
			<button class="btn" aria-label="Closed captions" aria-pressed={captionsVisible} use:focusable onclick={()=>captionsVisible=!captionsVisible}>CC</button>
		</div>
		{/if}
		{/if}
	{/if}
</div>
<style>
	@media (min-width: 761px) { .feed.beside { left: var(--sidebar-width, 15rem); } }  /* desktop keeps the sidebar menu in view; phones stay full screen */
	.feed { --shorts-toolbar-height: calc(4.5rem + env(safe-area-inset-top, 0px)); position: fixed; inset: 0; z-index: 140; display: grid; place-items: center; overflow: hidden; touch-action: none; color: var(--text);
		background: radial-gradient(120% 80% at 50% -10%, rgba(157, 134, 255, .22), transparent 60%), radial-gradient(90% 60% at 100% 110%, rgba(255, 181, 71, .1), transparent 60%), var(--bg-0); }
	.feed.playing { background: #000; }

	/* toolbar */
	.shorts-navigation { position:absolute; top:0; left:0; right:0; z-index:5; display:flex; flex-wrap:wrap; align-items:center; gap:.5rem; padding:calc(.75rem + env(safe-area-inset-top, 0px)) max(.75rem, env(safe-area-inset-right, 0px)) 1.25rem max(.75rem, env(safe-area-inset-left, 0px)); background:linear-gradient(rgba(5,4,12,.85), rgba(5,4,12,.45) 60%, transparent); touch-action:manipulation; pointer-events:none; }
	.shorts-navigation > * { pointer-events:auto; }
	.shorts-navigation .btn { white-space:nowrap; backdrop-filter: var(--blur); -webkit-backdrop-filter: var(--blur); }
	.now-feed { display:inline-flex; align-items:center; gap:.5rem; margin-inline:auto; font-weight:650; font-size:.95rem; color:#fff; text-shadow:0 1px 6px #000; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
	.count { font-size:.75rem; font-weight:600; padding:.15rem .5rem; border-radius:var(--r-pill); background:rgba(255,255,255,.14); font-variant-numeric:tabular-nums; }
	@media (max-width: 560px) { .now-feed { display:none; } }
	@media (max-width: 360px) { .shorts-navigation { gap:.35rem; } .shorts-navigation .btn { padding-inline:.55rem; font-size:.85rem; } }

	/* picker */
	.picker { width: min(100%, 64rem); height: 100%; overflow-y:auto; touch-action:pan-y; display:grid; gap:1.25rem; align-content:start; padding: calc(var(--shorts-toolbar-height) + .5rem) var(--gutter) calc(2rem + env(safe-area-inset-bottom, 0px)); }
	.picker-head { display:grid; gap:.35rem; }
	.picker-head h1 { margin:0; font-size:clamp(1.6rem, 2.4vw + .8rem, 2.6rem); letter-spacing:-.02em; }
	.picker-head p { margin:0; }
	.eyebrow { font-size:.8rem; font-weight:700; letter-spacing:.14em; text-transform:uppercase; color:var(--accent); }
	.profile-choices { display:flex; flex-wrap:wrap; gap:.5rem; }
	.feed-grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(min(100%, 19rem), 1fr)); gap:clamp(.75rem, 1.2vw, 1.25rem); }
	.feed-card { --tint: var(--ai); position:relative; display:grid; grid-template-columns:auto 1fr; gap:1rem; align-items:start; min-height:7.5rem; text-align:left; width:100%; padding:1.1rem; border:1px solid var(--line); border-radius:var(--r-lg); color:inherit; font:inherit; cursor:pointer; overflow:hidden;
		background: linear-gradient(140deg, color-mix(in srgb, var(--tint) 24%, transparent), transparent 55%), var(--surface); backdrop-filter: var(--blur); -webkit-backdrop-filter: var(--blur); }
	.feed-card.skeleton { min-height:7.5rem; border-radius:var(--r-lg); }
	.feed-card:hover { border-color:var(--line-strong); }
	.feed-card.paused { opacity:.78; }
	.feed-emoji { display:grid; place-items:center; width:3.25rem; height:3.25rem; border-radius:var(--r-md); font-size:1.7rem; background:color-mix(in srgb, var(--tint) 30%, rgba(0,0,0,.25)); box-shadow: inset 0 0 0 1px rgba(255,255,255,.08); }
	.feed-body { display:grid; gap:.55rem; min-width:0; }
	.feed-top { display:flex; align-items:baseline; justify-content:space-between; gap:.75rem; }
	.feed-top strong { font-size:1.08rem; line-height:1.25; overflow-wrap:anywhere; }
	.play-label { flex:none; font-size:.8rem; font-weight:650; padding:.25rem .65rem; border-radius:var(--r-pill); background:var(--surface-2); color:var(--muted); white-space:nowrap; }
	.feed-card.playable .play-label { background:var(--accent); color:var(--accent-ink); }
	.stats { display:flex; flex-wrap:wrap; gap:.35rem; }
	.stat { font-size:.75rem; font-weight:550; padding:.15rem .5rem; border-radius:var(--r-pill); background:rgba(255,255,255,.06); color:var(--muted); }
	.stat.ok { color:var(--ok); background:rgba(61,220,151,.12); }
	.stat.bad { color:var(--bad); background:rgba(255,92,122,.12); }
	.stat.warn { color:var(--warn); background:rgba(255,207,92,.12); }
	.stat.checking { background: linear-gradient(100deg, rgba(255,255,255,.04) 20%, rgba(255,255,255,.12) 40%, rgba(255,255,255,.04) 60%); background-size:220% 100%; animation: shimmer 1.4s linear infinite; }
	.meter { height:4px; border-radius:var(--r-pill); background:rgba(255,255,255,.08); overflow:hidden; }
	.meter span { display:block; height:100%; background:linear-gradient(90deg, var(--tint), var(--accent)); border-radius:inherit; }
	.picker-actions { display:flex; flex-wrap:wrap; gap:.5rem; padding-top:.25rem; }
	.notice { display:grid; gap:.5rem; justify-items:start; padding:1.25rem; border:1px dashed var(--line-strong); border-radius:var(--r-lg); }
	.notice p { margin:0; }
	@keyframes shimmer { to { background-position: -220% 0; } }

	/* states */
	.state { display: grid; gap: .8rem; justify-items: center; text-align: center; padding: var(--shorts-toolbar-height) 2rem calc(2rem + env(safe-area-inset-bottom, 0px)); max-width: 34rem; }
	.state h1 { margin: 0; font-size: 1.4rem; }
	.state p { margin: 0; }
	.state-emoji { font-size:2.5rem; }
	.row { display:flex; flex-wrap:wrap; justify-content:center; gap:.5rem; }
	.err { color: var(--bad); }
	.spinner { width:2rem; height:2rem; border-radius:50%; border:3px solid rgba(255,255,255,.15); border-top-color:var(--accent); animation: spin .8s linear infinite; }
	@keyframes spin { to { transform: rotate(360deg); } }
	@media (prefers-reduced-motion: reduce) { .spinner, .stat.checking { animation-duration: 3s; } }

	/* player */
	.backdrop { position:absolute; inset:-8%; background-size:cover; background-position:center; filter: blur(40px) brightness(.45) saturate(1.3); transform: scale(1.1); animation: fade .35s ease both; }
	@keyframes fade { from { opacity: 0; } }
	.stage { position:absolute; inset:0; width: 100%; height: 100%; object-fit: contain; background: transparent; }
	.scrim { position:absolute; left:0; right:0; bottom:0; height:45%; z-index:1; pointer-events:none; background:linear-gradient(transparent, rgba(0,0,0,.7)); }
	.caption-status { position:absolute; z-index:3; display:grid; justify-items:center; gap:.75rem; padding:1.5rem; max-width: min(90%, 36rem); text-align:center; background:var(--overlay-surface); border:1px solid var(--line); color:#fff; border-radius:var(--r-lg); box-shadow:var(--shadow-lg); backdrop-filter: var(--blur); -webkit-backdrop-filter: var(--blur); }
	.caption-status p { margin:0; }
	.captions { position:absolute; bottom:calc(18.5rem + env(safe-area-inset-bottom, 0px)); left:5%; right:5%; z-index:3; pointer-events:none; text-align:center; color:#fff; font-size:clamp(1.1rem, 3.5vw, 1.6rem); font-weight:650; line-height:1.45; white-space:pre-line; text-shadow:0 1px 3px #000; }
	.captions.details { bottom:calc(35vh + 8rem + env(safe-area-inset-bottom, 0px)); }
	.captions span { background:rgba(0,0,0,.78); border-radius:.3rem; box-decoration-break:clone; -webkit-box-decoration-break:clone; padding:.12rem .45rem; }
	.playback-error { position: absolute; z-index: 3; top: 20%; display:grid; gap:.75rem; justify-items:center; padding: 1.25rem; max-width: min(90%, 32rem); background: var(--overlay-surface); border:1px solid rgba(255,92,122,.4); border-radius:var(--r-lg); color: #fff; text-align: center; }
	.playback-error p { margin:0; }
	.tap { position: absolute; inset: 0; z-index: 1; display: grid; place-content: center; justify-items:center; gap: .75rem; background: rgba(5, 4, 12, .5); color: #fff; border: 0; font: inherit; font-size: 1.1rem; font-weight:600; cursor: pointer; }
	.tap-icon { padding:.85rem 1.6rem; border-radius:var(--r-pill); background:var(--accent); color:var(--accent-ink); font-size:1.15rem; font-weight:700; box-shadow:0 0 40px var(--accent-glow); }
	:global(html[data-modality='keys']) .tap:focus { position: absolute; z-index: 1; }
	.meta, .acts, .transport { z-index: 2; }
	.meta { position: absolute; left: 0; right: 5rem; bottom: 5.5rem; padding: 0 1.2rem; display: grid; gap: .3rem; text-shadow: 0 1px 6px rgba(0, 0, 0, .9); max-height:35vh; overflow-y:auto; }
	.meta h1 { margin: 0; font-size: clamp(1.1rem, 1vw + .8rem, 1.6rem); line-height: 1.3; color: #fff; }
	.meta p { margin:0; }
	.meta .muted { color: rgba(255, 255, 255, .75); }
	.why { color: rgba(255, 255, 255, .85); max-height: 4.5rem; overflow: hidden; }
	.acts { position: absolute; right: max(1rem, env(safe-area-inset-right, 0px)); bottom: 5.5rem; display: grid; gap: .75rem; }
	.btn.round { width: 3.25rem; height: 3.25rem; padding:0; border-radius: 50%; display: grid; place-items: center; font-size: 1.25rem; background: rgba(20, 16, 40, .62); border: 1px solid var(--line-strong); color: #fff; backdrop-filter: var(--blur); -webkit-backdrop-filter: var(--blur); }
	.transport { position:absolute; left:max(1rem, env(safe-area-inset-left, 0px)); right:max(1rem, env(safe-area-inset-right, 0px)); bottom:calc(1rem + env(safe-area-inset-bottom, 0px)); display:flex; align-items:center; gap:.5rem; }
	.transport input { flex:1; min-width:0; accent-color:var(--accent); }
	.transport [aria-pressed="true"] { background:var(--accent); color:var(--accent-ink); }
	@media (max-width: 760px) { .meta { bottom: 6.5rem; } .acts { bottom: 6.5rem; } }
</style>
