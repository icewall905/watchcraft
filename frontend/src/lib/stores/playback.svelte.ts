import { goto } from '$app/navigation';
import { request, ApiError } from '$lib/api';
import { session } from './session.svelte';
import { toast } from './toast.svelte';

export interface PlaybackTarget {
	id: string;
	name: string;
	online: boolean;
	paired: boolean;
	state: { video_id?: string | null; paused?: boolean; position_s?: number; error?: string };
}
interface Receiver { id: string; name: string; token: string; ack?: string; pair_code?: string }
interface Command { id: string; action: string; video_id: string | null; category_id: number | null; profile_id: number }
const KEY = 'bt.playback.receiver';

class Playback {
	targets = $state<PlaybackTarget[]>([]);
	selectedId = $state<string | null>(null);
	receiver = $state<Receiver | null>(null);
	open = $state(false);
	error = $state('');
	pending = $state(false);
	// Set (target id) when the selected screen still needs this profile's pairing code.
	needsPair = $state<string | null>(null);
	pairing = $state(false);
	selected = $derived(this.targets.find(t => t.id === this.selectedId));
	private ack = '';
	private running = false;
	private busy = false;
	private sending = false;

	start() {
		if (this.running) return () => {};
		this.running = true;
		try {
			const stored = JSON.parse(localStorage.getItem(KEY) || 'null');
			if (stored?.id && stored?.token && stored?.name) { this.receiver = stored; this.ack = stored.ack ?? ''; }
		} catch { /* unavailable storage */ }
		const click = (e: MouseEvent) => {
			if (!this.selectedId || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
			const a = (e.target as Element)?.closest('a[href]');
			if (!a || a.getAttribute('target') === '_blank') return;
			const url = new URL((a as HTMLAnchorElement).href, location.href);
			const match = url.pathname.match(/^\/watch\/([A-Za-z0-9_-]{11})\/?$/);
			if (url.origin !== location.origin || !match) return;
			e.preventDefault();
			e.stopImmediatePropagation();
			if (this.needsPair) { this.open = true; return; }
			void this.send('play', match[1], Number(url.searchParams.get('c')) || null);
		};
		document.addEventListener('click', click, true);
		void this.poll();
		const timer = setInterval(() => void this.poll(), 2000);
		return () => { this.running = false; clearInterval(timer); document.removeEventListener('click', click, true); };
	}

	select(id: string | null) {
		this.selectedId = id;
		this.needsPair = id && !this.targets.find(t => t.id === id)?.paired ? id : null;
	}

	async pair(code: string) {
		const id = this.needsPair;
		if (!id || this.pairing) return;
		this.pairing = true;
		try {
			await request<{ paired: boolean }>(`/playback/targets/${id}/pair`, { body: { code } });
			this.needsPair = null;
			toast.success(`Paired with ${this.selected?.name ?? 'screen'}`);
			await this.poll();
		} catch { /* request displays errors; the code entry stays open */ }
		finally { this.pairing = false; }
	}

	async register(name: string) {
		this.pending = true;
		try {
			// Persist credentials before enabling the receiver; fail visibly if storage is unavailable.
			const r = await request<Receiver>('/playback/targets', { body: { name } });
			try { localStorage.setItem(KEY, JSON.stringify(r)); }
			catch {
				await request(`/playback/targets/${r.id}`, { method: 'DELETE', headers: { 'X-Receiver-Token': r.token } });
				toast.error('Allow browser storage to register this screen.');
				return;
			}
			this.receiver = r;
			this.selectedId = null;
			this.ack = '';
			this.needsPair = null;
			this.error = '';
			toast.success(`${name} is ready to receive videos`);
			await this.poll();
		} finally { this.pending = false; }
	}

	async unregister() {
		const r = this.receiver;
		if (!r) return;
		await request(`/playback/targets/${r.id}`, { method: 'DELETE', headers: { 'X-Receiver-Token': r.token } });
		this.receiver = null;
		localStorage.removeItem(KEY);
		this.error = '';
		await this.poll();
	}

	async send(action: string, video_id?: string, category_id?: number | null) {
		if (!this.selectedId || this.sending) return;
		this.sending = true;
		try {
			await request(`/playback/targets/${this.selectedId}/commands`, { body: { action, video_id, category_id } });
			if (action === 'play') toast.success(`Sent to ${this.selected?.name ?? 'screen'}`);
		} catch (e) {
			// An unpaired profile must pair once; the picker shows the code entry.
			if (e instanceof ApiError && e.status === 403) this.needsPair = this.selectedId;
			/* request displays errors; keep the selected target to avoid unintended local playback */
		}
		finally { this.sending = false; }
	}

	private async receive(command: Command) {
		this.ack = command.id;
		if (this.receiver) {
			this.receiver = { ...this.receiver, ack: command.id };
			try { localStorage.setItem(KEY, JSON.stringify(this.receiver)); } catch { /* retain in-memory acknowledgement */ }
		}
		this.error = '';
		try {
			if (command.action === 'play' && command.video_id) {
				window.dispatchEvent(new Event('bt:before-cast'));
				if (!(await session.switchProfile(command.profile_id))) throw new Error('Profile was not unlocked');
				const query = new URLSearchParams({ cast: command.id });
				if (command.category_id) query.set('c', String(command.category_id));
				await goto(`/watch/${command.video_id}?${query}`);
			} else {
				const video = document.querySelector<HTMLVideoElement>('.watch video');
				if (command.action === 'stop') { video?.pause(); await goto('/'); }
				else if (command.action === 'pause') video?.pause();
				else if (command.action === 'resume') await video?.play();
			}
		} catch { this.error = 'Playback could not start. Press Play on this screen.'; }
	}

	private async poll() {
		if (this.busy || !session.ready || session.needsLogin) return;
		this.busy = true;
		try {
			const r = this.receiver;
			if (r) {
				const video = document.querySelector<HTMLVideoElement>('.watch video');
				const match = location.pathname.match(/^\/watch\/([A-Za-z0-9_-]{11})/);
				const reply = await request<{ command: Command | null; pair_code?: string }>(`/playback/targets/${r.id}/heartbeat`, {
					body: { ack: this.ack, video_id: match?.[1] ?? null, paused: video?.paused ?? true,
						position_s: Number.isFinite(video?.currentTime) ? video!.currentTime : 0, error: this.error },
					headers: { 'X-Receiver-Token': r.token }, toast: false,
					signal: AbortSignal.timeout(8000)
				});
				if (reply.command && this.receiver?.id === r.id) await this.receive(reply.command);
				// The pairing code lets upgraded screens show their code after a server update.
				if (this.receiver?.id === r.id && reply.pair_code && this.receiver.pair_code !== reply.pair_code) {
					this.receiver = { ...this.receiver, pair_code: reply.pair_code };
					try { localStorage.setItem(KEY, JSON.stringify(this.receiver)); } catch { /* keep in-memory */ }
				}
			}
			if (this.open || this.selectedId) {
				this.targets = await request<PlaybackTarget[]>('/playback/targets', { toast: false, signal: AbortSignal.timeout(8000) });
			}
		} catch (e) {
			if (e instanceof ApiError && e.status === 403 && this.receiver) {
				this.receiver = null;
				localStorage.removeItem(KEY);
				this.error = 'Screen registration expired. Register this screen again.';
			}
		} finally { this.busy = false; }
	}
}
export const playback = new Playback();
