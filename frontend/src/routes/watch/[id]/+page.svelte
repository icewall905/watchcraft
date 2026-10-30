<script lang="ts">
	import { onMount, onDestroy, tick } from 'svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { api } from '$lib/api';
	import { session } from '$lib/stores/session.svelte';
	import { playback } from '$lib/stores/playback.svelte';
	import { preview } from '$lib/preview';
	import { dialogs } from '$lib/stores/dialogs.svelte';
	import { toast } from '$lib/stores/toast.svelte';
	import { focusable, navScope, nav, scrollByKeys, type NavKey } from '$lib/nav';
	import { fmtDuration, fmtCount, timeAgo, SB_COLORS, SB_LABELS, clamp, isVideo } from '$lib/util';
	import { loadShaka } from '$lib/player/shaka';
	import { prepareSegments, type Segment } from '$lib/player/sponsorblock';
	import { warmVideo } from '$lib/prefetch';
	import type { Candidate, CaptionTrack, VideoDetail } from '$lib/types';
	import Icon from '$lib/components/Icon.svelte';
	import OfflineButton from '$lib/components/OfflineButton.svelte';

	// ---------- route
	const id = $derived(page.params.id ?? '');
	const castCommand = $derived(page.url.searchParams.get('cast'));
	const catId = $derived(page.url.searchParams.get('c') ? Number(page.url.searchParams.get('c')) : null);

	// ---------- media state
	let video = $state<HTMLVideoElement>();
	let player: any = null; // shaka.Player
	let mode = $state<'dash' | 'fallback' | 'offline' | 'none'>('none');
	let detail = $state<VideoDetail | null>(null);
	let loadError = $state('');
	let loading = $state(true);
	let paused = $state(true);
	let time = $state(0);
	let duration = $state(0);
	let buffered = $state(0);
	let waiting = $state(false);
	let speed = $state(1);
	let qualities = $state<number[]>([]);
	let quality = $state<number | 'auto'>('auto');
	let captions = $state<CaptionTrack[]>([]);
	let captionKey = $state<string | null>(null);
	let segments = $state<Segment[]>([]);
	let ended = $state(false);
	let upNext = $state<Candidate[]>([]);
	let countdown = $state<number | null>(null);

	// ---------- UI state
	let controls = $state(true);
	let menu = $state<'quality' | 'speed' | 'captions' | null>(null);
	let info = $state(false);
	let askSeg = $state<Segment | null>(null);
	let seeking = $state(false);
	let hideTimer: ReturnType<typeof setTimeout> | null = null;
	const handled = new Set<string>();
	let lastSaved = 0;
	let lastBeat = 0;
	let loadSeq = 0;
	let manifestRetried = false;
	let decodeRetried = false;
	let slow = $state(false);
	let slowTimer: ReturnType<typeof setTimeout> | null = null;

	const chapters = $derived((detail?.chapters ?? []).filter((c) => typeof c.start_time === 'number'));
	const chapter = $derived([...chapters].reverse().find((c) => c.start_time <= time));
	const inCat = $derived(detail?.in_categories?.find((c) => c.category_id === catId) ?? detail?.in_categories?.[0]);
	const category = $derived(session.categoryById(catId ?? inCat?.category_id));
	const title = $derived(detail?.dearrow?.title || detail?.title || '');
	const progressPct = $derived(duration ? (time / duration) * 100 : 0);

	// ---------- controls visibility
	function poke() {
		controls = true;
		if (hideTimer) clearTimeout(hideTimer);
		hideTimer = setTimeout(() => {
			if (!paused && !menu && !info && !ended && !askSeg) controls = false;
		}, 3000);
	}
	$effect(() => {
		if (paused || ended) {
			controls = true;
			if (hideTimer) clearTimeout(hideTimer);
		} else if (!menu && !info && !askSeg) {
			poke();
		}
	});

	// ---------- load
	async function load(vid: string) {
		const seq = ++loadSeq;
		loading = true;
		manifestRetried = false;
		decodeRetried = false;
		slow = false;
		if (slowTimer) clearTimeout(slowTimer);
		slowTimer = setTimeout(() => seq === loadSeq && (slow = true), 8000);
		loadError = '';
		ended = false;
		countdown = null;
		detail = null;
		segments = [];
		captions = [];
		captionKey = null;
		handled.clear();
		askSeg = null;
		time = 0;
		duration = 0;
		info = false;
		menu = null;
		await teardown();
		await tick();
		if (!video || seq !== loadSeq) return;

		// Local asset: have Shaka ready before anything else can block.
		loadShaka().catch(() => {});

		speed = session.settings?.player.default_speed ?? 1;
		// Detail (title, AI reasons, download state) is UI data; playback never waits on it.
		const detailP = api.video(vid).then(
			(d) => (detail = d),
			(e) => {
				loadError = e?.status === 503 ? 'YouTube is temporarily blocking requests. Try again later.' : e?.status === 404 ? 'This video is unavailable.' : '';
				return null;
			}
		);

		// Resume position: small history call instead of the full detail.
		let start = 0;
		const h = await api.historyFor(vid).catch(() => null);
		if (seq !== loadSeq) return;
		if (h && !h.finished) start = h.position_s ?? 0;
		if (start < 5) start = 0;

		// A saved copy plays straight from disk; the local probe beats any detail round trip.
		const offlineOk = await playOffline(vid, start, seq, false);
		if (seq !== loadSeq) return;
		if (!offlineOk && !(await stream(vid, start, seq))) return;
		if (seq !== loadSeq) return;
		if (h?.finished && h.updated_at) {
			await api.restartHistory(vid, h.updated_at).catch(() => {});
			if (seq !== loadSeq) return;
		}
		loadError = ''; // it plays: never show the error card over it
		video.playbackRate = speed;
		loading = false;
		if (slowTimer) clearTimeout(slowTimer);
		video.play().then(() => { if (playback.receiver) playback.error = ''; }).catch(() => {
			paused = true;
			if (playback.receiver) playback.error = 'Autoplay was blocked. Press Play on this screen, or allow autoplay for WatchCraft.';
		});
		poke();

		// Side data, all optional.
		api.sponsorblock(vid).then((raw) => seq === loadSeq && (segments = prepareSegments(raw, session.settings?.sponsorblock)), () => {});
		api.captions(vid).then((c) => {
			if (seq !== loadSeq) return;
			captions = c;
			const pref = session.settings?.player.captions_lang;
			if (pref) {
				const m = c.find((t) => t.lang === pref && !t.auto) ?? c.find((t) => t.lang.startsWith(pref));
				if (m) setCaption(m.key);
			}
		}, () => {});
		detailP.then(() => {});
		const cid = catId ?? null;
		if (cid) api.categoryFeed(cid, 'accepted', { limit: 12, after: vid }).then((l) => seq === loadSeq && (upNext = l.filter((c) => c.video.id !== vid)), () => {});
	}

	/** Stream through Shaka (DASH), falling back to the progressive proxy. Returns false if superseded. */
	async function stream(vid: string, start: number, seq: number): Promise<boolean> {
		if (!video) return false;
		const maxH = session.settings?.player.max_height;
		let ok = false;
		try {
			const shaka = await loadShaka();
			if (seq !== loadSeq) return false;
			if (shaka.Player.isBrowserSupported()) {
				player = new shaka.Player();
				await player.attach(video);
				player.configure({
					streaming: { bufferingGoal: 45, rebufferingGoal: 2, bufferBehind: 30, retryParameters: { maxAttempts: 4, timeout: 30000 } },
					manifest: { retryParameters: { maxAttempts: 2, timeout: 60000 } },
					abr: { enabled: true, restrictions: maxH ? { maxHeight: maxH } : {} }
				});
				player.addEventListener('error', (ev: any) => {
					const d = ev?.detail;
					console.warn('shaka error', d);
					if (mode !== 'dash' || seq !== loadSeq) return;
					const at = video!.currentTime || start;
					// Signed proxy URLs can expire or be refused: rebuild the manifest once, then fall back.
					if (d?.code === 1001 && d?.data?.[1] === 403 && String(d?.data?.[0] ?? '').includes('/api/proxy/') && !manifestRetried) {
						manifestRetried = true;
						player.load(api.manifestUrl(vid, maxH), at).then(
							() => video!.play().catch(() => {}),
							() => fallback(vid, at, seq)
						);
						return;
					}
					// Decoder choked (often on a quality switch): retry once pinned to a single quality.
					if (d?.code === 3016 && !decodeRetried) {
						decodeRetried = true;
						player.configure({ abr: { enabled: false } });
						player.load(api.manifestUrl(vid, maxH), at).then(
							() => {
								const tracks: any[] = player.getVideoTracks();
								const best = tracks.sort((a, b) => (b.height ?? 0) - (a.height ?? 0))[0];
								if (best) {
									player.selectVideoTrack(best, true);
									quality = best.height ?? 'auto';
								}
								video!.play().catch(() => {});
							},
							() => fallback(vid, at, seq)
						);
						return;
					}
					if (d?.severity === 2) fallback(vid, at, seq);
				});
				player.addEventListener('variantchanged', refreshQualities);
				player.addEventListener('trackschanged', refreshQualities);
				await player.load(api.manifestUrl(vid, maxH), start || undefined);
				if (seq !== loadSeq) return false;
				mode = 'dash';
				ok = true;
				refreshQualities();
			}
		} catch (e: any) {
			console.warn('DASH failed, using fallback', e);
			if (e?.data?.[1] === 409 || String(e?.data?.[0] ?? '').includes('409')) {
				loadError = 'Live streams are not supported yet.';
			}
		}
		if (!ok && seq === loadSeq && !loadError) await fallback(vid, start, seq);
		return seq === loadSeq;
	}

	/** Play the downloaded file. `known` = the API said it is done; otherwise probe first. */
	async function playOffline(vid: string, start: number, seq: number, known: boolean): Promise<boolean> {
		if (!known) {
			try {
				const r = await fetch(api.fileUrl(vid), { headers: { Range: 'bytes=0-0' } });
				r.body?.cancel().catch(() => {});
				if (!r.ok) return false;
			} catch {
				return false;
			}
		}
		if (seq !== loadSeq || !video) return false;
		const v = video;
		const ok = await new Promise<boolean>((resolve) => {
			const done = (x: boolean) => {
				v.removeEventListener('loadedmetadata', onOk);
				v.removeEventListener('error', onErr);
				resolve(x);
			};
			const onOk = () => done(true);
			const onErr = () => done(false);
			v.addEventListener('loadedmetadata', onOk);
			v.addEventListener('error', onErr);
			v.src = api.fileUrl(vid);
			setTimeout(() => done(false), 20000);
		});
		if (seq !== loadSeq) return false;
		if (!ok) {
			v.removeAttribute('src');
			v.load();
			return false;
		}
		mode = 'offline';
		loadError = '';
		if (start) v.currentTime = start;
		// A broken or evicted file mid-play: continue by streaming from the same spot.
		v.addEventListener(
			'error',
			() => {
				if (seq !== loadSeq || mode !== 'offline') return;
				const at = v.currentTime;
				mode = 'none';
				toast.info('The saved copy failed. Streaming instead.');
				stream(vid, at, seq).then((s) => {
					if (s) v.play().catch(() => {});
				});
			},
			{ once: true }
		);
		return true;
	}

	async function fallback(vid: string, start: number, seq: number) {
		if (seq !== loadSeq || !video) return;
		if (player) {
			try {
				await player.destroy();
			} catch {
				/* ignore */
			}
			player = null;
		}
		mode = 'fallback';
		video.src = api.fallbackUrl(vid);
		if (start) {
			const s = start;
			video.addEventListener('loadedmetadata', () => (video!.currentTime = s), { once: true });
		}
		video.addEventListener(
			'error',
			() => {
				if (seq === loadSeq) {
					loadError = loadError || 'This video could not be played. YouTube may be blocking the server, or the video is restricted.';
					loading = false;
				}
			},
			{ once: true }
		);
		video.playbackRate = speed;
		video.play().catch(() => {});
	}

	async function teardown() {
		if (player) {
			try {
				await player.destroy();
			} catch {
				/* ignore */
			}
			player = null;
		}
		if (video) {
			video.removeAttribute('src');
			for (const t of [...video.querySelectorAll('track')]) t.remove();
			video.load();
		}
		mode = 'none';
	}

	function refreshQualities() {
		if (!player) return;
		const tracks: { height: number | null }[] = player.getVideoTracks?.() ?? [];
		qualities = [...new Set(tracks.map((t) => t.height).filter((h): h is number => !!h))].sort((a, b) => b - a);
	}

	function setQuality(q: number | 'auto') {
		quality = q;
		menu = null;
		if (!player) return;
		if (q === 'auto') {
			player.configure({ abr: { enabled: true } });
			return;
		}
		player.configure({ abr: { enabled: false } });
		const tracks: any[] = player.getVideoTracks();
		const t = tracks.filter((x) => x.height === q).sort((a, b) => b.bandwidth - a.bandwidth)[0];
		if (t) player.selectVideoTrack(t, true);
	}

	function setSpeed(s: number) {
		speed = s;
		if (video) video.playbackRate = s;
		menu = null;
	}

	function setCaption(key: string | null) {
		captionKey = key;
		menu = null;
		if (!video) return;
		for (const t of [...video.querySelectorAll('track')]) t.remove();
		if (!key) return;
		const c = captions.find((x) => x.key === key);
		if (!c) return;
		const el = document.createElement('track');
		el.kind = 'subtitles';
		el.srclang = c.lang;
		el.label = c.name;
		el.src = c.url;
		el.default = true;
		video.appendChild(el);
		el.addEventListener('load', () => (el.track.mode = 'showing'));
		el.track.mode = 'showing';
	}

	// ---------- playback helpers
	function togglePlay() {
		if (!video) return;
		if (ended) return replay();
		if (video.paused) video.play().catch(() => {});
		else video.pause();
		poke();
	}
	function seekBy(d: number) {
		if (!video || !duration) return;
		video.currentTime = clamp(video.currentTime + d, 0, Math.max(0, duration - 0.5));
		time = video.currentTime;
		poke();
	}
	function seekTo(t: number) {
		if (!video) return;
		video.currentTime = clamp(t, 0, duration || t);
		time = video.currentTime;
	}
	async function replay() {
		const vid = id;
		const h = await api.historyFor(vid).catch(() => null);
		if (h?.finished && h.updated_at) await api.restartHistory(vid, h.updated_at).catch(() => {});
		if (vid !== id) return;
		ended = false;
		countdown = null;
		seekTo(0);
		video?.play();
	}

	function saveHistory(keepalive = false, finished = false) {
		if (!video || !id || mode === 'none') return;
		const pos = video.currentTime || time;
		if (pos < 1 && !finished) return;
		lastSaved = Date.now();
		api.saveHistory({ video_id: id, position_s: Math.floor(pos), duration_s: Math.floor(duration || 0), category_id: catId ?? inCat?.category_id ?? null, finished }, keepalive).catch(() => {});
	}

	function onTime() {
		if (!video) return;
		time = video.currentTime;
		const b = video.buffered;
		for (let i = 0; i < b.length; i++) if (b.start(i) <= time && b.end(i) >= time) buffered = b.end(i);
		if (!video.paused) {
			if (Date.now() - lastSaved > 10000) saveHistory();
			if (Date.now() - lastBeat > 60000) {
				lastBeat = Date.now();
				api.agentPlaying(90).catch(() => {});
			}
		}
		checkSegments();
	}

	function checkSegments() {
		if (!segments.length) return;
		const t = time;
		const seg = segments.find((s) => !s.poi && t >= s.start && t < s.end - 0.3);
		if (askSeg && (!seg || seg.uuid !== askSeg.uuid)) askSeg = null;
		if (!seg || handled.has(seg.uuid)) return;
		if (seg.action === 'skip') {
			handled.add(seg.uuid);
			seekTo(seg.end);
			toast.info(`Skipped ${(SB_LABELS[seg.category] ?? seg.category).toLowerCase()}`, { timeout: 2500 });
		} else if (seg.action === 'ask') {
			handled.add(seg.uuid);
			askSeg = seg;
			controls = true;
			tick().then(() => {
				const b = document.querySelector<HTMLElement>('#sb-skip');
				if (b) nav.focus(b, { scroll: false });
			});
		}
	}

	function skipAsked() {
		if (!askSeg) return;
		seekTo(askSeg.end);
		askSeg = null;
		poke();
	}

	function onEnded() {
		ended = true;
		saveHistory(false, true);
		controls = true;
		menu = null;
		info = false;
		if (session.settings?.player.autoplay_next && upNext.length) {
			countdown = 10;
			// Autoplay is ~10s away: warm its streams now so it starts without "Preparing video".
			warmVideo(upNext[0].video.id, { manifest: true, force: true });
		}
	}

	let cdTimer: ReturnType<typeof setInterval> | null = null;
	$effect(() => {
		if (countdown == null) {
			if (cdTimer) clearInterval(cdTimer);
			cdTimer = null;
			return;
		}
		if (!cdTimer)
			cdTimer = setInterval(() => {
				if (countdown == null) return;
				if (countdown <= 1) {
					countdown = null;
					playNext();
				} else countdown--;
			}, 1000);
	});

	function playNext() {
		const n = upNext[0];
		if (!n) return;
		goto(`/watch/${n.video.id}${catId ? `?c=${catId}` : ''}`, { replaceState: true });
	}

	/** Leave the player. Goes straight to the router: calling nav.back() from a scope's onBack would recurse. */
	function exit() {
		saveHistory(true);
		nav.backFallback();
	}

	function feedback(rating?: number) {
		video?.pause();
		dialogs.openFeedback({ videoId: id, categoryId: catId ?? inCat?.category_id ?? null, title, rating });
	}

	async function quickFeedback(rating: number, label: string) {
		await api.feedback({ video_id: id, category_id: catId ?? inCat?.category_id ?? null, rating });
		toast.success(`${label}. The AI will learn from this.`);
	}

	async function channelAction(body: { subscribed?: boolean; blocked?: boolean }) {
		if (!detail?.channel?.id) return;
		// Blocking also dismisses the channel's other suggestions in your categories.
		const r = body.blocked
			? await api.blockChannel(detail.channel.id, detail.channel.name || detail.channel_name)
			: await api.putChannel(detail.channel.id, { ...body, name: detail.channel.name || detail.channel_name });
		detail.channel = { ...detail.channel, subscribed: r.subscribed, blocked: r.blocked };
		toast.success(body.blocked ? `Blocked ${detail.channel_name}. The AI will skip this channel.` : r.subscribed ? `Subscribed to ${detail.channel_name}` : `Unsubscribed from ${detail.channel_name}`);
	}

	// ---------- keys
	function onKey(key: NavKey, e: KeyboardEvent): boolean {
		if (dialogs.feedback || dialogs.addTo) return false;
		switch (key) {
			case 'playpause':
				if (e.key === ' ' || e.key.startsWith('Media')) {
					togglePlay();
					return true;
				}
				return false;
			case 'next':
				playNext();
				return true;
			case 'prev':
				seekTo(0);
				return true;
			case 'ff':
				seekBy(30);
				return true;
			case 'rw':
				seekBy(-30);
				return true;
			case 'back':
				if (menu) {
					menu = null;
					return true;
				}
				if (info) return false; // scope handles it
				exit();
				return true;
		}
		if (!controls && !menu && !info) {
			// Wake the controls on any key; OK also toggles play.
			if (key === 'select') togglePlay();
			else if (key === 'left') seekBy(-10);
			else if (key === 'right') seekBy(10);
			poke();
			tick().then(() => {
				const bar = document.querySelector<HTMLElement>('#seekbar');
				if (bar && !askSeg) nav.focus(bar, { scroll: false });
			});
			return true;
		}
		poke();
		return false;
	}

	const seekKey = (key: NavKey) => {
		if (key === 'left') return seekBy(-10), true;
		if (key === 'right') return seekBy(10), true;
		if (key === 'select') return togglePlay(), true;
		return false;
	};
	const downOpensInfo = (key: NavKey) => {
		if (key === 'down' && !ended) {
			info = true;
			return true;
		}
		return false;
	};

	function onBarPointer(e: PointerEvent) {
		const el = e.currentTarget as HTMLElement;
		const r = el.getBoundingClientRect();
		seekTo(((e.clientX - r.left) / r.width) * duration);
		poke();
	}

	// ---------- lifecycle
	let skipExitHistory = false;
	const beforeCast = () => { saveHistory(true); skipExitHistory = true; };
	let offKeys: (() => void) | null = null;
	const onVis = () => document.visibilityState === 'hidden' && saveHistory(true);
	onMount(() => {
		offKeys = nav.handleKeys(onKey);
		document.addEventListener('visibilitychange', onVis);
		window.addEventListener('pagehide', onVis);
		window.addEventListener('bt:before-cast', beforeCast);
	});
	$effect(() => {
		const v = id;
		void castCommand; // A new cast of the same video must also reload the player.
		if (v && video) { skipExitHistory = false; load(v); }
	});
	onDestroy(() => {
		if (!skipExitHistory) saveHistory(true);
		offKeys?.();
		document.removeEventListener('visibilitychange', onVis);
		window.removeEventListener('pagehide', onVis);
		window.removeEventListener('bt:before-cast', beforeCast);
		if (hideTimer) clearTimeout(hideTimer);
		if (cdTimer) clearInterval(cdTimer);
		if (slowTimer) clearTimeout(slowTimer);
		loadSeq++;
		teardown();
	});

	const SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2];
	const thumbUrl = $derived(id ? api.thumb(id, 'maxres') : '');
</script>


<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="watch" class:hide-cursor={!controls} onpointermove={poke} onpointerdown={poke}>
	<!-- svelte-ignore a11y_media_has_caption -->
	<video
		bind:this={video}
		poster={thumbUrl}
		playsinline
		crossorigin="use-credentials"
		onplay={() => {
			paused = false;
			ended = false;
			lastBeat = Date.now();
			api.agentPlaying(90).catch(() => {});
			poke();
		}}
		onpause={() => {
			paused = true;
			saveHistory();
		}}
		ontimeupdate={onTime}
		ondurationchange={() => (duration = Number.isFinite(video!.duration) ? video!.duration : duration)}
		onwaiting={() => (waiting = true)}
		onplaying={() => (waiting = false)}
		oncanplay={() => (waiting = false)}
		onended={onEnded}
		onclick={togglePlay}
	></video>

	{#if loading && !loadError}
		<div class="preparing" aria-live="polite">
			<img src={api.thumb(id, 'hq')} alt="" />
			<div class="prep-body">
				<div class="spinner static" aria-hidden="true"></div>
				<p class="prep-title">Preparing video…</p>
				{#if title}<p class="muted">{title}</p>{/if}
				{#if slow}<p class="small faint">The first play of a video takes a little longer while the server fetches it from YouTube.</p>{/if}
			</div>
		</div>
	{:else if waiting && !loadError}
		<div class="spinner" aria-label="Buffering"></div>
	{/if}

	{#if loadError}
		<div class="error-card glass" use:navScope={{ onBack: exit }}>
			<h2>Can't play this video</h2>
			<p class="muted">{loadError}</p>
			<div class="hstack">
				<button class="btn primary" use:focusable={{ autofocus: true, onSelect: () => load(id) }}>Try again</button>
				<button class="btn" use:focusable={{ onSelect: exit }}>Go back</button>
			</div>
		</div>
	{/if}

	{#if askSeg}
		<div class="sb-ask glass" style:--seg={SB_COLORS[askSeg.category] ?? '#fff'}>
			<span class="dot"></span>
			<span>{SB_LABELS[askSeg.category] ?? askSeg.category} ({fmtDuration(askSeg.end - askSeg.start)})</span>
			<button id="sb-skip" class="btn primary" use:focusable={{ group: 'sb', scroll: 'none', onSelect: skipAsked }}>Skip</button>
		</div>
	{/if}

	{#if !ended}
		<div class="overlay" class:show={controls} data-nav-noscroll inert={!controls}>
			<div class="top">
				<button class="btn icon round" aria-label="Back" use:focusable={{ group: 'p-top', scroll: 'none', onSelect: exit }}><Icon name="back" /></button>
				<div class="ttl">
					<h1>{title || ' '}</h1>
					<p class="muted">
						{detail?.channel_name ?? ''}
						{#if category}<a class="catchip" href="/category/{category.id}" style:--cat={category.color} use:focusable={{ group: 'p-top', id: 'p-cat', scroll: 'none' }}>{category.emoji} {category.name}</a>{/if}
						{#if chapter}<span class="chap">{chapter.title}</span>{/if}
					</p>
				</div>
			</div>

			<div class="bottom">
				<div class="times">
					<span>{fmtDuration(time)}</span>
					<span class="muted">{fmtDuration(duration)}</span>
				</div>
				<div
					id="seekbar"
					class="bar"
					role="slider"
					tabindex="0"
					aria-label="Seek"
					aria-valuemin={0}
					aria-valuemax={Math.round(duration)}
					aria-valuenow={Math.round(time)}
					aria-valuetext={fmtDuration(time)}
					use:focusable={{ group: 'p-bar', id: 'seekbar', autofocus: true, scroll: 'none', onKey: seekKey }}
					onpointerdown={(e) => {
						seeking = true;
						(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
						onBarPointer(e);
					}}
					onpointermove={(e) => seeking && onBarPointer(e)}
					onpointerup={() => (seeking = false)}
				>
					<div class="rail">
						<div class="buf" style:width="{duration ? (buffered / duration) * 100 : 0}%"></div>
						{#each segments as s (s.uuid)}
							{#if duration}
								<div
									class="seg"
									class:poi={s.poi}
									title={SB_LABELS[s.category] ?? s.category}
									style:left="{(s.start / duration) * 100}%"
									style:width={s.poi ? undefined : `${Math.max(0.3, ((s.end - s.start) / duration) * 100)}%`}
									style:--seg={SB_COLORS[s.category] ?? '#fff'}
								></div>
							{/if}
						{/each}
						<div class="fill" style:width="{progressPct}%"></div>
						{#each chapters as c}
							{#if duration && c.start_time > 0}<div class="chapter" style:left="{(c.start_time / duration) * 100}%"></div>{/if}
						{/each}
					</div>
					<div class="knob" style:left="{progressPct}%"></div>
				</div>

				<div class="btns">
					<button class="btn icon round big" aria-label={paused ? 'Play' : 'Pause'} use:focusable={{ group: 'p-btns', id: 'p-play', scroll: 'none', onSelect: togglePlay, onKey: downOpensInfo }}>
						<Icon name={paused ? 'play' : 'pause'} size="1.4em" />
					</button>
					<button class="btn icon round" aria-label="Back 10 seconds" use:focusable={{ group: 'p-btns', scroll: 'none', onSelect: () => seekBy(-10), onKey: downOpensInfo }}><Icon name="rewind" /></button>
					<button class="btn icon round" aria-label="Forward 10 seconds" use:focusable={{ group: 'p-btns', scroll: 'none', onSelect: () => seekBy(10), onKey: downOpensInfo }}><Icon name="forward" /></button>
					{#if upNext.length}
						<button class="btn icon round" aria-label="Next video" use:focusable={{ group: 'p-btns', scroll: 'none', onSelect: playNext, onKey: downOpensInfo }}><Icon name="next" /></button>
					{/if}
					<span class="spacer"></span>
					<button class="btn fb" aria-label="Like" use:focusable={{ group: 'p-btns', scroll: 'none', onSelect: () => quickFeedback(1, 'Liked'), onKey: downOpensInfo }}>👍</button>
					<button class="btn fb" aria-label="Love" use:focusable={{ group: 'p-btns', scroll: 'none', onSelect: () => quickFeedback(2, 'Loved'), onKey: downOpensInfo }}>❤️</button>
					<button class="btn fb" aria-label="Dislike" use:focusable={{ group: 'p-btns', scroll: 'none', onSelect: () => feedback(-1), onKey: downOpensInfo }}>👎</button>
					<button class="btn fb" aria-label="Write feedback" use:focusable={{ group: 'p-btns', scroll: 'none', onSelect: () => feedback(), onKey: downOpensInfo }}>📝</button>
					<span class="sep"></span>
					<button class="btn" class:on={!!captionKey} aria-label="Captions" disabled={!captions.length} use:focusable={{ group: 'p-btns', scroll: 'none', disabled: !captions.length, onSelect: () => (menu = 'captions'), onKey: downOpensInfo }}><Icon name="cc" /><span class="lbl">CC</span></button>
					<button class="btn" aria-label="Speed" use:focusable={{ group: 'p-btns', scroll: 'none', onSelect: () => (menu = 'speed'), onKey: downOpensInfo }}><Icon name="speed" /><span class="lbl">{speed}×</span></button>
					<button class="btn" aria-label="Quality" use:focusable={{ group: 'p-btns', scroll: 'none', onSelect: () => (menu = 'quality'), onKey: downOpensInfo }}>
						<Icon name="quality" /><span class="lbl">{mode === 'offline' ? `Offline ${detail?.download?.height || video?.videoHeight || ''}p` : mode === 'fallback' ? 'Basic' : quality === 'auto' ? 'Auto' : `${quality}p`}</span>
					</button>
					<button class="btn" aria-label="Info" use:focusable={{ group: 'p-btns', id: 'p-info', scroll: 'none', onSelect: () => (info = true), onKey: downOpensInfo }}><Icon name="info" /><span class="lbl">Info</span></button>
				</div>
				<p class="hint faint tiny">Down for details. Back to leave.</p>
			</div>
		</div>
	{/if}

	{#if menu}
		<div class="menu glass" role="menu" use:navScope={{ onBack: () => (menu = null) }}>
			{#if menu === 'speed'}
				<h3>Playback speed</h3>
				{#each SPEEDS as s}
					<button class="opt" class:sel={s === speed} use:focusable={{ autofocus: s === speed, onSelect: () => setSpeed(s) }}>{s === 1 ? 'Normal' : `${s}×`}</button>
				{/each}
			{:else if menu === 'quality'}
				<h3>Quality</h3>
				{#if mode === 'offline'}
					<p class="small muted">Playing the copy saved on this server{detail?.download?.height ? ` (${detail.download.height}p)` : ''}. No YouTube traffic.</p>
					<button class="opt" use:focusable={{ autofocus: true, onSelect: () => (menu = null) }}>OK</button>
				{:else if mode === 'fallback'}
					<p class="small muted">Basic mode: the high-quality stream did not load, so a single combined stream is playing.</p>
					<button class="opt" use:focusable={{ autofocus: true, onSelect: () => (menu = null) }}>OK</button>
				{:else}
					<button class="opt" class:sel={quality === 'auto'} use:focusable={{ autofocus: quality === 'auto', onSelect: () => setQuality('auto') }}>Auto</button>
					{#each qualities as q}
						<button class="opt" class:sel={quality === q} use:focusable={{ autofocus: quality === q, onSelect: () => setQuality(q) }}>{q}p{q >= 720 ? ' HD' : ''}</button>
					{/each}
				{/if}
			{:else}
				<h3>Captions</h3>
				<button class="opt" class:sel={!captionKey} use:focusable={{ autofocus: !captionKey, onSelect: () => setCaption(null) }}>Off</button>
				{#each captions as c}
					<button class="opt" class:sel={captionKey === c.key} use:focusable={{ autofocus: captionKey === c.key, onSelect: () => setCaption(c.key) }}>{c.name}{c.auto ? ' (auto)' : ''}</button>
				{/each}
			{/if}
		</div>
	{/if}

	{#if info && detail}
		<div class="info glass" use:navScope={{ onBack: () => (info = false) }}>
			<div class="info-inner">
				<div class="info-main">
					<h2>{title}</h2>
					<div class="stats hstack muted small">
						<span>{fmtCount(detail.view_count)} views</span>
						<span>👍 {fmtCount(detail.like_count || detail.dislikes?.likes)}</span>
						{#if detail.dislikes?.dislikes != null}<span>👎 {fmtCount(detail.dislikes.dislikes)}</span>{/if}
						{#if detail.published_at}<span>{timeAgo(detail.published_at)}</span>{/if}
						<span>{fmtDuration(detail.duration_s)}</span>
					</div>
					<div class="chan">
						{#if detail.channel?.id}<img class="cav" src={detail.channel.avatar} alt="" onerror={(e) => ((e.currentTarget as HTMLImageElement).style.visibility = 'hidden')} />{/if}
						<div class="cn">
							<strong>{detail.channel_name}</strong>
							{#if detail.channel?.blocked}<span class="small" style="color:var(--bad)">Blocked</span>{/if}
						</div>
						{#if detail.channel?.id}
							<a class="btn" href="/channel/{detail.channel.id}" use:focusable={{ group: 'i-chan', scroll: 'nearest' }}>Channel</a>
							<button class="btn" class:primary={!detail.channel.subscribed} use:focusable={{ group: 'i-chan', autofocus: true, scroll: 'nearest', onSelect: () => channelAction({ subscribed: !detail!.channel!.subscribed }) }}>
								{detail.channel.subscribed ? 'Unsubscribe' : 'Subscribe'}
							</button>
							<button class="btn danger" use:focusable={{ group: 'i-chan', scroll: 'nearest', onSelect: () => channelAction({ blocked: !detail!.channel!.blocked }) }}>
								{detail.channel.blocked ? 'Unblock' : 'Block channel'}
							</button>
						{/if}
					</div>
					<div class="hstack">
						<button class="btn" use:focusable={{ group: 'i-fb', scroll: 'nearest', onSelect: () => quickFeedback(1, 'Liked') }}>👍 Like</button>
						<button class="btn" use:focusable={{ group: 'i-fb', scroll: 'nearest', onSelect: () => quickFeedback(2, 'Loved') }}>❤️ Love</button>
						<button class="btn" use:focusable={{ group: 'i-fb', scroll: 'nearest', onSelect: () => feedback(-1) }}>👎 Not for us</button>
						<button class="btn" use:focusable={{ group: 'i-fb', scroll: 'nearest', onSelect: () => feedback() }}>📝 Feedback</button>
						<button class="btn" use:focusable={{ group: 'i-fb', scroll: 'nearest', onSelect: () => dialogs.openAddTo(id, title) }}>＋ Add to playlist</button>
						{#key detail.id}
							<OfflineButton videoId={id} status={detail.download?.status ?? null} progress={detail.download?.progress ?? 0} group="i-fb" size="normal" />
						{/key}
					</div>
					{#if detail.description}
						<p class="desc" aria-label="Description" use:focusable={{ group: 'i-desc', scroll: 'nearest', onKey: scrollByKeys }}>{detail.description}</p>
					{/if}
				</div>
				<aside class="info-ai">
					{#if detail.in_categories?.length}
						{#each detail.in_categories as ic}
							{@const c = session.categoryById(ic.category_id)}
							<div class="ai-card" style:--cat={c?.color ?? 'var(--ai)'}>
								<div class="hstack small">
									<strong>
										{#if c}<a class="aicat" href="/category/{c.id}" use:focusable={{ group: 'i-ai', id: `ai-cat-${ic.category_id}` }}>{c.emoji} {c.name}</a>{:else}Playlist{/if}
									</strong>
									{#if ic.score != null}<span class="score">✦ {ic.score}</span>{/if}
								</div>
								{#if ic.summary}<p>{ic.summary}</p>{/if}
								{#if ic.reasons?.length}
									<ul>{#each ic.reasons as r}<li>{r}</li>{/each}</ul>
								{/if}
								<button class="btn" use:focusable={{ group: 'i-ai', scroll: 'nearest', onSelect: () => dialogs.openCandidate(ic.candidate_id) }}>Full AI reasoning</button>
							</div>
						{/each}
					{:else}
						<p class="muted small">You found this one yourself. The AI did not review it.</p>
					{/if}
					{#if chapters.length}
						<h3>Chapters</h3>
						<div class="chapters">
							{#each chapters as c, i}
								<button class="opt" use:focusable={{ group: 'i-chapters', scroll: 'nearest', onSelect: () => (seekTo(c.start_time), (info = false)) }}>
									<span class="faint">{fmtDuration(c.start_time)}</span> {c.title}
								</button>
							{/each}
						</div>
					{/if}
				</aside>
			</div>
			<button class="btn close" use:focusable={{ scroll: 'nearest', onSelect: () => (info = false) }}>Close details</button>
		</div>
	{/if}

	{#if ended}
		<div class="endscreen" use:navScope={{ onBack: exit }}>
			<div class="end-inner">
				<h2>How was "{title}"?</h2>
				<div class="hstack">
					<button class="btn big" use:focusable={{ group: 'e-fb', autofocus: !countdown, onSelect: () => quickFeedback(2, 'Loved') }}>❤️ Loved it</button>
					<button class="btn big" use:focusable={{ group: 'e-fb', onSelect: () => quickFeedback(1, 'Liked') }}>👍 Good</button>
					<button class="btn big" use:focusable={{ group: 'e-fb', onSelect: () => ((countdown = null), feedback(-1)) }}>👎 Not for us</button>
					<button class="btn big" use:focusable={{ group: 'e-fb', onSelect: () => ((countdown = null), feedback()) }}>📝 Tell the AI more</button>
				</div>
				{#if upNext.length}
					<h3>Up next{category ? ` in ${category.emoji} ${category.name}` : ''}</h3>
					<div class="next-row">
						{#each upNext.slice(0, 4) as n, i (n.id)}
							{@const nv = isVideo(n.video) ? n.video : null}
							<a class="next" href="/watch/{n.video.id}?c={catId ?? ''}" data-sveltekit-replacestate use:focusable={{ group: 'e-next', autofocus: i === 0 && countdown != null }}>
								<div class="nthumb" use:preview={{ id: n.video.id, offline: !!nv?.offline }}>
									<img src={api.thumb(n.video.id)} alt="" />
									{#if i === 0 && countdown != null}
										<div class="cd"><span>{countdown}</span></div>
									{/if}
								</div>
								<span class="nt">{nv?.title ?? ''}</span>
							</a>
						{/each}
					</div>
					{#if countdown != null}
						<button class="btn" use:focusable={{ group: 'e-ctl', onSelect: () => (countdown = null) }}>Cancel autoplay</button>
					{/if}
				{/if}
				<div class="hstack">
					<button class="btn" use:focusable={{ group: 'e-ctl', onSelect: replay }}>↺ Watch again</button>
					<button class="btn" use:focusable={{ group: 'e-ctl', onSelect: exit }}>Back</button>
				</div>
			</div>
		</div>
	{/if}
</div>

<style>
	.watch {
		position: fixed;
		inset: 0;
		background: #000;
		z-index: 50;
		overflow: hidden;
	}
	.watch.hide-cursor {
		cursor: none;
	}
	video {
		width: 100%;
		height: 100%;
		object-fit: contain;
		background: #000;
	}
	video::cue {
		font-family: var(--font);
		font-size: clamp(1.1rem, 1.6vw, 2.2rem);
		background: rgba(0, 0, 0, 0.72);
		color: #fff;
		line-height: 1.3;
	}
	.spinner {
		position: absolute;
		top: 50%;
		left: 50%;
		width: 4rem;
		height: 4rem;
		margin: -2rem 0 0 -2rem;
		border-radius: 50%;
		border: 4px solid rgba(255, 255, 255, 0.15);
		border-top-color: var(--accent);
		animation: spin 0.9s linear infinite;
		pointer-events: none;
	}
	.preparing {
		position: absolute;
		inset: 0;
		z-index: 25;
		display: grid;
		place-items: center;
		overflow: hidden;
		background: #05040e;
	}
	.preparing img {
		position: absolute;
		inset: -5%;
		width: 110%;
		height: 110%;
		object-fit: cover;
		filter: blur(28px) brightness(0.45) saturate(1.2);
	}
	.prep-body {
		position: relative;
		display: grid;
		justify-items: center;
		gap: 0.7rem;
		text-align: center;
		max-width: min(90vw, 44rem);
		padding: 1rem;
	}
	.prep-title {
		font-size: clamp(1.3rem, 1.2vw + 0.8rem, 2rem);
		font-weight: 700;
	}
	.spinner.static {
		position: static;
		margin: 0 0 0.5rem;
	}
	.overlay {
		position: absolute;
		inset: 0;
		display: flex;
		flex-direction: column;
		justify-content: space-between;
		opacity: 0;
		transition: opacity 0.35s var(--ease);
		pointer-events: none;
		background:
			linear-gradient(180deg, rgba(0, 0, 0, 0.75), transparent 22%),
			linear-gradient(0deg, rgba(4, 3, 14, 0.92), transparent 38%);
	}
	.overlay.show {
		opacity: 1;
	}
	.overlay.show .top, .overlay.show .bottom {
		pointer-events: auto;
	}
	.top {
		display: flex;
		align-items: flex-start;
		gap: 1rem;
		padding: clamp(1rem, 2.5vw, 2.5rem) var(--gutter) 0;
	}
	.ttl {
		min-width: 0;
		display: grid;
		gap: 0.35rem;
	}
	.ttl h1 {
		font-size: clamp(1.2rem, 1.2vw + 0.8rem, 2.2rem);
		display: -webkit-box;
		-webkit-line-clamp: 2;
		line-clamp: 2;
		-webkit-box-orient: vertical;
		overflow: hidden;
	}
	.ttl p {
		display: flex;
		gap: 0.75rem;
		align-items: center;
		flex-wrap: wrap;
	}
	.catchip {
		padding: 0.1rem 0.6rem;
		border-radius: 1rem;
		font-size: 0.8rem;
		font-weight: 650;
		color: #fff;
		background: color-mix(in srgb, var(--cat) 35%, transparent);
	}
	a.catchip:hover {
		background: color-mix(in srgb, var(--cat) 55%, transparent);
	}
	.chap {
		font-size: 0.85rem;
		color: var(--accent-2);
	}
	.btn.round {
		border-radius: 50%;
		width: 3rem;
		height: 3rem;
		min-width: 3rem;
	}
	.btn.round.big {
		width: 3.8rem;
		height: 3.8rem;
		background: var(--text);
		color: #0a0817;
	}
	.bottom {
		padding: 0 var(--gutter) clamp(1rem, 2.5vw, 2.4rem);
		display: grid;
		gap: 0.8rem;
	}
	.times {
		display: flex;
		justify-content: space-between;
		font-variant-numeric: tabular-nums;
		font-weight: 600;
		font-size: 0.95rem;
	}
	.bar {
		--focus-scale: 1;
		position: relative;
		height: 2.2rem;
		display: flex;
		align-items: center;
		cursor: pointer;
		border-radius: 0.6rem;
		touch-action: none;
	}
	:global(html[data-modality='keys']) .bar:focus {
		box-shadow: none;
		transform: none;
	}
	.rail {
		position: relative;
		width: 100%;
		height: 0.4rem;
		border-radius: 1rem;
		background: rgba(255, 255, 255, 0.2);
		overflow: visible;
		transition: height 0.2s var(--ease);
	}
	.bar:hover .rail,
	.bar:focus .rail {
		height: 0.7rem;
	}
	.buf {
		position: absolute;
		inset: 0 auto 0 0;
		background: rgba(255, 255, 255, 0.25);
		border-radius: inherit;
	}
	.fill {
		position: absolute;
		inset: 0 auto 0 0;
		background: var(--accent);
		border-radius: inherit;
	}
	.seg {
		position: absolute;
		top: 0;
		bottom: 0;
		background: var(--seg);
		opacity: 0.9;
		z-index: 1;
	}
	.seg.poi {
		width: 0.35rem;
		border-radius: 1rem;
	}
	.chapter {
		position: absolute;
		top: -1px;
		bottom: -1px;
		width: 3px;
		background: rgba(4, 3, 14, 0.9);
		z-index: 2;
	}
	.knob {
		position: absolute;
		top: 50%;
		width: 1.2rem;
		height: 1.2rem;
		border-radius: 50%;
		background: #fff;
		transform: translate(-50%, -50%) scale(0.6);
		transition: transform 0.2s var(--ease-spring);
		box-shadow: 0 2px 10px rgba(0, 0, 0, 0.6);
		z-index: 3;
	}
	.bar:focus .knob,
	.bar:hover .knob {
		transform: translate(-50%, -50%) scale(1.2);
	}
	:global(html[data-modality='keys']) .bar:focus .knob {
		box-shadow:
			0 0 0 4px var(--accent),
			0 0 24px 6px var(--accent-glow);
	}
	.btns {
		display: flex;
		align-items: center;
		gap: 0.6rem;
		flex-wrap: wrap;
	}
	.btns .btn.on {
		border-color: var(--accent);
		color: var(--accent);
	}
	.btn.fb {
		font-size: 1.15rem;
		padding: 0 0.8rem;
	}
	.sep {
		width: 1px;
		height: 1.8rem;
		background: var(--line-strong);
	}
	.hint {
		text-align: center;
	}
	.sb-ask {
		position: absolute;
		right: var(--gutter);
		bottom: clamp(9rem, 22vh, 14rem);
		display: flex;
		align-items: center;
		gap: 0.8rem;
		padding: 0.6rem 0.7rem 0.6rem 1.1rem;
		border-radius: var(--r-pill);
		background: rgba(18, 15, 40, 0.9);
		z-index: 20;
		font-weight: 600;
		animation: rise 0.25s var(--ease);
	}
	.sb-ask .dot {
		width: 0.7rem;
		height: 0.7rem;
		border-radius: 50%;
		background: var(--seg);
	}
	.error-card {
		position: absolute;
		top: 50%;
		left: 50%;
		transform: translate(-50%, -50%);
		width: min(90vw, 34rem);
		padding: 1.6rem;
		border-radius: var(--r-lg);
		display: grid;
		gap: 0.9rem;
		z-index: 30;
		background: var(--overlay-surface);
	}
	.menu {
		position: absolute;
		right: var(--gutter);
		bottom: clamp(8rem, 18vh, 12rem);
		width: min(90vw, 20rem);
		max-height: 60vh;
		overflow: auto;
		display: grid;
		gap: 0.35rem;
		padding: 1rem;
		border-radius: var(--r-lg);
		background: var(--overlay-surface-deep);
		z-index: 40;
		animation: rise 0.2s var(--ease);
	}
	.menu h3 {
		margin-bottom: 0.4rem;
	}
	.opt {
		--focus-scale: 1.03;
		text-align: left;
		padding: 0.65rem 0.9rem;
		border-radius: var(--r-md);
		border: 1px solid transparent;
		background: rgba(255, 255, 255, 0.05);
		cursor: pointer;
		font-weight: 600;
	}
	.opt.sel {
		border-color: var(--accent);
		color: var(--accent-2);
	}
	.info {
		position: absolute;
		left: 0;
		right: 0;
		bottom: 0;
		max-height: 78vh;
		overflow: auto;
		z-index: 45;
		padding: clamp(1.2rem, 2.4vw, 2.6rem) var(--gutter);
		border-radius: var(--r-lg) var(--r-lg) 0 0;
		background: rgba(14, 12, 34, 0.94);
		animation: up 0.3s var(--ease);
		display: grid;
		gap: 1.2rem;
	}
	@keyframes up {
		from {
			transform: translateY(40px);
			opacity: 0;
		}
	}
	.info-inner {
		display: grid;
		grid-template-columns: minmax(0, 1.6fr) minmax(0, 1fr);
		gap: clamp(1rem, 3vw, 3rem);
	}
	@media (max-width: 900px) {
		.info-inner {
			grid-template-columns: 1fr;
		}
	}
	.info-main,
	.info-ai {
		display: grid;
		gap: 1rem;
		align-content: start;
	}
	.chan {
		display: flex;
		align-items: center;
		gap: 0.8rem;
		flex-wrap: wrap;
	}
	.cav {
		width: 3rem;
		height: 3rem;
		border-radius: 50%;
		object-fit: cover;
	}
	.cn {
		display: grid;
		flex: 1;
		min-width: 8rem;
	}
	.desc {
		--focus-scale: 1;
		white-space: pre-wrap;
		color: var(--muted);
		font-size: 0.92rem;
		max-height: 18rem;
		overflow: auto;
		padding: 1rem;
		border-radius: var(--r-md);
		background: rgba(255, 255, 255, 0.03);
	}
	.ai-card {
		display: grid;
		gap: 0.6rem;
		padding: 1rem;
		border-radius: var(--r-md);
		background: color-mix(in srgb, var(--cat) 10%, rgba(255, 255, 255, 0.02));
		border: 1px solid color-mix(in srgb, var(--cat) 35%, transparent);
	}
	.ai-card a.aicat {
		color: inherit;
	}
	.ai-card a.aicat:hover {
		text-decoration: underline;
		text-decoration-color: var(--cat, var(--ai));
		text-underline-offset: 0.3rem;
	}
	.ai-card ul {
		margin: 0;
		padding-left: 1.2rem;
		font-size: 0.9rem;
		color: var(--muted);
	}
	.chapters {
		display: grid;
		gap: 0.3rem;
	}
	.close {
		justify-self: center;
	}
	.endscreen {
		position: absolute;
		inset: 0;
		z-index: 35;
		display: grid;
		place-items: center;
		padding: var(--gutter);
		background: radial-gradient(80% 80% at 50% 40%, rgba(30, 24, 70, 0.85), rgba(4, 3, 14, 0.96));
		overflow: auto;
		animation: rise 0.3s var(--ease);
	}
	.end-inner {
		display: grid;
		gap: 1.4rem;
		justify-items: center;
		text-align: center;
		width: min(100%, 80rem);
	}
	.next-row {
		display: flex;
		gap: 1.2rem;
		flex-wrap: wrap;
		justify-content: center;
	}
	.next {
		--focus-scale: 1.06;
		width: clamp(12rem, 16vw, 20rem);
		display: grid;
		gap: 0.5rem;
		text-align: left;
		border-radius: var(--r-md);
	}
	.nthumb {
		position: relative;
		aspect-ratio: 16/9;
		border-radius: var(--r-md);
		overflow: hidden;
	}
	.nthumb img {
		width: 100%;
		height: 100%;
		object-fit: cover;
	}
	.cd {
		position: absolute;
		inset: 0;
		display: grid;
		place-items: center;
		background: rgba(0, 0, 0, 0.5);
	}
	.cd span {
		display: grid;
		place-items: center;
		width: 3.5rem;
		height: 3.5rem;
		border-radius: 50%;
		border: 3px solid var(--accent);
		font-size: 1.5rem;
		font-weight: 800;
	}
	.nt {
		font-weight: 600;
		font-size: 0.9rem;
		display: -webkit-box;
		-webkit-line-clamp: 2;
		line-clamp: 2;
		-webkit-box-orient: vertical;
		overflow: hidden;
	}
	@media (max-width: 760px) {
		.lbl,
		.hint {
			display: none;
		}
		.btns {
			gap: 0.4rem;
		}
	}
</style>
