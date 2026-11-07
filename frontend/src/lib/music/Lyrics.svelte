<script module lang="ts">
	import { music as api, type Lyrics as L } from './api';
	// Shared across mounts so the up-next song's lyrics are ready before it starts.
	const cache = new Map<string, L>();
	export async function prefetchLyrics(videoId: string) {
		if (cache.has(videoId)) return;
		try {
			cache.set(videoId, await api.lyrics(videoId));
		} catch {
			/* fetched again when it plays */
		}
	}
</script>

<script lang="ts">
	// Karaoke lyrics over the video: the sung line fills up as it is sung, the next lines wait below.
	// Timed lyrics come from LRCLIB; songs without them can be transcribed ("listen") by the app's
	// speech-to-text, which also auto-syncs timed lyrics that start early or late in a music video.
	import { onDestroy } from 'svelte';
	import { focusable } from '$lib/nav';
	import { toast } from '$lib/stores/toast.svelte';
	import { music, type LyricLine, type Lyrics, type Track } from './api';

	interface Props {
		track: Track;
		el: HTMLVideoElement | undefined;
		big?: boolean;
	}
	let { track, el, big = false }: Props = $props();

	let data = $state<Lyrics | null>(null);
	let loading = $state(false);
	let offset = $state(0);
	let rate = $state(1); // video time = rate × lyric time + offset (auto-sync finds edits that run slower or faster)
	let now = $state(0); // playback position in ms, read every animation frame
	let listening = $state(false);
	let listenNote = $state('');
	let raf = 0;
	let saveTimer: number | undefined;
	let pollTimer: number | undefined;
	let seq = 0;

	async function load(videoId: string) {
		const my = ++seq;
		window.clearTimeout(pollTimer);
		listening = false;
		listenNote = '';
		const hit = cache.get(videoId);
		data = hit ?? null;
		offset = hit?.offset_ms ?? 0;
		rate = hit?.rate || 1;
		if (hit && hit.status !== 'error') {
			loading = false;
			const st = hit.transcript?.status;
			if (st === 'queued' || st === 'running') {
				listening = true; // still transcribing from earlier: keep following it
				poll(videoId, my);
			}
			return;
		}
		loading = true;
		try {
			const r = await music.lyrics(videoId);
			if (my !== seq) return;
			cache.set(videoId, r);
			data = r;
			offset = r.offset_ms;
			rate = r.rate || 1;
			const st = r.transcript?.status;
			if (st === 'queued' || st === 'running') poll(videoId, my);
		} catch {
			if (my === seq) data = null;
		} finally {
			if (my === seq) loading = false;
		}
	}
	$effect(() => {
		void load(track.video_id);
	});

	// Timed lines to show: lyrics when timed, else the transcript the app wrote while listening.
	const timed = $derived.by((): LyricLine[] => {
		if (!data) return [];
		if (data.status === 'found' && data.synced) return data.lines;
		return data.transcript?.lines ?? [];
	});
	const fromTranscript = $derived(!!data && !(data.status === 'found' && data.synced) && timed.length > 0);
	const at = $derived(fromTranscript ? now : (now - offset) / rate);
	const idx = $derived.by(() => {
		let lo = 0, hi = timed.length - 1, found = -1;
		while (lo <= hi) {
			const mid = (lo + hi) >> 1;
			if (timed[mid].t <= at) { found = mid; lo = mid + 1; } else hi = mid - 1;
		}
		return found;
	});
	function lineEnd(i: number): number {
		const ln = timed[i];
		if (ln?.end) return ln.end;
		const next = timed[i + 1]?.t ?? ln.t + 5000;
		return Math.min(next, ln.t + Math.max(1500, (ln.text.length || 10) * 180)); // a long gap is not sung
	}
	const progress = $derived.by(() => {
		if (idx < 0) return 0;
		const ln = timed[idx];
		return Math.max(0, Math.min(1, (at - ln.t) / Math.max(1, lineEnd(idx) - ln.t)));
	});
	// Counted in video seconds: lyric time runs `rate` times slower or faster than the video.
	const waitingFor = $derived(idx < 0 && timed.length ? Math.ceil(((timed[0].t - at) * (fromTranscript ? 1 : rate)) / 1000) : 0);

	function frame() {
		if (el) now = el.currentTime * 1000;
		raf = requestAnimationFrame(frame);
	}
	$effect(() => {
		raf = requestAnimationFrame(frame);
		return () => cancelAnimationFrame(raf);
	});

	function nudge(ms: number) {
		if (!data || data.status !== 'found') return;
		offset = Math.max(-120000, Math.min(120000, offset + ms));
		const vid = data.video_id;
		const val = offset;
		window.clearTimeout(saveTimer);
		saveTimer = window.setTimeout(() => {
			music.lyricsOffset(vid, val).then((r) => cache.set(vid, r)).catch(() => {});
		}, 800);
	}

	function poll(videoId: string, my: number) {
		window.clearTimeout(pollTimer);
		pollTimer = window.setTimeout(() => void listen(videoId, my, true), 4000);
	}
	async function listen(videoId = track.video_id, my = seq, quiet = false) {
		listening = true;
		try {
			const r = await music.listen(videoId);
			if (my !== seq) return;
			const tr = r.transcript;
			const base = r.lyrics ?? data;
			if (base) {
				data = { ...$state.snapshot(base), transcript: tr } as Lyrics;
				cache.set(videoId, $state.snapshot(data) as Lyrics);
			}
			if (r.aligned && r.lyrics) {
				offset = r.lyrics.offset_ms;
				rate = r.lyrics.rate || 1;
				const speed = Math.abs(rate - 1) >= 0.005 ? `, video ${rate > 1 ? 'slower' : 'faster'} ×${rate.toFixed(3)}` : '';
				toast.success(`🎯 Lyrics synced on ${r.matched_lines ?? 'several'} lines (${offset >= 0 ? '+' : ''}${(offset / 1000).toFixed(1)} s${speed})`);
			} else if (tr.status === 'queued' || tr.status === 'running') {
				listenNote = tr.status === 'queued' ? 'Waiting for the transcriber…' : `Listening… ${Math.round(tr.progress)}%`;
				poll(videoId, my);
				return;
			} else if (tr.lines.length && data?.status === 'found' && data.synced && !quiet) {
				toast.info('Could not match the lyrics to what is sung — nudge them by hand.');
			} else if (!tr.lines.length && tr.status === 'failed') {
				toast.warn('Transcribing this song failed.');
			}
			listenNote = '';
		} catch {
			listenNote = '';
		} finally {
			if (my === seq && !listenNote) listening = false;
		}
	}

	onDestroy(() => {
		seq++;
		cancelAnimationFrame(raf);
		window.clearTimeout(pollTimer);
		window.clearTimeout(saveTimer);
	});
</script>

<div class="lyrics" class:big aria-live="off">
	{#if loading && !data}
		<p class="note">Finding the lyrics…</p>
	{:else if timed.length}
		<div class="lines">
			{#if idx > 0}<p class="line past">{timed[idx - 1].text}</p>{/if}
			{#if idx < 0}
				<p class="line wait">♪ {waitingFor > 0 ? `${waitingFor}…` : ''}</p>
			{:else}
				<p class="line now" style:--p={progress}>
					{#if timed[idx].words?.length}
						{#each timed[idx].words! as w, wi (wi)}<span class="w" class:sung={w.t <= at}>{w.text} </span>{/each}
					{:else}
						<span class="fill" data-text={timed[idx].text}>{timed[idx].text}</span>
					{/if}
				</p>
			{/if}
			{#each timed.slice(idx + 1, idx + 3) as ln, k (idx + 1 + k)}
				<p class="line next" class:far={k === 1}>{ln.text}</p>
			{/each}
		</div>
		<div class="tools">
			{#if fromTranscript}
				<span class="src" title="No timed lyrics were found, so these are what the app heard">👂 heard by the app</span>
			{:else}
				<button class="t" use:focusable aria-label="Lyrics earlier" onclick={() => nudge(-500)}>⏪</button>
				<span class="off" title="Lyrics timing shift">{offset >= 0 ? '+' : ''}{(offset / 1000).toFixed(1)}s{Math.abs(rate - 1) >= 0.005 ? ` ×${rate.toFixed(2)}` : ''}</span>
				<button class="t" use:focusable aria-label="Lyrics later" onclick={() => nudge(500)}>⏩</button>
				<button class="t" use:focusable disabled={listening} title="Listen to the song and line the lyrics up automatically" onclick={() => listen()}>{listening ? listenNote || '🎯…' : '🎯 Auto-sync'}</button>
			{/if}
		</div>
	{:else if data?.status === 'found' && data.plain}
		<div class="plain">{data.plain}</div>
		<div class="tools">
			<button class="t" use:focusable disabled={listening} onclick={() => listen()}>{listening ? listenNote || 'Listening…' : '👂 Time them by listening'}</button>
		</div>
	{:else if data}
		<p class="note">No lyrics found for this one.</p>
		<div class="tools">
			{#if data.transcript?.enabled !== false}
				<button class="t" use:focusable disabled={listening} onclick={() => listen()}>{listening ? listenNote || 'Listening…' : '👂 Listen and write them'}</button>
			{/if}
		</div>
	{/if}
</div>

<style>
	.lyrics { position: absolute; inset: auto 0 0 0; padding: 0.6rem 1rem 0.8rem; display: grid; gap: 0.4rem; justify-items: center; text-align: center; pointer-events: none; background: linear-gradient(transparent, rgba(0, 0, 0, 0.78) 35%); color: #fff; }
	.lyrics.big { padding: 2rem 2rem 1.4rem; }
	.lines { display: grid; gap: 0.15rem; justify-items: center; }
	.line { margin: 0; line-height: 1.2; text-shadow: 0 2px 10px rgba(0, 0, 0, 0.8); font-weight: 700; }
	.past { font-size: 0.8rem; opacity: 0.45; }
	.now { font-size: 1.05rem; }
	.next { font-size: 0.85rem; opacity: 0.7; }
	.next.far { opacity: 0.4; }
	.big .past { font-size: clamp(0.9rem, 1.5vw, 1.4rem); }
	.big .now { font-size: clamp(1.4rem, 3.2vw, 3rem); }
	.big .next { font-size: clamp(1rem, 1.8vw, 1.7rem); }
	.wait { font-size: 1.1rem; opacity: 0.8; }
	/* The sung part fills in pink from the left. */
	.fill { background: linear-gradient(90deg, #ff4fa3 0, #ffb547 calc(var(--p) * 100%), #fff calc(var(--p) * 100%)); -webkit-background-clip: text; background-clip: text; color: transparent; text-shadow: none; filter: drop-shadow(0 2px 6px rgba(0, 0, 0, 0.9)); }
	.w { transition: color 0.15s; }
	.w.sung { color: #ff4fa3; }
	.tools { display: flex; gap: 0.3rem; align-items: center; pointer-events: auto; flex-wrap: wrap; justify-content: center; }
	.t { background: rgba(255, 255, 255, 0.14); border: 1px solid rgba(255, 255, 255, 0.25); color: #fff; border-radius: 999px; padding: 0.15rem 0.6rem; font: inherit; font-size: 0.78rem; cursor: pointer; }
	.t:disabled { opacity: 0.6; cursor: default; }
	.t:focus-visible { outline: 2px solid #ffb547; }
	.off, .src { font-size: 0.75rem; opacity: 0.85; font-variant-numeric: tabular-nums; }
	.plain { max-height: 40%; overflow: auto; white-space: pre-line; font-size: 0.9rem; pointer-events: auto; text-shadow: 0 2px 8px #000; }
	.big .plain { font-size: 1.2rem; max-height: 55vh; }
	.note { margin: 0; font-size: 0.85rem; opacity: 0.85; }
</style>
