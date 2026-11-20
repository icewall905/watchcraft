<script lang="ts">
	// An item's picture. Prefers the captured local copy; `remote` falls back to the publisher's
	// image, fetched through the backend so the household's address never reaches them.
	import { profileHeaders } from '$lib/api';
	let { id, remote = false, alt = '', loading = 'lazy', expandable = false, width, onfail }: {
		id: number; expandable?: boolean; width?: number; remote?: boolean; alt?: string; loading?: 'lazy' | 'eager'; onfail?: () => void;
	} = $props();
	let src = $state('');
	$effect(() => {
		const controller = new AbortController();
		let objectUrl = '';
		const headers = profileHeaders();
		// `width` asks for a small pre-made preview the browser may keep; full captures are never cached.
		const endpoint = remote ? `/api/hub/items/${id}/image` : `/api/hub/items/${id}/assets/image${width ? `?w=${width}` : ''}`;
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
{#if src}{#if expandable}<a href={src} target="_blank" rel="noopener" aria-label="Open full-size archived image"><img {src} {alt} {loading} /></a>{:else}<img {src} {alt} {loading} />{/if}{/if}
