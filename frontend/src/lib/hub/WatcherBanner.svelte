<script lang="ts">
	import { session } from '$lib/stores/session.svelte';
	import { focusable } from '$lib/nav';
	import { watcher, type WatchUpdate } from './watcher';

	let updates = $state<WatchUpdate[]>([]);
	const canLoad = () => session.ready && !session.needsLogin && !session.choosing && !!session.profileId && session.grownUp;

	$effect(() => {
		const version = session.version;
		updates = [];
		if (!canLoad()) return;
		const controller = new AbortController();
		let loading = false;
		const refresh = async () => {
			if (document.visibilityState === 'hidden' || loading) return;
			loading = true;
			try {
				const result = await watcher.updates(true, controller.signal);
				if (!controller.signal.aborted && version === session.version && canLoad()) updates = result;
			} catch { /* The edition can still load when Watcher is unavailable. */ }
			finally { loading = false; }
		};
		void refresh();
		const timer = window.setInterval(refresh, 5000);
		document.addEventListener('visibilitychange', refresh);
		return () => {
			controller.abort();
			window.clearInterval(timer);
			document.removeEventListener('visibilitychange', refresh);
		};
	});
</script>

{#if updates.length}
	<div class="watch-banner" role="status">
		<a href={updates[0].watch_id === 0 ? "/settings?tab=watcher-notifications" : "/watcher#watch-updates"} use:focusable>
			<strong>The Watcher · {updates.length === 50 ? '50+' : updates.length} new {updates.length === 1 ? 'update' : 'updates'}</strong>
			<span>{updates[0].text}</span>
		</a>
	</div>
{/if}

<style>
	.watch-banner { margin-bottom: 1rem; border: 1px solid var(--accent, #ffb547); border-radius: 10px; }
	a { display: grid; gap: .25rem; padding: .75rem 1rem; color: inherit; text-decoration: none; }
	span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
