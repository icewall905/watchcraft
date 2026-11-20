// Lazy-loaded Shaka Player (DASH-only build keeps the bundle small).
type ShakaNs = typeof import('shaka-player/dist/shaka-player.dash.js').default;
let mod: Promise<ShakaNs> | null = null;

export function loadShaka(): Promise<ShakaNs> {
	if (!mod) {
		mod = import('shaka-player/dist/shaka-player.dash.js').then((m) => {
			const shaka = ((m as unknown as { default?: ShakaNs }).default ?? m) as ShakaNs;
			shaka.polyfill.installAll();
			return shaka;
		});
	}
	return mod;
}

// HLS build for Jellyfin streams; loaded only by the Jellyfin player.
type ShakaHlsNs = typeof import('shaka-player/dist/shaka-player.hls.js').default;
let hlsMod: Promise<ShakaHlsNs> | null = null;

export function loadShakaHls(): Promise<ShakaHlsNs> {
	if (!hlsMod) {
		hlsMod = import('shaka-player/dist/shaka-player.hls.js').then((m) => {
			const shaka = ((m as unknown as { default?: ShakaHlsNs }).default ?? m) as ShakaHlsNs;
			shaka.polyfill.installAll();
			return shaka;
		});
	}
	return hlsMod;
}
