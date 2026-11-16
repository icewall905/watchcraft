// Presentation belongs to this browser, so changing a desktop never changes the TV.
export type RemoteKeys = 'auto' | 'on' | 'off';
class DeviceMode {
	mode = $state<'desktop' | 'tv'>('desktop');
	/** Remote-control keys (arrows move focus, Backspace = Back): auto = only in TV mode. */
	remoteKeys = $state<RemoteKeys>('auto');
	/** Page this browser opens on when it starts at `/` (The Screen). Must be a sidebar destination. */
	landing = $state('/');
	get remote() { return this.remoteKeys === 'on' || (this.remoteKeys === 'auto' && this.mode === 'tv'); }
	init() {
		try {
			this.mode = localStorage.getItem('wc.deviceMode') === 'tv' ? 'tv' : 'desktop';
			const keys = localStorage.getItem('wc.remoteKeys');
			this.remoteKeys = keys === 'on' || keys === 'off' ? keys : 'auto';
			this.landing = localStorage.getItem('wc.landing') || '/';
		} catch { /* Storage may be unavailable. */ }
	}
	set(mode: 'desktop' | 'tv') {
		this.mode = mode;
		try { localStorage.setItem('wc.deviceMode', mode); } catch { /* Keep the session preference. */ }
	}
	setLanding(path: string) {
		this.landing = path;
		try { localStorage.setItem('wc.landing', path); } catch { /* Keep the session preference. */ }
	}
	setRemoteKeys(value: RemoteKeys) {
		this.remoteKeys = value;
		try { localStorage.setItem('wc.remoteKeys', value); } catch { /* Keep the session preference. */ }
	}
}

export const deviceMode = new DeviceMode();
