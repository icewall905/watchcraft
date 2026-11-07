<script lang="ts">
	// The music deck: one <video> that docks at the bottom of /music or fills the screen in party mode.
	import { onDestroy, onMount } from 'svelte';
	import { focusable, nav, isTextInput } from '$lib/nav';
	import { api } from '$lib/api';
	import { session } from '$lib/stores/session.svelte';
	import { toast } from '$lib/stores/toast.svelte';
	import { warmVideo } from '$lib/prefetch';
	import { player } from './player.svelte';
	import { fmtDuration, type Track } from './api';

	interface Props {
		onArtist: (name: string) => void;
		onMore: (t: Track) => void;
	}
	let { onArtist, onMore }: Props = $props();

	const VIBES = ['Party', 'Dance', 'Throwback', 'Chill', 'Hype', 'Singalong'];

	let el = $state<HTMLVideoElement>();
	let shaka: any = null;
	let shakaEl: HTMLVideoElement | null = null;
	let loadSeq = 0;
	let loadedToken = -1;
	let paused = $state(true);
	let loading = $state(false);
	let time = $state(0);
	let length = $state(0);
	let failures = 0;
	let ask = $state('');
	let feedEl = $state<HTMLElement>();
	let announceTimer: number | undefined;
	let showAnnounce = $state(false);

	const cur = $derived(player.current);

	async function ensureShaka(seq: number): Promise<boolean> {
		if (!el) return false;
		if (shaka && shakaEl !== el) {
			// The deck closed and reopened: the old <video> is gone, so is its player.
			const old = shaka;
			shaka = null;
			await old.destroy().catch(() => {});
		}
		if (shaka) return seq === loadSeq;
		if (el.getAttribute('src')) {
			el.removeAttribute('src'); // leave progressive mode
			el.load();
		}
		const { loadShaka } = await import('$lib/player/shaka');
		const lib = await loadShaka();
		if (seq !== loadSeq || !el) return false;
		if (!lib.Player.isBrowserSupported()) throw new Error('no MSE');
		const p = new lib.Player();
		// Manifests are profile-scoped (player prefs, PIN): send the profile like the watch page does.
		const profileId = session.profileId;
		p.getNetworkingEngine()?.registerRequestFilter((_type: number, req: { uris: string[]; headers: Record<string, string> }) => {
			if (profileId === null || !req.uris.length) return;
			const ok = req.uris.every((u) => {
				const url = new URL(u, location.href);
				return url.origin === location.origin && /^\/api\/video\/[^/]+\/manifest\.mpd$/.test(url.pathname);
			});
			if (ok) req.headers['X-Profile-Id'] = String(profileId);
		});
		await p.attach(el);
		p.configure({
			streaming: { bufferingGoal: 40, rebufferingGoal: 2, bufferBehind: 20, retryParameters: { maxAttempts: 3, timeout: 30000 } },
			manifest: { retryParameters: { maxAttempts: 2, timeout: 60000 } },
			abr: { enabled: true, restrictions: { maxHeight: Math.min(session.playerPrefs.max_height || 1080, 1080) } }
		});
		shaka = p;
		shakaEl = el;
		return seq === loadSeq;
	}

	/** A plain mp4: the stored copy, or the backend's progressive proxy when DASH is unusable. */
	function progressive(t: Track, seq: number, src = api.fallbackUrl(t.video_id)): Promise<boolean> {
		return new Promise((resolve) => {
			if (!el) return resolve(false);
			const v = el;
			const timer = window.setTimeout(() => done(false), 20000); // a stalled request must not hang the deck
			const done = (ok: boolean) => {
				window.clearTimeout(timer);
				v.removeEventListener('loadeddata', onOk);
				v.removeEventListener('error', onErr);
				resolve(ok && seq === loadSeq);
			};
			const ours = () => v.currentSrc.includes(src) || v.getAttribute('src') === src;
			const onOk = () => ours() && done(true);
			// A late error from the torn-down DASH source must not fail the mp4 before it loads.
			const onErr = () => ours() && done(false);
			v.addEventListener('loadeddata', onOk); // ready even if autoplay is refused
			v.addEventListener('error', onErr);
			v.src = src;
			v.play().catch(() => {});
		});
	}

	async function load(t: Track) {
		if (!el) return;
		const seq = ++loadSeq;
		loading = true;
		time = 0;
		length = t.duration_s || 0;
		let ok = false;
		if (t.offline) {
			// Stored on the server: play from disk, no YouTube round trip. Shaka steps aside for it.
			if (shaka) {
				const old = shaka;
				shaka = null;
				shakaEl = null;
				await old.destroy().catch(() => {});
				if (seq !== loadSeq) return;
			}
			ok = await progressive(t, seq, api.fileUrl(t.video_id));
			if (seq !== loadSeq) return;
			if (ok) {
				loading = false;
				failures = 0;
				player.started();
				return;
			}
		}
		try {
			if (await ensureShaka(seq)) {
				await shaka.load(api.manifestUrl(t.video_id, session.playerPrefs.max_height));
				if (seq !== loadSeq) return;
				await el.play().catch(() => {});
				ok = true;
			}
		} catch (e) {
			if (seq !== loadSeq) return;
			console.warn('music: DASH failed, trying progressive', t.video_id, e);
			// Drop the player: the element plays a plain mp4 now, and the next track attaches a fresh one.
			const old = shaka;
			shaka = null;
			shakaEl = null;
			await old?.destroy?.().catch?.(() => {});
			if (seq !== loadSeq) return;
			ok = await progressive(t, seq);
		}
		if (seq !== loadSeq) return;
		loading = false;
		if (ok) {
			failures = 0;
			player.started();
			return;
		}
		failures++;
		if (failures >= 3) {
			toast.error('Several videos in a row would not play. Check the YouTube connection.');
			failures = 0;
			player.stop();
			return;
		}
		toast.warn(`Skipping "${t.title}" — it would not play here.`);
		player.next(false);
	}

	// Keyed on the play token, not the video id: replaying the same song must reload it too.
	$effect(() => {
		const t = cur;
		const token = player.playToken;
		if (t && el && token !== loadedToken) {
			loadedToken = token;
			void load(t);
		}
		if (!t && loadedToken !== -1) {
			loadSeq++;
			loadedToken = -1;
			el?.pause();
			void shaka?.unload?.().catch?.(() => {});
		}
	});

	// Prepare the next song while this one plays, so the hand-over is instant.
	$effect(() => {
		const next = player.upNext[0];
		if (next && cur) {
			const id = window.setTimeout(() => warmVideo(next.video_id, { manifest: true, force: true }), 4000);
			return () => window.clearTimeout(id);
		}
	});

	$effect(() => {
		const line = player.announce;
		if (!line) return;
		showAnnounce = true;
		window.clearTimeout(announceTimer);
		announceTimer = window.setTimeout(() => (showAnnounce = false), 7000);
	});

	$effect(() => {
		void player.messages.length;
		queueMicrotask(() => feedEl?.scrollTo({ top: feedEl.scrollHeight, behavior: 'smooth' }));
	});

	// The app shell has transformed ancestors that trap position:fixed. The deck lives on <body>,
	// moved there before its video ever plays (moving a playing <video> would pause it).
	function toBody(node: HTMLElement) {
		document.body.appendChild(node);
		return { destroy: () => node.remove() };
	}

	function toggle() {
		if (!el) return;
		if (el.paused) void el.play().catch(() => {});
		else el.pause();
	}
	function seek(e: MouseEvent) {
		const bar = e.currentTarget as HTMLElement;
		const r = bar.getBoundingClientRect();
		if (el && length) el.currentTime = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * length;
	}
	function sendRequest(e: Event) {
		e.preventDefault();
		const text = ask;
		ask = '';
		void player.request(text);
	}
	function onKey(e: KeyboardEvent) {
		if (!player.active || isTextInput(document.activeElement)) return;
		if (e.key === 'MediaTrackNext' || (player.party && e.key === 'ArrowRight' && e.shiftKey)) {
			e.preventDefault();
			player.next();
		} else if (e.key === 'MediaTrackPrevious') {
			e.preventDefault();
			player.prev();
		} else if (player.party && e.key === 'Escape') {
			e.preventDefault();
			player.party = false;
		}
	}
	let offRaw: (() => void) | undefined;
	onMount(() => {
		offRaw = nav.onRawKey(onKey);
	});
	onDestroy(() => {
		offRaw?.();
		loadSeq++;
		window.clearTimeout(announceTimer);
		void shaka?.destroy?.().catch?.(() => {});
		player.stop();
		try { window.speechSynthesis?.cancel(); } catch { /* none */ }
	});
</script>

{#if cur}
	<section class="deck" class:party={player.party} aria-label="Music player" use:toBody>
		<div class="screen">
			<!-- svelte-ignore a11y_media_has_caption -->
			<video
				bind:this={el}
				playsinline
				onplay={() => (paused = false)}
				onpause={() => (paused = true)}
				ontimeupdate={() => (time = el?.currentTime ?? 0)}
				ondurationchange={() => { if (el && Number.isFinite(el.duration)) length = el.duration; }}
				onended={() => player.next(false)}
				onclick={toggle}
			></video>
			{#if loading}<div class="spinner" aria-label="Loading"><span class="vinyl"></span></div>{/if}
			{#if showAnnounce && player.mode === 'dj'}
				<div class="announce" role="status"><span class="mic">🎙️</span>{player.announce}</div>
			{/if}
			{#if player.party}
				<div class="now">
					<span class="src">{player.source || 'Now playing'}</span>
					<h2>{cur.title}</h2>
					<button class="artist-link" onclick={() => { player.party = false; onArtist(cur.artist); }}>{cur.artist}</button>
				</div>
			{/if}
		</div>

		<div class="bar">
			<div class="meta">
				<img src={cur.thumbnail} alt="" />
				<div class="txt">
					<strong title={cur.title}>{cur.title}</strong>
					<button class="artist-link" use:focusable onclick={() => onArtist(cur.artist)}>{cur.artist}{cur.offline ? ' · 💾 saved' : ''}</button>
				</div>
				<button class="heart" class:on={player.isFav(cur)} use:focusable aria-label={player.isFav(cur) ? 'Remove from favourites' : 'Add to favourites'} onclick={() => player.toggleFav(cur)}>{player.isFav(cur) ? '❤️' : '🤍'}</button>
				<button class="mini" use:focusable aria-label="More actions" onclick={() => onMore(cur)}>⋯</button>
			</div>
			<div class="controls">
				<button class="ctl" use:focusable aria-label="Previous" disabled={player.index <= 0} onclick={() => player.prev()}>⏮</button>
				<button class="ctl play" use:focusable aria-label={paused ? 'Play' : 'Pause'} onclick={toggle}>{paused ? '▶' : '⏸'}</button>
				<button class="ctl" use:focusable aria-label="Skip" onclick={() => player.next()}>⏭</button>
				<span class="mode">{player.mode === 'dj' ? '🎧 DJ' : player.mode === 'shuffle' ? '🔀 Shuffle' : '▶ Playlist'}</span>
			</div>
			<div class="right">
				<button class="btn ghost" use:focusable onclick={() => (player.party = !player.party)}>{player.party ? '↙ Shrink' : '🎉 Party mode'}</button>
				<button class="mini" use:focusable aria-label="Stop and close player" onclick={() => player.stop()}>✕</button>
			</div>
			<!-- svelte-ignore a11y_click_events_have_key_events -->
			<div class="progress" role="presentation" onclick={seek}>
				<div class="fill" style:width={length ? `${Math.min(100, (time / length) * 100)}%` : '0%'}></div>
				<span class="t">{fmtDuration(Math.floor(time))} / {fmtDuration(Math.floor(length))}</span>
			</div>
		</div>

		{#if player.party}
			<aside class="booth">
				<header>
					<h3>{player.mode === 'dj' ? '🎧 DJ booth' : 'Up next'}</h3>
					{#if player.mode === 'dj'}
						<label class="voice"><input type="checkbox" bind:checked={player.djVoice} /> DJ voice</label>
					{:else}
						<button class="btn small" use:focusable onclick={() => player.djTakeOver()}>🎧 Let the DJ take over</button>
					{/if}
				</header>
				{#if player.mode === 'dj'}
					<div class="vibes">
						{#each VIBES as v}
							<button class="chip" class:on={player.vibe === v} use:focusable onclick={() => player.setVibe(player.vibe === v ? '' : v)}>{v}</button>
						{/each}
					</div>
					<div class="feed" bind:this={feedEl} aria-live="polite">
						{#each player.messages as m (m.id)}
							<p class="msg {m.kind}">{m.text}</p>
						{/each}
						{#if player.djBusy}<p class="msg info digging">Digging through the crates<span>.</span><span>.</span><span>.</span></p>{/if}
					</div>
				{/if}
				<form class="ask" onsubmit={sendRequest}>
					<input class="input" bind:value={ask} placeholder="Request a song or artist…" maxlength="200" use:focusable />
					<button class="btn primary" use:focusable disabled={!ask.trim() || player.djBusy}>Request</button>
				</form>
				<ol class="upnext">
					{#each player.upNext.slice(0, 12) as t, i (t.video_id)}
						<li>
							<button class="row" use:focusable onclick={() => player.jumpTo(i)}>
								<img src={t.thumbnail} alt="" />
								<span><strong>{t.title}</strong><small>{t.artist}{t.reason ? ` · ${t.reason}` : ''}</small></span>
							</button>
							<button class="mini" aria-label="Remove from queue" use:focusable onclick={() => player.removeUpcoming(i)}>✕</button>
						</li>
					{:else}
						<li class="muted small">{player.mode === 'dj' ? 'The DJ is lining up the next tracks…' : 'Nothing queued after this one.'}</li>
					{/each}
				</ol>
			</aside>
		{/if}
	</section>
{/if}

<style>
	.deck {
		position: fixed;
		right: clamp(0.5rem, 1.5vw, 1.2rem);
		bottom: clamp(0.5rem, 1.5vw, 1.2rem);
		width: min(64rem, calc(100vw - 1rem));
		z-index: 80;
		color: var(--text);
		font-family: var(--font);
		display: grid;
		grid-template-columns: 11rem minmax(0, 1fr);
		align-items: center;
		gap: 0.8rem;
		padding: 0.6rem;
		border-radius: var(--r-lg);
		background: linear-gradient(110deg, color-mix(in srgb, #ff4fa3 22%, var(--surface-solid)), color-mix(in srgb, #3fb6ff 18%, var(--surface-solid)));
		border: 1px solid var(--line-strong);
		box-shadow: 0 -10px 40px -20px rgba(0, 0, 0, 0.6);
	}
	.screen { position: relative; aspect-ratio: 16 / 9; border-radius: var(--r-md); overflow: hidden; background: #000; }
	video { width: 100%; height: 100%; display: block; object-fit: contain; background: #000; cursor: pointer; }
	.bar { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; grid-template-areas: 'meta controls right' 'progress progress progress'; gap: 0.5rem 1rem; align-items: center; }
	.meta { grid-area: meta; display: flex; align-items: center; gap: 0.6rem; min-width: 0; }
	.meta img { width: 3rem; height: 3rem; object-fit: cover; border-radius: var(--r-sm); display: none; }
	.txt { display: grid; min-width: 0; }
	.txt strong { font-size: 1.05rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
	.artist-link { background: none; border: 0; padding: 0; color: var(--muted); font: inherit; text-align: start; cursor: pointer; }
	.artist-link:hover, .artist-link:focus-visible { color: var(--accent); text-decoration: underline; }
	.controls { grid-area: controls; display: flex; align-items: center; gap: 0.4rem; }
	.right { grid-area: right; display: flex; align-items: center; gap: 0.4rem; }
	.ctl { width: 2.9rem; height: 2.9rem; border-radius: 50%; border: 1px solid var(--line-strong); background: var(--surface); color: var(--text); font-size: 1.1rem; cursor: pointer; }
	.ctl.play { width: 3.6rem; height: 3.6rem; font-size: 1.4rem; background: var(--accent); color: var(--accent-ink); border: 0; box-shadow: 0 0 24px var(--accent-glow); }
	.ctl:disabled { opacity: 0.35; cursor: default; }
	.ctl:focus-visible, .mini:focus-visible, .heart:focus-visible { outline: 3px solid var(--accent); outline-offset: 2px; }
	.mode { font-size: 0.8rem; color: var(--muted); margin-left: 0.3rem; white-space: nowrap; }
	.heart, .mini { background: none; border: 0; font-size: 1.35rem; cursor: pointer; color: var(--text); padding: 0.3rem; border-radius: var(--r-sm); }
	.heart.on { animation: pop 0.35s ease; }
	.progress { grid-area: progress; position: relative; height: 0.5rem; border-radius: var(--r-pill); background: var(--surface-2); cursor: pointer; }
	.fill { height: 100%; border-radius: inherit; background: linear-gradient(90deg, #ff4fa3, var(--accent)); transition: width 0.25s linear; }
	.t { position: absolute; right: 0; top: 0.7rem; font-size: 0.72rem; color: var(--muted); font-variant-numeric: tabular-nums; }
	.spinner { position: absolute; inset: 0; display: grid; place-items: center; background: rgba(0, 0, 0, 0.35); }
	.vinyl { width: 3rem; height: 3rem; border-radius: 50%; background: radial-gradient(circle, var(--accent) 0 18%, #111 19% 60%, #333 61% 64%, #111 65%); animation: spin 1.2s linear infinite; }
	.announce { position: absolute; left: 50%; bottom: 1.2rem; transform: translateX(-50%); max-width: 90%; padding: 0.7rem 1.2rem; border-radius: var(--r-pill); background: rgba(10, 8, 23, 0.82); color: #fff; font-weight: 700; font-size: clamp(0.9rem, 1.4vw, 1.5rem); display: flex; gap: 0.6rem; align-items: center; animation: rise 0.5s ease; backdrop-filter: blur(8px); }
	.mic { font-size: 1.3em; }

	/* ---------- party mode: full screen, big type, the booth on the side */
	.deck.party {
		position: fixed;
		inset: 0;
		width: auto;
		z-index: 300; /* above the sidebar (110), below dialogs (500) and toasts */
		border-radius: 0;
		padding: clamp(0.8rem, 2vw, 2rem);
		grid-template-columns: minmax(0, 1fr) minmax(18rem, 24rem);
		grid-template-rows: minmax(0, 1fr) auto;
		grid-template-areas: 'screen booth' 'bar booth';
		align-items: stretch;
		background: radial-gradient(circle at 20% 10%, #ff4fa355, transparent 45%), radial-gradient(circle at 80% 90%, #3fb6ff44, transparent 45%), radial-gradient(circle at 60% 30%, #ffb02033, transparent 40%), #07051a;
		animation: glow 12s ease-in-out infinite alternate;
	}
	.party .screen { grid-area: screen; aspect-ratio: auto; min-height: 0; }
	.party .bar { grid-area: bar; }
	.party .meta img { display: block; }
	.party .now { position: absolute; left: 0; right: 0; top: 0; padding: 1.2rem 1.5rem 3rem; background: linear-gradient(rgba(0, 0, 0, 0.7), transparent); color: #fff; pointer-events: none; }
	.party .now button { pointer-events: auto; color: #ffd6ec; font-size: clamp(1rem, 1.6vw, 1.5rem); }
	.now h2 { margin: 0.2rem 0; font-size: clamp(1.5rem, 3.2vw, 3.2rem); line-height: 1.05; text-shadow: 0 2px 20px rgba(0, 0, 0, 0.6); }
	.src { text-transform: uppercase; letter-spacing: 0.12em; font-size: 0.8rem; opacity: 0.8; }
	.booth { grid-area: booth; display: grid; grid-template-rows: auto auto minmax(6rem, 1fr) auto minmax(0, 1.2fr); gap: 0.7rem; min-height: 0; padding: 1rem; border-radius: var(--r-lg); background: rgba(10, 8, 30, 0.6); border: 1px solid var(--line-strong); backdrop-filter: blur(10px); }
	.booth header { display: flex; justify-content: space-between; align-items: center; gap: 0.5rem; }
	.booth h3 { margin: 0; font-size: 1.2rem; }
	.voice { font-size: 0.85rem; display: flex; gap: 0.3rem; align-items: center; color: var(--muted); }
	.vibes { display: flex; gap: 0.35rem; flex-wrap: wrap; }
	.feed { overflow: auto; display: grid; gap: 0.4rem; align-content: start; min-height: 0; }
	.msg { margin: 0; padding: 0.5rem 0.8rem; border-radius: 1rem; max-width: 92%; font-size: 0.92rem; animation: rise 0.3s ease; }
	.msg.dj { background: linear-gradient(120deg, #ff4fa3, #ff8a3d); color: #fff; font-weight: 600; border-bottom-left-radius: 0.2rem; }
	.msg.request { justify-self: end; background: var(--accent); color: var(--accent-ink); border-bottom-right-radius: 0.2rem; }
	.msg.info { background: var(--surface-2); color: var(--muted); font-size: 0.82rem; }
	.digging span { animation: blink 1.2s infinite; }
	.digging span:nth-child(2) { animation-delay: 0.2s; }
	.digging span:nth-child(3) { animation-delay: 0.4s; }
	.ask { display: flex; gap: 0.4rem; }
	.ask .input { min-height: 2.75rem; flex: 1; }
	.upnext { list-style: none; margin: 0; padding: 0; overflow: auto; display: grid; gap: 0.3rem; align-content: start; min-height: 0; }
	.upnext li { display: flex; align-items: center; gap: 0.2rem; }
	.row { flex: 1; display: flex; gap: 0.6rem; align-items: center; text-align: start; background: none; border: 1px solid transparent; color: inherit; padding: 0.3rem; border-radius: var(--r-sm); cursor: pointer; min-width: 0; }
	.row:hover, .row:focus-visible { background: var(--surface-2); border-color: var(--line-strong); }
	.row img { width: 4rem; aspect-ratio: 16 / 9; object-fit: cover; border-radius: 0.35rem; }
	.row span { display: grid; min-width: 0; }
	.row strong, .row small { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
	.row small { color: var(--muted); }

	@media (max-width: 900px) {
		.deck { grid-template-columns: 6.5rem minmax(0, 1fr); }
		.bar { grid-template-columns: minmax(0, 1fr) auto; grid-template-areas: 'meta meta' 'controls right' 'progress progress'; }
		.deck.party { grid-template-columns: 1fr; grid-template-rows: auto auto minmax(0, 1fr); grid-template-areas: 'screen' 'bar' 'booth'; overflow: auto; }
		.party .screen { aspect-ratio: 16 / 9; }
		.mode { display: none; }
	}
	@keyframes spin { to { transform: rotate(360deg); } }
	@keyframes pop { 50% { transform: scale(1.4); } }
	@keyframes rise { from { opacity: 0; transform: translate(var(--tx, 0), 0.6rem); } }
	.announce { --tx: -50%; }
	@keyframes blink { 50% { opacity: 0.2; } }
	@keyframes glow { to { background-position: 10% 0, 90% 100%, 40% 60%, 0 0; filter: hue-rotate(25deg); } }
	@media (prefers-reduced-motion: reduce) {
		.deck.party, .vinyl, .heart.on, .msg, .announce { animation: none; }
	}
</style>
