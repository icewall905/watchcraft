// Desktop notifications for new alerts, while a WatchCraft tab is open.
// No push server: the page polls the same alerts endpoint the Alerts page uses.
import { hub } from '$lib/hub/api';
import { session } from './session.svelte';

const KEY = 'bt.notify.enabled';
const SEEN = 'bt.notify.seen';
const POLL_MS = 60_000;

class BrowserNotify {
	supported = $state(typeof window !== 'undefined' && 'Notification' in window);
	permission = $state<NotificationPermission>('default');
	enabled = $state(false);
	private timer: ReturnType<typeof setInterval> | undefined;
	private busy = false;

	constructor() {
		if (!this.supported) return;
		this.permission = Notification.permission;
		try { this.enabled = localStorage.getItem(KEY) === '1'; } catch { /* storage unavailable */ }
	}

	async request() {
		if (!this.supported) return;
		this.permission = await Notification.requestPermission();
		if (this.permission === 'granted') this.set(true);
	}

	set(on: boolean) {
		this.enabled = on && this.permission === 'granted';
		try { localStorage.setItem(KEY, this.enabled ? '1' : '0'); } catch { /* storage unavailable */ }
	}

	/** Start polling once the session is ready; returns a teardown for the layout. */
	start() {
		if (!this.supported || this.timer) return () => {};
		this.timer = setInterval(() => void this.poll(), POLL_MS);
		void this.poll();
		return () => { clearInterval(this.timer); this.timer = undefined; };
	}

	private seen(): number {
		try { return Number(localStorage.getItem(SEEN) || 0); } catch { return 0; }
	}

	private async poll() {
		if (this.busy || !this.enabled || this.permission !== 'granted') return;
		if (!session.ready || session.needsLogin || !session.profileId) return;
		this.busy = true;
		try {
			const page = await hub.alerts('new');
			const fresh = page.items.filter((a) => a.id > this.seen());
			// Remember the newest id even when nothing is shown, so a first visit does not replay history.
			const newest = page.items.reduce((n, a) => Math.max(n, a.id), this.seen());
			try { localStorage.setItem(SEEN, String(newest)); } catch { /* storage unavailable */ }
			for (const a of fresh.slice(0, 3)) {
				const n = new Notification(`${a.severity.toUpperCase()}: ${a.title}`.slice(0, 120), {
					body: (a.reason || 'New alert in WatchCraft').slice(0, 200),
					tag: `wc-alert-${a.id}`
				});
				n.onclick = () => { window.focus(); location.href = '/alerts'; };
			}
		} catch { /* offline or locked profile: try again on the next tick */ }
		finally { this.busy = false; }
	}
}

export const browserNotify = new BrowserNotify();
