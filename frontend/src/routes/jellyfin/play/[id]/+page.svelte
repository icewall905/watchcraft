<script lang="ts">
	import { onDestroy, onMount, tick, untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { focusable, navScope, nav, type NavKey } from '$lib/nav';
	import Icon from '$lib/components/Icon.svelte';
	import { loadShakaHls } from '$lib/player/shaka';
	import { QUALITIES, deviceCaps, fmtTime, jellyfin, type Play, type Playing } from '$lib/jellyfin/api';
	import { playback } from '$lib/stores/playback.svelte';

	const id = $derived(page.params.id ?? '');

	/** Hand this title to another screen at the current position; this one pauses. */
	function playOn() {
		video?.pause();
		void playback.castJellyfin(id, current);
	}

	let video = $state<HTMLVideoElement | null>(null);
	let shakaPlayer: any = null; // shaka.Player for HLS; direct play uses video.src
	let info = $state<Play | null>(null);
	let failed = $state('');
	let loading = $state(true);
	let paused = $state(true);
	let current = $state(0);
	let duration = $state(0);
	let buffering = $state(false);
	let controls = $state(true);
	let menu = $state<null | 'audio' | 'subs' | 'quality' | 'info'>(null);
	let quality = $state(0);
	let textIndex = $state<number | null>(null); // client-side text subtitle shown
	let serverInfo = $state<Playing | null>(null);
	let upNext = $state<number | null>(null); // countdown seconds
	let loadSeq = 0;
	let hideTimer: ReturnType<typeof setTimeout> | undefined;
	let progressTimer: ReturnType<typeof setInterval> | undefined;
	let infoTimer: ReturnType<typeof setInterval> | undefined;
	let nextTimer: ReturnType<typeof setInterval> | undefined;

	async function teardown(position?: number) {
		const s = info?.session;
		if (s) void jellyfin.progress(s, position ?? video?.currentTime ?? 0, true, 'stopped', true).catch(() => {});
		clearInterval(progressTimer);
		if (shakaPlayer) {
			const p = shakaPlayer;
			shakaPlayer = null;
			await p.destroy().catch(() => {});
		}
		if (video) {
			video.removeAttribute('src');
			video.load();
		}
	}

	async function load(itemId: string, opts: { start?: number; audio?: number | null; subtitle?: number | null } = {}) {
		const mine = ++loadSeq;
		loading = true;
		failed = '';
		upNext = null;
		clearInterval(nextTimer);
		const q = QUALITIES[quality];
		const startParam = page.url.searchParams.get('start');
		try {
			const r = await jellyfin.play(itemId, {
				caps: deviceCaps(), max_bitrate: q.bitrate, force_transcode: q.force,
				start_s: opts.start ?? (startParam != null ? Number(startParam) || 0 : undefined),
				audio_index: opts.audio, subtitle_index: opts.subtitle
			});
			if (mine !== loadSeq || !video) return;
			info = r;
			duration = r.duration_s;
			const text = r.subtitles.find((s) => s.index === r.subtitle_index && s.url && !s.burned);
			textIndex = text ? text.index : null;
			if (r.kind === 'hls') {
				const shaka = await loadShakaHls();
				if (mine !== loadSeq) return;
				shakaPlayer = new shaka.Player();
				await shakaPlayer.attach(video);
				shakaPlayer.configure({ streaming: { bufferingGoal: 30, rebufferingGoal: 2 } });
				shakaPlayer.addEventListener('error', (ev: any) => { console.warn('jellyfin shaka error', ev.detail); failed = `Playback error ${ev.detail?.code ?? ''}`.trim(); });
				await shakaPlayer.load(r.url, r.start_s || undefined);
			} else {
				video.src = r.url;
				if (r.start_s) video.addEventListener('loadedmetadata', () => { if (video) video.currentTime = r.start_s; }, { once: true });
			}
			if (mine !== loadSeq) return;
			await tick();
			applyText();
			await video.play().catch(() => {});
			clearInterval(progressTimer);
			progressTimer = setInterval(report, 10_000);
		} catch (e) {
			if (mine === loadSeq) failed = (e as Error).message || 'Jellyfin could not start this.';
		} finally {
			if (mine === loadSeq) loading = false;
		}
	}

	function applyText() {
		if (!video) return;
		for (const t of Array.from(video.textTracks)) {
			const idx = Number((t as TextTrack & { id: string }).id?.replace('jf-sub-', ''));
			t.mode = t.kind === 'subtitles' && (t as TextTrack & { id: string }).id?.startsWith('jf-sub-') && idx === textIndex ? 'showing' : 'disabled';
		}
	}

	function report() {
		if (info && video) void jellyfin.progress(info.session, video.currentTime, video.paused).catch(() => {});
	}

	async function reload(opts: { audio?: number | null; subtitle?: number | null }) {
		const at = video?.currentTime ?? 0;
		const audio = opts.audio !== undefined ? opts.audio : info?.audio_index;
		const subtitle = opts.subtitle !== undefined ? opts.subtitle : info?.subtitle_index;
		menu = null;
		await teardown(at);
		await load(id, { start: at, audio, subtitle });
	}

	function pickSub(index: number) {
		const s = info?.subtitles.find((x) => x.index === index);
		if (index === -1 || (s && s.url && !s.burned)) {
			// Text subtitles switch on this device; a burned-in one was baked into the old stream.
			const wasBurned = info?.subtitles.find((x) => x.index === info?.subtitle_index)?.burned;
			textIndex = index === -1 ? null : index;
			if (wasBurned) return void reload({ subtitle: index });
			if (info) info.subtitle_index = index;
			applyText();
			menu = null;
			return;
		}
		void reload({ subtitle: index }); // image subtitles: Jellyfin burns them in
	}

	function pickQuality(i: number) {
		quality = i;
		void reload({});
	}

	function togglePlay() {
		if (!video) return;
		if (video.paused) void video.play(); else video.pause();
		poke();
	}
	function seekTo(t: number) {
		if (!video) return;
		video.currentTime = Math.max(0, Math.min(t, (duration || video.duration || 0) - 1));
		poke();
	}
	const seekBy = (d: number) => seekTo((video?.currentTime ?? 0) + d);

	function poke() {
		controls = true;
		clearTimeout(hideTimer);
		hideTimer = setTimeout(() => { if (!paused && !menu) controls = false; }, 3500);
	}

	function exit() {
		nav.backFallback();
	}

	function onEnded() {
		report();
		if (!info?.next) return void (info && jellyfin.progress(info.session, duration, true, 'stopped').catch(() => {}), exit());
		upNext = 10;
		clearInterval(nextTimer);
		nextTimer = setInterval(() => {
			if (upNext == null) return clearInterval(nextTimer);
			upNext -= 1;
			if (upNext <= 0) playNext();
		}, 1000);
	}

	function playNext() {
		clearInterval(nextTimer);
		upNext = null;
		if (info?.next) goto(`/jellyfin/play/${info.next.id}?start=0`, { replaceState: true });
	}

	$effect(() => {
		const s = info?.session;
		if (menu !== 'info' || !s) { clearInterval(infoTimer); return; }
		const fetchInfo = () => jellyfin.playing(s).then((r) => (serverInfo = r)).catch(() => {});
		void fetchInfo();
		infoTimer = setInterval(fetchInfo, 4000);
		return () => clearInterval(infoTimer);
	});

	function onKey(key: NavKey, e: KeyboardEvent): boolean {
		switch (key) {
			case 'playpause':
				if (e.key === ' ' || e.key.startsWith('Media')) return togglePlay(), true;
				return false;
			case 'ff': return seekBy(30), true;
			case 'rw': return seekBy(-30), true;
			case 'next': if (info?.next) { playNext(); return true; } return false;
			case 'back':
				if (menu) { menu = null; return true; }
				exit();
				return true;
		}
		if (!controls && !menu) {
			if (key === 'select') togglePlay();
			else if (key === 'left') seekBy(-10);
			else if (key === 'right') seekBy(10);
			poke();
			tick().then(() => { const b = document.querySelector<HTMLElement>('#jf-seek'); if (b) nav.focus(b, { scroll: false }); });
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
	function onBarPointer(e: PointerEvent) {
		const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
		seekTo(((e.clientX - r.left) / r.width) * duration);
	}

	let offKeys: (() => void) | null = null;
	const onHide = () => { if (document.visibilityState === 'hidden') report(); };
	const onPageHide = () => { if (info && video) void jellyfin.progress(info.session, video.currentTime, true, 'stopped', true).catch(() => {}); };
	onMount(() => {
		offKeys = nav.handleKeys(onKey);
		document.addEventListener('visibilitychange', onHide);
		window.addEventListener('pagehide', onPageHide);
	});
	$effect(() => {
		const v = id;
		if (v && video) untrack(() => { void teardown().then(() => load(v)); });
	});
	onDestroy(() => {
		loadSeq++;
		offKeys?.();
		clearTimeout(hideTimer);
		clearInterval(nextTimer);
		clearInterval(infoTimer);
		document.removeEventListener('visibilitychange', onHide);
		window.removeEventListener('pagehide', onPageHide);
		void teardown();
	});

	const title = $derived(info?.item.type === 'Episode'
		? `${info.item.series_name} · S${info.item.season ?? '?'}E${info.item.episode ?? '?'} · ${info.item.name}`
		: info?.item.name ?? '');
	const methodLabel = (m?: string | null) => (m === 'DirectPlay' ? 'Direct play' : m === 'DirectStream' ? 'Direct stream (remux)' : m === 'Transcode' ? 'Transcoding' : m ?? '…');
</script>

<svelte:head><title>{info?.item.name ?? 'Playing'} · The Marquee</title></svelte:head>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="player" class:hide={!controls && !menu && !paused} onpointermove={poke}>
	<video
		bind:this={video}
		bind:paused
		bind:currentTime={current}
		playsinline
		onclick={togglePlay}
		onwaiting={() => (buffering = true)}
		onplaying={() => (buffering = false)}
		onpause={report}
		onended={onEnded}
		ondurationchange={() => { if (video && isFinite(video.duration) && video.duration > 0 && !duration) duration = video.duration; }}
	>
		{#each info?.subtitles.filter((s) => s.url && !s.burned) ?? [] as s (s.index)}
			<track id="jf-sub-{s.index}" kind="subtitles" src={s.url} srclang={s.lang ?? undefined} label={s.label} onload={applyText} />
		{/each}
	</video>

	{#if loading || buffering}<div class="spin" aria-label="Loading"></div>{/if}

	{#if failed}
		<div class="fail" use:navScope={{ onBack: exit }}>
			<p>{failed}</p>
			<div class="row">
				<button class="btn primary" use:focusable={{ autofocus: true, onSelect: () => load(id) }}>Try again</button>
				{#if quality === 0}<button class="btn" use:focusable={{ onSelect: () => pickQuality(3) }}>Try a lower quality</button>{/if}
				<button class="btn" use:focusable={{ onSelect: exit }}>Go back</button>
			</div>
		</div>
	{/if}

	{#if upNext != null && info?.next}
		<div class="upnext">
			<span>Next: <strong>{info.next.name}</strong> in {upNext}s</span>
			<button class="btn primary" use:focusable={{ group: 'jf-next', autofocus: true, onSelect: playNext }}>Play now</button>
			<button class="btn" use:focusable={{ group: 'jf-next', onSelect: () => { upNext = null; clearInterval(nextTimer); } }}>Cancel</button>
		</div>
	{/if}

	<div class="top">
		<button class="btn icon round" aria-label="Back" use:focusable={{ group: 'jf-top', scroll: 'none', onSelect: exit }}><Icon name="back" /></button>
		<h1>{title}</h1>
	</div>

	<div class="bottom">
		<div id="jf-seek" class="bar" role="slider" tabindex="0" aria-label="Seek" aria-valuemin={0} aria-valuemax={Math.round(duration)} aria-valuenow={Math.round(current)}
			onpointerdown={onBarPointer} use:focusable={{ group: 'jf-bar', autofocus: true, scroll: 'none', onKey: seekKey }}>
			<i style:width="{duration ? (current / duration) * 100 : 0}%"></i>
		</div>
		<div class="btns">
			<button class="btn icon round big" aria-label={paused ? 'Play' : 'Pause'} use:focusable={{ group: 'jf-btns', scroll: 'none', onSelect: togglePlay }}><Icon name={paused ? 'play' : 'pause'} /></button>
			<button class="btn icon round" aria-label="Back 10 seconds" use:focusable={{ group: 'jf-btns', scroll: 'none', onSelect: () => seekBy(-10) }}><Icon name="rewind" /></button>
			<button class="btn icon round" aria-label="Forward 10 seconds" use:focusable={{ group: 'jf-btns', scroll: 'none', onSelect: () => seekBy(10) }}><Icon name="forward" /></button>
			{#if info?.next}<button class="btn icon round" aria-label="Next episode" use:focusable={{ group: 'jf-btns', scroll: 'none', onSelect: playNext }}><Icon name="next" /></button>{/if}
			<span class="time">{fmtTime(current)} / {fmtTime(duration)}</span>
			<span class="spacer"></span>
			{#if (info?.audio.length ?? 0) > 1}
				<button class="btn" use:focusable={{ group: 'jf-btns', scroll: 'none', onSelect: () => (menu = 'audio') }}>Audio</button>
			{/if}
			{#if info?.subtitles.length}
				<button class="btn icon round" aria-label="Subtitles" use:focusable={{ group: 'jf-btns', scroll: 'none', onSelect: () => (menu = 'subs') }}><Icon name="cc" /></button>
			{/if}
			<button class="btn icon round" aria-label="Quality" use:focusable={{ group: 'jf-btns', scroll: 'none', onSelect: () => (menu = 'quality') }}><Icon name="quality" /></button>
			<button class="btn icon round" aria-label="Playback info" use:focusable={{ group: 'jf-btns', scroll: 'none', onSelect: () => (menu = 'info') }}><Icon name="info" /></button>
			<button class="btn" use:focusable={{ group: 'jf-btns', scroll: 'none', onSelect: playOn }}>Play on…</button>
		</div>
	</div>

	{#if menu}
		<div class="menu" role="dialog" aria-label="Player options" use:navScope={{ onBack: () => (menu = null) }}>
			{#if menu === 'audio' && info}
				<h2>Audio</h2>
				{#each info.audio as a (a.index)}
					<button class="opt" class:on={a.index === info.audio_index} use:focusable={{ group: 'jf-menu', autofocus: a.index === info.audio_index, onSelect: () => reload({ audio: a.index }) }}>{a.label}</button>
				{/each}
			{:else if menu === 'subs' && info}
				<h2>Subtitles</h2>
				<button class="opt" class:on={textIndex == null && !info.subtitles.find((s) => s.index === info?.subtitle_index)?.burned} use:focusable={{ group: 'jf-menu', autofocus: true, onSelect: () => pickSub(-1) }}>Off</button>
				{#each info.subtitles as s (s.index)}
					<button class="opt" class:on={s.index === textIndex || (s.burned && s.index === info.subtitle_index)}
						use:focusable={{ group: 'jf-menu', onSelect: () => pickSub(s.index) }}>{s.label}{s.forced ? ' (forced)' : ''}{s.burned ? ' · burned in' : ''}</button>
				{/each}
			{:else if menu === 'quality'}
				<h2>Quality</h2>
				{#each QUALITIES as q, i}
					<button class="opt" class:on={i === quality} use:focusable={{ group: 'jf-menu', autofocus: i === quality, onSelect: () => pickQuality(i) }}>
						{q.label}{i === 0 ? ' (direct play when possible)' : ' · transcode'}
					</button>
				{/each}
			{:else if menu === 'info' && info}
				<h2>Playback</h2>
				<dl>
					<dt>Method</dt><dd>{methodLabel(serverInfo?.method ?? info.method)}</dd>
					{#if serverInfo?.transcode}
						{@const t = serverInfo.transcode}
						<dt>Hardware</dt><dd>{t.video_direct ? 'Not needed (video copied)' : t.hardware ? t.hardware.toUpperCase() : 'None (software encoding)'}</dd>
						<dt>Video</dt><dd>{t.video_direct ? 'copy' : t.video_codec}{t.width && t.height ? ` · ${t.width}×${t.height}` : ''}</dd>
						<dt>Audio</dt><dd>{t.audio_direct ? 'copy' : t.audio_codec}</dd>
						{#if t.framerate}<dt>Encoder</dt><dd>{Math.round(t.framerate)} fps{t.progress != null ? ` · ${Math.round(t.progress)}% ready` : ''}</dd>{/if}
						{#if t.bitrate}<dt>Bitrate</dt><dd>{(t.bitrate / 1e6).toFixed(1)} Mbps</dd>{/if}
						{#if t.reasons.length}<dt>Why</dt><dd>{t.reasons.join(', ')}</dd>{/if}
					{:else if info.transcode_reasons.length}
						<dt>Why</dt><dd>{info.transcode_reasons.join(', ')}</dd>
					{/if}
				</dl>
			{/if}
			<button class="btn" use:focusable={{ group: 'jf-menu-close', onSelect: () => (menu = null) }}>Close</button>
		</div>
	{/if}
</div>

<style>
	.player { position: fixed; inset: 0; z-index: 50; background: #000; color: #fff; }
	video { width: 100%; height: 100%; object-fit: contain; }
	.player.hide { cursor: none; }
	.player.hide .top, .player.hide .bottom { opacity: 0; pointer-events: none; }
	.top, .bottom { position: absolute; left: 0; right: 0; padding: 1rem var(--gutter, 1.5rem); transition: opacity var(--dur-calm) var(--ease-calm); }
	.top { top: 0; display: flex; gap: 1rem; align-items: center; background: linear-gradient(#000c, transparent); }
	.top h1 { margin: 0; font-size: var(--fs-lg); font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
	.bottom { bottom: 0; display: grid; gap: 0.8rem; background: linear-gradient(transparent, #000d); }
	.bar { height: 0.45rem; background: #fff3; border-radius: 1rem; cursor: pointer; }
	.bar:focus-visible { outline: 3px solid var(--accent); outline-offset: 4px; }
	.bar i { display: block; height: 100%; background: var(--accent); border-radius: 1rem; }
	.btns { display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap; }
	.time { font-variant-numeric: tabular-nums; }
	.spacer { flex: 1; }
	.spin {
		position: absolute; top: 50%; left: 50%; width: 3rem; height: 3rem; margin: -1.5rem; border-radius: 50%;
		border: 4px solid #fff3; border-top-color: var(--accent); animation: spin 0.9s linear infinite; pointer-events: none;
	}
	@keyframes spin { to { transform: rotate(360deg); } }
	.fail, .upnext {
		position: absolute; left: 50%; transform: translateX(-50%); background: #000d; border-radius: var(--r-md);
		padding: 1.2rem 1.5rem; display: grid; gap: 0.8rem; max-width: min(36rem, 90vw);
	}
	.fail { top: 40%; }
	.upnext { bottom: 8rem; display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; }
	.row { display: flex; gap: 0.6rem; flex-wrap: wrap; }
	.menu {
		position: absolute; right: var(--gutter, 1.5rem); bottom: 7rem; width: min(24rem, 90vw); max-height: 70vh; overflow: auto;
		background: #111e; border-radius: var(--r-md); padding: 1rem; display: grid; gap: 0.4rem;
	}
	.menu h2 { margin: 0 0 0.4rem; font-size: var(--fs-md); }
	.opt { text-align: left; padding: 0.6rem 0.8rem; border-radius: 0.6rem; background: transparent; color: inherit; border: 1px solid transparent; cursor: pointer; }
	.opt.on { border-color: var(--accent); }
	.opt.on::before { content: '✓ '; }
	.opt:focus-visible, .opt:hover { background: #fff2; }
	dl { display: grid; grid-template-columns: auto 1fr; gap: 0.3rem 1rem; margin: 0 0 0.6rem; }
	dt { color: #fffa; }
	dd { margin: 0; }
</style>
