import { goto } from '$app/navigation';
import { request, ApiError } from '$lib/api';
import { session } from './session.svelte';
import { toast } from './toast.svelte';
import { setCastGrant } from '$lib/jellyfin/api';

export interface PlaybackTarget {
	id: string;
	name: string;
	online: boolean;
	paired: boolean;
	/** Profile ids allowed to send here; only administrators receive this list. */
	paired_profiles?: number[];
	state: { video_id?: string | null; jellyfin_id?: string | null; paused?: boolean; position_s?: number; duration_s?: number | null; error?: string };
}
interface Receiver { id: string; name: string; token: string; ack?: string; pair_code?: string }
interface Command { id: string; action: string; video_id: string | null; jellyfin_id?: string | null; grant?: string; category_id: number | null; position_s?: number | null; profile_id: number }
const KEY = 'bt.playback.receiver';
const JF_PLAY = /^\/jellyfin\/play\/([0-9a-fA-F-]{32,36})\/?$/;
/** The playing <video> on this screen: the YouTube watch page or The Marquee player. */
const playingVideo = () => document.querySelector<HTMLVideoElement>(location.pathname.startsWith('/jellyfin/play/') ? '.player video' : '.watch video');

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
	/** A Marquee title waiting for the user to pick (or pair) a screen in the picker. */
	pendingJellyfin = $state<{ id: string; position_s?: number } | null>(null);
	selected = $derived(this.targets.find(t => t.id === this.selectedId));
	private ack = '';
	private running = false;
	private busy = false;
	private sending = false;
	private targetsRevision = 0;

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
			const jf = url.pathname.match(JF_PLAY);
			if (url.origin !== location.origin || !(match || jf)) return;
			e.preventDefault();
			e.stopImmediatePropagation();
			if (jf) { void this.castJellyfin(jf[1]); return; }
			if (this.needsPair) { this.open = true; return; }
			void this.send('play', match![1], Number(url.searchParams.get('c')) || null);
		};
		document.addEventListener('click', click, true);
		void this.poll();
		const timer = setInterval(() => void this.poll(), 2000);
		return () => { this.running = false; clearInterval(timer); document.removeEventListener('click', click, true); };
	}

	select(id: string | null) {
		this.selectedId = id;
		this.needsPair = id && !this.targets.find(t => t.id === id)?.paired ? id : null;
		if (!id) this.pendingJellyfin = null;
		else if (!this.needsPair) void this.flushJellyfin();
	}

	/** Send a Marquee (Jellyfin) title to the selected screen, or open the picker to choose one first. */
	async castJellyfin(id: string, position_s?: number): Promise<boolean> {
		if (!this.selectedId || this.needsPair || !this.selected?.online) {
			this.pendingJellyfin = { id, position_s };
			this.open = true;
			return false;
		}
		return this.send('play', undefined, null, position_s, id);
	}

	private async flushJellyfin() {
		const p = this.pendingJellyfin;
		if (!p || !this.selectedId || this.needsPair) return;
		this.pendingJellyfin = null;
		if (await this.send('play', undefined, null, p.position_s, p.id)) this.open = false;
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
			await this.flushJellyfin();
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

	/** Screens change from other devices too, so the settings tab refreshes on demand. */
	async refresh() {
		const revision = ++this.targetsRevision;
		const targets = await request<PlaybackTarget[]>('/playback/targets', { toast: false, signal: AbortSignal.timeout(8000) });
		// A poll that started before pairing changed must not restore stale permissions.
		if (revision === this.targetsRevision) this.targets = targets;
	}

	/** Stop sending from this profile; the screen keeps its other pairings. */
	async forget(id: string) {
		this.targetsRevision++;
		await request(`/playback/targets/${id}/pair`, { method: 'DELETE' });
		// Revoke local permission immediately, even if refresh fails or is superseded by a poll.
		this.targetsRevision++;
		this.targets = this.targets.map(t => t.id === id ? { ...t, paired: false,
			paired_profiles: t.paired_profiles?.filter(pid => pid !== session.profileId) } : t);
		if (this.selectedId === id) this.select(null);
		await this.refresh();
	}

	async forgetSelected() {
		if (this.selectedId) await this.forget(this.selectedId);
	}

	/** Administrators: revoke one profile's pairing, replace the code, or remove the screen. */
	async revoke(id: string, profile: number) {
		await request(`/playback/targets/${id}/pair?profile=${profile}`, { method: 'DELETE' });
		await this.refresh();
	}

	async rotateCode(id: string) {
		await request(`/playback/targets/${id}/pair-code`, { method: 'POST' });
		await this.refresh();
	}

	async remove(id: string) {
		await request(`/playback/targets/${id}/registration`, { method: 'DELETE' });
		if (this.selectedId === id) this.select(null);
		await this.refresh();
	}

	async send(action: string, video_id?: string, category_id?: number | null, position_s?: number, jellyfin_id?: string): Promise<boolean> {
		if (!this.selectedId || this.sending) return false;
		this.sending = true;
		try {
			await request(`/playback/targets/${this.selectedId}/commands`, { body: { action, video_id, jellyfin_id, category_id, position_s } });
			if (action === 'play') toast.success(`Sent to ${this.selected?.name ?? 'screen'}`);
			return true;
		} catch (e) {
			// An unpaired profile must pair once; the picker shows the code entry.
			if (e instanceof ApiError && e.status === 403) this.needsPair = this.selectedId;
			/* request displays errors; keep the selected target to avoid unintended local playback */
		}
		finally { this.sending = false; }
		return false;
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
				const own = await this.becomeSender(command.profile_id);
				const query = new URLSearchParams({ cast: command.id });
				if (command.category_id && own) query.set('c', String(command.category_id));
				await goto(`/watch/${command.video_id}?${query}`);
			} else if (command.action === 'play' && command.jellyfin_id) {
				window.dispatchEvent(new Event('bt:before-cast'));
				await this.becomeSender(command.profile_id);
				setCastGrant(command.grant ?? null);
				// No start resumes from Jellyfin's saved position; a sender mid-film hands over its own position.
				const query = new URLSearchParams({ cast: command.id });
				if (command.position_s != null) query.set('start', String(Math.floor(command.position_s)));
				await goto(`/jellyfin/play/${command.jellyfin_id}?${query}`);
			} else {
				const video = playingVideo();
				if (command.action === 'stop') { video?.pause(); await goto('/'); }
				else if (command.action === 'pause') video?.pause();
				else if (command.action === 'resume') await video?.play();
				else if (command.action === 'seek' && video && command.position_s != null) {
					const end = Number.isFinite(video.duration) ? video.duration : command.position_s;
					video.currentTime = Math.max(0, Math.min(command.position_s, end));
				}
			}
		} catch { this.error = 'Playback could not start. Press Play on this screen.'; }
	}

	/** Switch to the sending profile when it belongs to this screen's account (true). A shared screen
	 * signed in to another account stays on its own profile (false); Marquee titles then play with the
	 * command's cast grant, YouTube videos simply play here. */
	private async becomeSender(pid: number): Promise<boolean> {
		await session.loadProfiles();
		if (!session.profiles.some(p => p.id === pid)) return false;
		if (!(await session.switchProfile(pid))) throw new Error('Profile was not unlocked');
		return true;
	}

	private async poll() {
		if (this.busy || !session.ready || session.needsLogin) return;
		this.busy = true;
		try {
			const r = this.receiver;
			if (r) {
				const video = playingVideo();
				const match = location.pathname.match(/^\/watch\/([A-Za-z0-9_-]{11})/);
				const jf = location.pathname.match(JF_PLAY);
				const reply = await request<{ command: Command | null; pair_code?: string }>(`/playback/targets/${r.id}/heartbeat`, {
					body: { ack: this.ack, video_id: match?.[1] ?? null, jellyfin_id: jf?.[1] ?? null, paused: video?.paused ?? true,
						position_s: Number.isFinite(video?.currentTime) ? video!.currentTime : 0,
						duration_s: video && Number.isFinite(video.duration) ? video.duration : null, error: this.error },
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
				await this.refresh();
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
