<script lang="ts">
	// An item's picture. Prefers the captured local copy; `remote` falls back to the publisher's
	// image, fetched through the backend so the household's address never reaches them.
	import { currentProfileId } from '$lib/api';
	let { id, remote = false, alt = '', loading = 'lazy', onfail }: {
		id: number; remote?: boolean; alt?: string; loading?: 'lazy' | 'eager'; onfail?: () => void;
	} = $props();
	let src = $state('');
	$effect(() => {
		const controller = new AbortController();
		let objectUrl = '';
		const headers: Record<string, string> = {};
		const profile = currentProfileId();
		if (profile) headers['X-Profile-Id'] = profile;
		const endpoint = remote ? `/api/hub/items/${id}/image` : `/api/hub/items/${id}/assets/image`;
		fetch(endpoint, { credentials: 'same-origin', headers, signal: controller.signal })
			.then((response) => response.ok ? response.blob() : null)
			.then((blob) => {
				if (controller.signal.aborted) return;
				if (blob) { objectUrl = URL.createObjectURL(blob); src = objectUrl; } else onfail?.();
			})
			.catch(() => { if (!controller.signal.aborted) onfail?.(); });
		return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); src = ''; };
	});
</script>
{#if src}<img {src} {alt} {loading} />{/if}
