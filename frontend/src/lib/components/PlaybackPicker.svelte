<script lang="ts">
	import { playback } from '$lib/stores/playback.svelte';
	import { focusable } from '$lib/nav';
	import Modal from './Modal.svelte';
	let { floating = false }: { floating?: boolean } = $props();
	let name = $state('Media center');
	let code = $state('');
	let working = $state(false);
	async function register() {
		if (!name.trim() || working) return;
		working = true;
		try { await playback.register(name.trim()); } catch { /* API displays the error */ }
		finally { working = false; }
	}
	async function unregister() {
		working = true;
		try { await playback.unregister(); } catch { /* API displays the error */ }
		finally { working = false; }
	}
	async function forget() {
		working = true;
		try { await playback.forgetSelected(); } catch { /* API displays the error */ }
		finally { working = false; }
	}
	// Remote progress: the screen reports position every heartbeat; a drag holds its value until the screen catches up.
	let scrub = $state<number | null>(null);
	let scrubTimer: ReturnType<typeof setTimeout> | undefined;
	const remote = $derived(playback.selected?.state);
	const duration = $derived(remote?.video_id && remote.duration_s ? remote.duration_s : 0);
	const position = $derived(scrub ?? Math.min(remote?.position_s ?? 0, duration));
	const controllable = $derived(!!playback.selected?.online && !playback.needsPair);
	const clock = (s: number) => {
		const t = Math.max(0, Math.floor(s)), h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), sec = String(t % 60).padStart(2, '0');
		return h ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`;
	};
	function seek(to: number) {
		if (!duration) return;
		scrub = Math.max(0, Math.min(to, duration));
		void playback.send('seek', undefined, null, scrub);
		clearTimeout(scrubTimer);
		scrubTimer = setTimeout(() => (scrub = null), 4000);
	}
	async function pair() {
		if (!code.trim()) return;
		await playback.pair(code.trim());
		code = '';
	}
</script>

<button class="btn cast" class:floating class:active={!!playback.selectedId} aria-label="Play on" title="Choose a playback screen"
	use:focusable={{ group: 'topbar', id: 'play-on', onSelect: () => (playback.open = true) }}>
	<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M3 8V4h18v16h-9M3 20h.01M3 15a5 5 0 0 1 5 5M3 10a10 10 0 0 1 10 10" /></svg>
	<span>{playback.selected?.name ?? 'Play on'}</span>
</button>

{#if playback.open}
	<Modal title="Play on" subtitle="Choose where videos play when you tap them." onClose={() => (playback.open = false)} size="sm">
		<div class="choices">
			<button class="btn" class:primary={!playback.selectedId} use:focusable={{ group: 'cast', autofocus: true, onSelect: () => { playback.select(null); playback.open = false; } }}>This device</button>
			{#each playback.targets.filter(t => t.id !== playback.receiver?.id) as target (target.id)}
				<button class="btn target" class:primary={target.id === playback.selectedId} disabled={!target.online}
					use:focusable={{ group: 'cast', onSelect: () => { playback.select(target.id); if (target.paired) playback.open = false; } }}>
					<span>{target.name}</span><small>{target.online ? (playback.needsPair === target.id ? 'Needs pairing' : 'Online') : 'Offline'}</small>
				</button>
			{/each}
			{#if !playback.targets.some(t => t.online && t.id !== playback.receiver?.id)}
				<p class="muted small">To add a screen, open WatchCraft on it and register it below. Keep that browser or PWA open.</p>
			{/if}
		</div>
		{#if playback.selectedId}
			<section>
				<h3>{playback.selected?.name ?? 'Selected screen'}</h3>
				<p class="muted small">{playback.selected?.online ? (playback.needsPair ? 'Enter the pairing code shown on that screen to send from this profile.' : 'Tap any video to play it on this screen.') : 'Screen offline. Open WatchCraft on it to reconnect.'}</p>
				{#if playback.needsPair === playback.selectedId}
					<form class="pair-row" onsubmit={(e) => { e.preventDefault(); void pair(); }}>
						<label for="pair-code">Pairing code</label>
						<input id="pair-code" maxlength="12" inputmode="numeric" bind:value={code}
							placeholder="6-digit code" use:focusable={{ group: 'cast' }} />
						<button class="btn" disabled={!code.trim() || playback.pairing} use:focusable={{ group: 'cast' }}>Pair</button>
					</form>
				{/if}
				{#if playback.selected?.state.error}<p role="status">{playback.selected.state.error}</p>{/if}
				{#if duration}
					<div class="progress">
						<input type="range" aria-label="Playback position" min="0" max={Math.ceil(duration)} step="1" value={position}
							disabled={!controllable}
							oninput={(e) => { scrub = Number(e.currentTarget.value); clearTimeout(scrubTimer); }}
							onchange={(e) => seek(Number(e.currentTarget.value))} />
						<span class="small muted time">{clock(position)} / {clock(duration)}</span>
					</div>
				{/if}
				<div class="hstack">
					<button class="btn" disabled={!controllable || !duration} aria-label="Back 10 seconds" use:focusable={{ group: 'cast', onSelect: () => seek(position - 10) }}>−10s</button>
					<button class="btn" disabled={!playback.selected?.online || !!playback.needsPair} use:focusable={{ group: 'cast', onSelect: () => playback.send('pause') }}>Pause</button>
					<button class="btn" disabled={!playback.selected?.online || !!playback.needsPair} use:focusable={{ group: 'cast', onSelect: () => playback.send('resume') }}>Resume</button>
					<button class="btn" disabled={!playback.selected?.online || !!playback.needsPair} use:focusable={{ group: 'cast', onSelect: () => playback.send('stop') }}>Stop</button>
					<button class="btn" disabled={!controllable || !duration} aria-label="Forward 10 seconds" use:focusable={{ group: 'cast', onSelect: () => seek(position + 10) }}>+10s</button>
				</div>
				{#if playback.selected?.paired}
					<button class="btn" disabled={working} use:focusable={{ group: 'cast', onSelect: forget }}>Forget this screen</button>
				{/if}
			</section>
		{/if}
		<section>
			{#if playback.receiver}
				<h3>This screen: {playback.receiver.name}</h3>
				<p class="muted small">Ready to receive videos. Registration stays enabled after reloading.</p>
				{#if playback.receiver.pair_code}<p>Pairing code to enter on senders: <b>{playback.receiver.pair_code}</b></p>{/if}
				<button class="btn" disabled={working} use:focusable={{ group: 'cast', onSelect: unregister }}>Unregister this screen</button>
			{:else}
				<h3>Use this device as a screen</h3>
				<label for="screen-name">Screen name</label>
				<input id="screen-name" maxlength="60" bind:value={name} placeholder="Media center" use:focusable={{ group: 'cast' }} />
				<button class="btn" disabled={working || !name.trim()} use:focusable={{ group: 'cast', onSelect: register }}>Register this screen</button>
			{/if}
			{#if playback.error}<p role="status">{playback.error}</p>{/if}
			<p class="muted small">If the browser asks, allow sound and autoplay for WatchCraft. Household profiles that entered this screen's pairing code can send videos to it.</p>
		</section>
		<button class="btn" use:focusable={{ group: 'cast', onSelect: () => (playback.open = false) }}>Done</button>
	</Modal>
{/if}

<style>
	.cast { white-space: nowrap; min-width: 2.6rem; padding: 0.5rem 0.7rem; }
	.cast.active { border-color: var(--accent); color: var(--accent); }
	.floating { position: fixed; top: 1rem; right: 1rem; z-index: 110; background: var(--bg-2); opacity: 0.8; }
	.choices { display: grid; gap: 0.6rem; }
	.target { justify-content: space-between; }
	section { display: grid; gap: 0.7rem; border-top: 1px solid var(--line); margin: 1rem 0; padding-top: 1rem; }
	.progress { display: grid; grid-template-columns: 1fr auto; gap: 0.6rem; align-items: center; }
	.progress input { width: 100%; accent-color: var(--accent); }
	.time { font-variant-numeric: tabular-nums; }
	.hstack { flex-wrap: wrap; }
	.pair-row { display: flex; gap: 0.6rem; align-items: end; }
	label { font-size: 0.9rem; }
	@media (max-width: 1300px) { .cast span { display: none; } }
</style>
