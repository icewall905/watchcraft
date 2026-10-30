<script lang="ts">
	import { currentProfileId } from '$lib/api';
	let { id, alt = '', loading = 'lazy' }: { id: number; alt?: string; loading?: 'lazy' | 'eager' } = $props();
	let src = $state('');
	$effect(() => {
		const controller = new AbortController();
		let objectUrl = '';
		const headers: Record<string, string> = {};
		const profile = currentProfileId();
		if (profile) headers['X-Profile-Id'] = profile;
		fetch(`/api/hub/items/${id}/assets/image`, { credentials: 'same-origin', headers, signal: controller.signal })
			.then((response) => response.ok ? response.blob() : null)
			.then((blob) => { if (blob && !controller.signal.aborted) { objectUrl = URL.createObjectURL(blob); src = objectUrl; } })
			.catch(() => {});
		return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); src = ''; };
	});
</script>
{#if src}<img {src} {alt} {loading} />{/if}
