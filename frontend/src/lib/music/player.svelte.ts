// Music player state: the queue, shuffle, the live DJ and favourites shared by every list on the page.
// The <video> element and Shaka live in Player.svelte; this store only decides what plays next.
import { SvelteSet } from 'svelte/reactivity';
import { toast } from '$lib/stores/toast.svelte';
import { music, type Track, type Favorites } from './api';

export type Mode = 'list' | 'shuffle' | 'dj';
export type DjMessage = { id: number; text: string; kind: 'dj' | 'request' | 'info' };

const DJ_AHEAD = 3; // tracks the DJ keeps lined up behind the current one
const RETRY_MS = 6000;
const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();

export function shuffled<T>(list: T[]): T[] {
	const a = [...list];
	for (let i = a.length - 1; i > 0; i--) {
		const j = Math.floor(Math.random() * (i + 1));
		[a[i], a[j]] = [a[j], a[i]];
	}
	return a;
}

/** Make a set equal to `want`, touching only what differs (no churn for effects that read it). */
function syncSet(set: SvelteSet<string>, want: Set<string>) {
	for (const v of [...set]) if (!want.has(v)) set.delete(v);
	for (const v of want) if (!set.has(v)) set.add(v);
}

class MusicPlayer {
	queue = $state<Track[]>([]);
	index = $state(-1);
	/** Bumped on every "play the track at index", so replaying the same video still reloads. */
	playToken = $state(0);
	mode = $state<Mode>('list');
	/** Name of what is playing ("For you", a mix name, "DJ · Party"). */
	source = $state('');
	vibe = $state('');
	party = $state(false);
	djBusy = $state(false);
	djVoice = $state(false);
	/** Karaoke: lyrics over the video, and optionally the vocals turned down. */
	showLyrics = $state(false);
	vocalCut = $state(false);
	/** The DJ's mic: newest last. */
	messages = $state<DjMessage[]>([]);
	/** Shown big over the video when a DJ track starts. */
	announce = $state('');
	favTracks = new SvelteSet<string>();
	favArtists = new SvelteSet<string>();
	/** The page sets this to show a mix the player just saved. */
	savedMix: ((m: import('./api').Mix) => void) | null = null;
	/** Bumped only when the user changes a favourite here: lists that show favourites reload on it. */
	favVersion = $state(0);
	private played: string[] = [];
	private msgId = 0;
	private djSeq = 0;
	private pending: Promise<void> | null = null;
	private pendingSeq = -1;
	private retry: ReturnType<typeof setTimeout> | undefined;
	private startedAt = 0;
	/** The deck has nothing left to play and waits for the DJ's next find. */
	private stalled = false;

	get current(): Track | null {
		return this.queue[this.index] ?? null;
	}
	get upNext(): Track[] {
		return this.queue.slice(this.index + 1);
	}
	get active() {
		return this.index >= 0 && !!this.current;
	}

	// ---------- favourites

	syncFavorites(f: Favorites) {
		syncSet(this.favTracks, new Set(f.tracks.map((t) => t.video_id)));
		syncSet(this.favArtists, new Set(f.artists.map((a) => a.key)));
	}
	/** Seed favourite flags from tracks the server just decorated. */
	learn(tracks: Track[]) {
		for (const t of tracks) {
			if (t.fav && !this.favTracks.has(t.video_id)) this.favTracks.add(t.video_id);
			const a = norm(t.artist || '');
			if (t.fav_artist && a && !this.favArtists.has(a)) this.favArtists.add(a);
		}
	}
	isFav(t: Track) {
		return this.favTracks.has(t.video_id);
	}
	isFavArtist(name: string) {
		return this.favArtists.has(norm(name));
	}
	async toggleFav(t: Track) {
		const on = !this.isFav(t);
		if (on) this.favTracks.add(t.video_id); else this.favTracks.delete(t.video_id);
		try {
			this.syncFavorites(await music.favTrack(t, on));
			this.favVersion++;
			if (on) toast.success(`❤️ ${t.title}`);
		} catch {
			if (on) this.favTracks.delete(t.video_id); else this.favTracks.add(t.video_id);
		}
	}
	async toggleFavArtist(name: string, artistId = '') {
		const on = !this.isFavArtist(name);
		try {
			this.syncFavorites(await music.favArtist(name, on, artistId));
			this.favVersion++;
			if (on) toast.success(`⭐ ${name} is a favourite now`);
		} catch {
			/* toast already shown */
		}
	}

	// ---------- queue

	private go(index: number) {
		this.index = index;
		this.stalled = false;
		this.playToken++;
	}

	/** Play a list from `start`. Shuffle keeps the tapped track first and shuffles the rest. */
	play(tracks: Track[], start = 0, opts: { shuffle?: boolean; source?: string } = {}) {
		if (!tracks.length) return;
		this.finishCurrent();
		this.cancelDj();
		const at = Math.max(0, Math.min(start, tracks.length - 1));
		const first = tracks[at];
		this.queue = opts.shuffle ? [first, ...shuffled(tracks.filter((_, i) => i !== at))] : [...tracks];
		this.mode = opts.shuffle ? 'shuffle' : 'list';
		this.source = opts.source ?? '';
		this.announce = '';
		this.go(opts.shuffle ? 0 : at);
	}
	shuffleAll(tracks: Track[], source = '') {
		if (!tracks.length) return;
		this.play(tracks, Math.floor(Math.random() * tracks.length), { shuffle: true, source });
	}
	playNext(t: Track) {
		if (!this.active) return this.play([t]);
		if (t.video_id === this.current?.video_id) return;
		const q = this.queue.filter((x, i) => i <= this.index || x.video_id !== t.video_id);
		q.splice(this.index + 1, 0, t);
		this.queue = q;
		toast.info(`Up next: ${t.title}`);
	}
	enqueue(t: Track) {
		if (!this.active) return this.play([t]);
		if (t.video_id === this.current?.video_id || this.upNext.some((x) => x.video_id === t.video_id)) return;
		this.queue = [...this.queue, t];
		toast.info(`Queued: ${t.title}`);
	}
	removeUpcoming(i: number) {
		const at = this.index + 1 + i;
		if (at > this.index && at < this.queue.length) this.queue = this.queue.filter((_, k) => k !== at);
		this.keepDjFed();
	}
	jumpTo(i: number) {
		const at = this.index + 1 + i;
		if (at >= this.queue.length) return;
		this.finishCurrent(true);
		this.go(at);
		this.onAdvance();
	}

	/** `skipped` tells the taste log the crowd did not want this one. */
	async next(skipped = true) {
		if (!this.active) return;
		this.finishCurrent(skipped);
		if (this.index + 1 >= this.queue.length && this.mode === 'dj') {
			const seq = this.djSeq, tok = this.playToken;
			await this.topUp();
			// A second skip, a prev or a new set while we waited already moved the deck.
			if (seq !== this.djSeq || this.mode !== 'dj' || tok !== this.playToken) return;
		}
		if (this.index + 1 < this.queue.length) {
			this.go(this.index + 1);
			this.onAdvance();
		} else if (this.mode === 'shuffle' && this.queue.length > 1) {
			const cur = this.current!;
			this.queue = [cur, ...shuffled(this.queue.filter((t) => t !== cur))];
			this.go(1);
		} else if (this.mode === 'dj') {
			this.stalled = true;
			this.say('The crates ran dry for a moment — trying again…', 'info');
			this.scheduleRetry();
		} else {
			this.stop();
		}
	}
	prev() {
		if (this.index > 0) {
			this.finishCurrent(true);
			this.go(this.index - 1);
		}
	}
	stop() {
		this.finishCurrent();
		this.cancelDj();
		this.queue = [];
		this.index = -1;
		this.party = false;
		this.vocalCut = false; // the deck's <video> goes away, and its audio graph with it
		this.announce = '';
	}

	/** Player.svelte calls this once the current track really starts. */
	started() {
		this.startedAt = Date.now();
	}
	private finishCurrent(skipped = false) {
		const cur = this.current;
		if (!cur || !this.startedAt) return;
		// Less than 30 s in counts as a skip, whatever button ended it.
		const early = Date.now() - this.startedAt < 30_000;
		this.startedAt = 0;
		this.played = [...this.played.filter((v) => v !== cur.video_id), cur.video_id].slice(-60);
		void music.play(cur.video_id, skipped && early).catch(() => {});
	}
	private onAdvance() {
		const cur = this.current;
		if (this.mode !== 'dj') return;
		if (cur?.dj_line) this.say(cur.dj_line, 'dj', true);
		this.keepDjFed();
	}
	private keepDjFed() {
		if (this.mode === 'dj' && this.upNext.length < DJ_AHEAD - 1) void this.topUp();
	}

	// ---------- the DJ

	say(text: string, kind: DjMessage['kind'] = 'dj', announce = false) {
		this.messages = [...this.messages, { id: ++this.msgId, text, kind }].slice(-30);
		if (announce && kind === 'dj') {
			this.announce = text;
			if (this.djVoice) speak(text);
		}
	}

	/** Forget any DJ fetch in flight: its answer belongs to an old set or vibe. */
	private cancelDj() {
		this.djSeq++;
		this.pending = null;
		this.djBusy = false;
		clearTimeout(this.retry);
	}
	private scheduleRetry() {
		clearTimeout(this.retry);
		const seq = this.djSeq;
		this.retry = setTimeout(() => {
			if (seq !== this.djSeq || this.mode !== 'dj') return;
			void this.topUp().then(() => {
				if (seq !== this.djSeq) return;
				// Nothing playing yet (the set's first fetch failed) or stuck after the last track.
				if ((this.index < 0 || this.stalled) && this.index + 1 < this.queue.length) {
					this.go(this.index + 1);
					const cur = this.current;
					if (cur?.dj_line) this.say(cur.dj_line, 'dj', true);
					this.keepDjFed();
				} else if (this.upNext.length === 0) this.scheduleRetry();
			});
		}, RETRY_MS);
	}

	/**
	 * Start a DJ set. `seeds` play first (a song, your favourites, a mix); the DJ keeps lining up
	 * more behind them while you listen.
	 */
	async startDj(vibe = '', seeds: Track[] = []) {
		this.finishCurrent();
		this.cancelDj();
		this.mode = 'dj';
		this.vibe = vibe;
		this.source = vibe ? `DJ · ${vibe}` : 'DJ';
		this.queue = [...seeds];
		this.index = -1;
		this.messages = [];
		this.say(vibe ? `🎧 DJ booth is open — vibe: ${vibe}` : '🎧 DJ booth is open!', 'info');
		if (seeds.length) this.go(0);
		const seq = this.djSeq;
		if (seeds.length < DJ_AHEAD) await this.topUp();
		if (seq !== this.djSeq) return; // a newer set took over meanwhile
		if (this.index < 0 && this.queue.length) {
			this.go(0);
			const first = this.queue[0];
			if (first.dj_line) this.say(first.dj_line, 'dj', true);
		}
	}
	/** Hand the running queue to the DJ: what is lined up stays, the DJ keeps it going after. */
	djTakeOver() {
		if (!this.active) return void this.startDj(this.vibe);
		this.cancelDj();
		this.mode = 'dj';
		this.source = this.vibe ? `DJ · ${this.vibe}` : 'DJ';
		this.say('🎧 The DJ has the decks now!', 'info');
		this.keepDjFed();
	}
	setVibe(vibe: string) {
		this.vibe = vibe;
		this.source = vibe ? `DJ · ${vibe}` : 'DJ';
		this.say(`Vibe switched to ${vibe || 'anything goes'} ✨`, 'info');
		if (this.mode !== 'dj') return;
		// Drop what the DJ lined up for the old vibe and dig again.
		this.cancelDj();
		this.queue = this.queue.slice(0, this.index + 1);
		const seq = this.djSeq;
		void this.topUp().then(() => {
			if (seq !== this.djSeq || !this.stalled) return;
			// The deck sat on a finished track: start the new vibe's first find.
			if (this.index + 1 < this.queue.length) {
				this.go(this.index + 1);
				this.onAdvance();
			} else this.scheduleRetry();
		});
	}

	/** Guest request: the DJ finds it and slots it in right after the current song. */
	async request(text: string) {
		const ask = text.trim();
		if (!ask) return;
		if (!this.active) {
			this.party = true;
			await this.startDj(this.vibe);
		}
		this.say(`🙋 ${ask}`, 'request');
		const seq = this.djSeq;
		try {
			const r = await music.dj({ played: this.playedIds(), queue: this.queue.map((t) => t.video_id), vibe: this.vibe, request: ask, n: 1 });
			if (seq !== this.djSeq) return;
			this.learn(r.tracks);
			if (!r.tracks.length) {
				this.say("Couldn't find that one in the crates 😅", 'dj');
				return;
			}
			const t = { ...r.tracks[0], dj_line: r.line };
			const q = this.queue.filter((x, i) => i <= this.index || x.video_id !== t.video_id);
			q.splice(this.index + 1, 0, t);
			this.queue = q;
			this.say(r.line, 'dj');
			if (this.mode !== 'dj') toast.info(`Up next: ${t.title}`);
		} catch {
			this.say('The request line is busy — try again in a sec.', 'info');
		}
	}

	private playedIds() {
		const cur = this.current?.video_id;
		return cur ? [...this.played.filter((v) => v !== cur), cur] : this.played;
	}

	/** Line up more tracks. Concurrent callers share one fetch; a cancelled fetch is ignored. */
	topUp(n = DJ_AHEAD): Promise<void> {
		if (this.pending && this.pendingSeq === this.djSeq) return this.pending;
		const seq = this.djSeq;
		this.djBusy = true;
		const run = (async () => {
			try {
				const r = await music.dj({ played: this.playedIds(), queue: this.queue.map((t) => t.video_id), vibe: this.vibe, n });
				if (seq !== this.djSeq || this.mode !== 'dj') return;
				this.learn(r.tracks);
				const have = new Set(this.queue.map((t) => t.video_id));
				const fresh = r.tracks.filter((t) => !have.has(t.video_id));
				if (fresh.length) {
					fresh[0] = { ...fresh[0], dj_line: r.line };
					this.queue = [...this.queue, ...fresh];
					this.say(`🔎 Found ${fresh.length} fresh ${fresh.length === 1 ? 'track' : 'tracks'}: ${fresh.map((t) => t.artist).join(', ')}`, 'info');
				} else if (!this.upNext.length) {
					this.say(r.line || 'Digging for more…', 'info');
					this.scheduleRetry();
				}
			} catch {
				if (seq !== this.djSeq) return;
				this.say('Lost the signal for a moment — trying again shortly.', 'info');
				this.scheduleRetry();
			} finally {
				if (seq === this.djSeq) {
					this.pending = null;
					this.djBusy = false;
				}
			}
		})();
		this.pending = run;
		this.pendingSeq = seq;
		return run;
	}
}

function speak(text: string) {
	try {
		const synth = window.speechSynthesis;
		if (!synth) return;
		synth.cancel();
		const u = new SpeechSynthesisUtterance(text.replace(/[\p{Extended_Pictographic}]/gu, ''));
		u.rate = 1.05;
		u.pitch = 1.1;
		synth.speak(u);
	} catch {
		/* no voice on this device */
	}
}

export const player = new MusicPlayer();
