// Warm the backend stream caches for videos the user is about to play: card focus/hover and the
// autoplay target. A warm video skips the whole "Preparing video" extraction on click — for
// AI-picked videos the curator has usually extracted the info already, and this asks for it early.
// Cheap when nothing is cold: the backend serves detail from its caches without touching YouTube.
import { api, profileHeaders } from '$lib/api';
import { session } from '$lib/stores/session.svelte';

let lastAt = 0;
const inFlight = new Set<string>();
const warmed = new Set<string>();

export function warmVideo(id: string, opts: { offline?: boolean; manifest?: boolean; force?: boolean } = {}) {
	if (!id || opts.offline || warmed.has(id) || inFlight.has(id)) return;
	const now = Date.now();
	if (!opts.force && now - lastAt < 4000) return; // browsing must not become an extraction storm
	lastAt = now;
	inFlight.add(id);
	api.video(id)
		.then(
			() => {
				warmed.add(id);
				// Also warm the DASH manifest (its format checks) when a play is imminent.
				if (opts.manifest)
					void fetch(api.manifestUrl(id, session.playerPrefs.max_height), { cache: 'no-store', headers: profileHeaders() }).catch(() => {});
			},
			() => {}
		)
		.finally(() => inFlight.delete(id));
}
