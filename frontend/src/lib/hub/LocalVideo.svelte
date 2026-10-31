<script lang="ts">
	import { onDestroy } from 'svelte';
	import { currentProfileId } from '$lib/api';
	let { id }: { id: number } = $props();
	let src = $state('');
	let loading = $state(false);
	let error = $state('');
	const controller = new AbortController();
	let objectUrl = '';
	async function load() {
		loading = true; error = '';
		try {
			const headers: Record<string, string> = {};
			const profile = currentProfileId();
			if (profile) headers['X-Profile-Id'] = profile;
			const response = await fetch(`/api/hub/items/${id}/assets/video`, { credentials: 'same-origin', headers, signal: controller.signal });
			if (!response.ok) throw new Error('This video is unavailable. Try again or open the original post.');
			const blob = await response.blob();
			if (!controller.signal.aborted) { objectUrl = URL.createObjectURL(blob); src = objectUrl; }
		} catch (e) { if (!controller.signal.aborted) error = e instanceof Error ? e.message : 'Could not load video.'; }
		finally { loading = false; }
	}
	onDestroy(() => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); });
</script>

<div class="local-video">
	{#if src}
		<!-- svelte-ignore a11y_media_has_caption: source clips do not supply caption tracks -->
		<video {src} controls playsinline autoplay preload="metadata" aria-label="Meme video" onerror={() => (error = 'Your browser could not play this clip. Open the original post to watch it.')}>
			Your browser does not support this video.
		</video>
	{:else}
		<button class="btn primary" disabled={loading} onclick={load}>{loading ? 'Loading video…' : 'Play video'}</button>
	{/if}
	{#if error}<p role="alert">{error}</p>{/if}
</div>

<style>
	.local-video { width: 100%; display: grid; place-items: center; gap: .7rem; padding: .75rem; }
	video { width: 100%; max-height: 75vh; }
	p { color: var(--warning); padding: 0 .5rem; }
</style>
