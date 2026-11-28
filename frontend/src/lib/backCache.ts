// Last data each feed page showed, kept across navigations. Coming back (browser Back, the player's Back) renders it
// at once, so SvelteKit's own scroll restore lands on a full-height page instead of a short loading skeleton.

const entries = new Map<string, unknown>();
let returning = false;

/** Called by the layout before every navigation. */
export function markNavigation(type: string) {
	returning = type === 'popstate';
}

/** Cached value for `key`; `reuse` is true on a Back/Forward visit, where the page shows it as it was instead of
 * reloading (a reload would reorder rows under the restored scroll position). Normal visits reload. */
export function fromBackCache<T>(key: string): { value: T; reuse: boolean } | null {
	// One reuse per Back visit: later reloads on the same page (agent events, tab switches) fetch fresh data.
	const reuse = returning;
	returning = false;
	return entries.has(key) ? { value: entries.get(key) as T, reuse } : null;
}

/** Store what the page shows. */
export function toBackCache(key: string, value: unknown) {
	entries.set(key, value);
}
