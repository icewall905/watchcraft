<script lang="ts">
	// "Save offline" / "Remove download" toggle (Ticket D cache).
	import { api } from '$lib/api';
	import { toast } from '$lib/stores/toast.svelte';
	import { agent } from '$lib/stores/agent.svelte';
	import { focusable } from '$lib/nav';
	import type { DownloadStatus } from '$lib/types';

	interface Props {
		videoId: string;
		/** finished local copy */
		offline?: boolean;
		/** full status when known (video detail) */
		status?: DownloadStatus | null;
		progress?: number;
		group?: string;
		size?: 'small' | 'normal';
	}
	let { videoId, offline = false, status = null, progress = 0, group, size = 'small' }: Props = $props();
	/** Set by the toggle so the button reacts before the next fetch lands. */
	let local = $state<DownloadStatus | 'none' | null>(null);
	let busy = $state(false);
	// The shared SSE stream already carries the active download, so this shows live progress
	// instead of whatever the page happened to load with.
	const live = $derived(
		agent.downloader.video_id === videoId &&
			(agent.downloader.state === 'downloading' || agent.downloader.state === 'transcoding')
			? agent.downloader
			: null
	);
	const st = $derived(local ?? (live ? 'downloading' : (status ?? (offline ? 'done' : 'none'))));
	const shown = $derived(live?.progress ?? progress);
	const saved = $derived(st === 'done' || st === 'queued' || st === 'downloading');

	async function toggle() {
		if (busy) return;
		busy = true;
		try {
			if (saved) {
				await api.removeDownload(videoId);
				local = 'evicted';
				toast.success('Download removed');
			} else {
				await api.saveOffline(videoId);
				local = 'queued';
				toast.success('Saving offline. It downloads in the background.');
			}
		} finally {
			busy = false;
		}
	}
	const label = $derived(
		st === 'done' ? 'Remove download' : st === 'downloading' ? `Downloading ${Math.round(shown * 100)}%, cancel` : st === 'queued' ? 'Queued, cancel' : 'Save offline'
	);
</script>

<button class="btn off {size}" class:on={st === 'done'} aria-label={label} use:focusable={{ group, onSelect: toggle }}>
	<span aria-hidden="true">{st === 'done' ? '✓' : '⤓'}</span>
	<span>{label}</span>
</button>

<style>
	.off.small {
		min-height: 2.1rem;
		padding: 0.3rem 0.85rem;
		font-size: 0.82rem;
	}
	.off.on {
		border-color: rgba(61, 220, 151, 0.5);
		color: #b9f5d8;
	}
</style>
