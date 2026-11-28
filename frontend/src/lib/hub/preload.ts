// Gently downloads every grid preview of the open board in the background, so scrolling never waits.
// A few at a time, in idle time, nearest the viewport first; paused while the tab is hidden.
import { boardMediaReady, boardMediaUrl } from '$lib/hub/BoardImage.svelte';

const CONCURRENT = 4;
let queue: string[] = [];
let running = 0;
let generation = 0;

const idle = (fn: () => void) =>
	typeof requestIdleCallback === 'function' ? requestIdleCallback(fn, { timeout: 500 }) : setTimeout(fn, 50);

function pump(gen: number) {
	if (gen !== generation || typeof document === 'undefined') return;
	if (document.visibilityState === 'hidden') {
		document.addEventListener('visibilitychange', () => pump(gen), { once: true });
		return;
	}
	while (running < CONCURRENT && queue.length) {
		const url = queue.shift()!;
		if (boardMediaReady(url)) continue;
		running++;
		idle(() => {
			boardMediaUrl(url)
				.catch(() => {})
				.finally(() => {
					running--;
					pump(gen);
				});
		});
	}
}

/** Replace the queue with these URLs (in priority order). Already-downloaded ones are skipped. */
export function preloadImages(urls: string[]) {
	generation++;
	queue = [...new Set(urls)].filter((u) => !boardMediaReady(u));
	pump(generation);
}

/** Stop queued work, e.g. when the board or profile changes. In-flight requests finish quietly. */
export function stopPreloading() {
	generation++;
	queue = [];
}
