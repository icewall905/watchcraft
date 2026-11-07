// Karaoke vocal reducer. Lead vocals are usually mixed dead centre, so subtracting the right channel from
// the left removes most of them (and the centred bass and kick). The low end is added back from the mono
// sum through a low-pass filter. Works on any stereo mix, imperfectly by nature — a party trick, not stems.
// Web Audio can take over a media element only once, so the graph is built once per element and then
// switched between the normal and the karaoke path.

type Graph = { ctx: AudioContext; normal: GainNode; karaoke: GainNode };
const graphs = new WeakMap<HTMLMediaElement, Graph>();

function build(el: HTMLMediaElement): Graph | null {
	const Ctx = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
	if (!Ctx) return null;
	const ctx = new Ctx();
	const src = ctx.createMediaElementSource(el);
	const normal = ctx.createGain();
	const karaoke = ctx.createGain();
	karaoke.gain.value = 0;
	src.connect(normal).connect(ctx.destination);

	const split = ctx.createChannelSplitter(2);
	const invert = ctx.createGain();
	invert.gain.value = -1;
	const side = ctx.createGain(); // L - R
	src.connect(split);
	split.connect(side, 0);
	split.connect(invert, 1).connect(side);
	const low = ctx.createBiquadFilter(); // keep the bass the subtraction removed
	low.type = 'lowpass';
	low.frequency.value = 140;
	const mono = ctx.createGain();
	mono.gain.value = 0.5;
	src.connect(mono).connect(low);
	const sum = ctx.createGain();
	side.connect(sum);
	low.connect(sum);
	sum.connect(karaoke).connect(ctx.destination);
	return { ctx, normal, karaoke };
}

/** Turn the vocal reducer on or off for this element. Call from a click: browsers start audio only then. */
export function setVocalCut(el: HTMLMediaElement, on: boolean): boolean {
	let g = graphs.get(el);
	if (!g && !on) return true;
	if (!g) {
		try {
			g = build(el) ?? undefined;
		} catch {
			g = undefined;
		}
		if (!g) return false;
		graphs.set(el, g);
	}
	void g.ctx.resume().catch(() => {});
	const t = g.ctx.currentTime;
	g.normal.gain.setTargetAtTime(on ? 0 : 1, t, 0.05);
	g.karaoke.gain.setTargetAtTime(on ? 1 : 0, t, 0.05);
	return true;
}

export function closeVocals(el: HTMLMediaElement | undefined) {
	const g = el && graphs.get(el);
	if (g) {
		graphs.delete(el!);
		void g.ctx.close().catch(() => {});
	}
}
