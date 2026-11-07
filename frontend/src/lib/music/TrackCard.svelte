<script lang="ts">
	import { focusable } from '$lib/nav';
	import { player } from './player.svelte';
	import { fmtDuration, fmtViews, type Track } from './api';

	interface Props {
		track: Track;
		onPlay: () => void;
		onMore: (t: Track) => void;
		onArtist?: (name: string) => void;
	}
	let { track, onPlay, onMore, onArtist }: Props = $props();
	const playing = $derived(player.current?.video_id === track.video_id);
</script>

<article class="card" class:playing>
	<button class="cover" use:focusable onclick={onPlay} aria-label={`Play ${track.title} by ${track.artist}`}>
		<img src={track.thumbnail} alt="" loading="lazy" />
		<span class="play">{playing ? '' : '▶'}</span>
		{#if playing}<span class="eq" aria-hidden="true"><i></i><i></i><i></i><i></i></span>{/if}
		{#if track.duration_s}<span class="dur">{fmtDuration(track.duration_s)}</span>{/if}
		{#if track.offline}<span class="saved" title="Stored offline">💾</span>{/if}
	</button>
	<div class="info">
		<div class="txt">
			<strong title={track.title}>{track.title}</strong>
			{#if onArtist}
				<button class="artist" onclick={() => onArtist(track.artist)}>{track.artist}{player.isFavArtist(track.artist) ? ' ⭐' : ''}</button>
			{:else}
				<span class="artist">{track.artist}</span>
			{/if}
			{#if track.reason}<small class="reason">{track.reason}</small>{:else if track.views}<small class="reason">{fmtViews(track.views)} views</small>{/if}
		</div>
		<div class="acts">
			<button class="ic" class:on={player.isFav(track)} use:focusable aria-label={player.isFav(track) ? 'Remove from favourites' : 'Add to favourites'} onclick={() => player.toggleFav(track)}>{player.isFav(track) ? '❤️' : '🤍'}</button>
			<button class="ic" use:focusable aria-label="More actions" onclick={() => onMore(track)}>⋯</button>
		</div>
	</div>
</article>

<style>
	.card { display: grid; gap: 0.5rem; min-width: 0; }
	.cover { position: relative; display: block; padding: 0; border: 0; border-radius: var(--r-md); overflow: hidden; aspect-ratio: 16 / 9; background: #000; cursor: pointer; transition: transform 0.18s ease, box-shadow 0.18s ease; }
	.cover img { width: 100%; height: 100%; object-fit: cover; display: block; }
	.cover:hover, .cover:focus-visible { transform: translateY(-3px) scale(1.02); box-shadow: 0 12px 30px -12px #ff4fa3aa; outline: 3px solid var(--accent); outline-offset: 2px; }
	.play { position: absolute; inset: auto 0.5rem 0.5rem auto; width: 2.6rem; height: 2.6rem; display: grid; place-items: center; border-radius: 50%; background: var(--accent); color: var(--accent-ink); font-size: 1rem; opacity: 0; transform: scale(0.8); transition: 0.18s ease; }
	.cover:hover .play, .cover:focus-visible .play { opacity: 1; transform: scale(1); }
	.dur { position: absolute; left: 0.4rem; bottom: 0.4rem; padding: 0.1rem 0.4rem; border-radius: 0.3rem; background: rgba(0, 0, 0, 0.75); color: #fff; font-size: 0.72rem; font-variant-numeric: tabular-nums; }
	.saved { position: absolute; right: 0.4rem; top: 0.4rem; padding: 0.1rem 0.3rem; border-radius: 0.3rem; background: rgba(0, 0, 0, 0.7); font-size: 0.75rem; }
	.playing .cover { outline: 3px solid #ff4fa3; outline-offset: 2px; }
	.eq { position: absolute; inset: 0; display: flex; gap: 4px; align-items: end; justify-content: center; padding-bottom: 30%; background: rgba(0, 0, 0, 0.45); }
	.eq i { width: 6px; height: 30%; background: #ff4fa3; border-radius: 3px; animation: eq 0.9s ease-in-out infinite alternate; }
	.eq i:nth-child(2) { animation-delay: 0.2s; background: var(--accent); }
	.eq i:nth-child(3) { animation-delay: 0.4s; background: #3fb6ff; }
	.eq i:nth-child(4) { animation-delay: 0.1s; background: #2bd67b; }
	.info { display: flex; gap: 0.3rem; align-items: start; justify-content: space-between; }
	.txt { display: grid; min-width: 0; }
	.txt strong { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-size: 0.95rem; }
	.artist { background: none; border: 0; padding: 0; text-align: start; color: var(--muted); font: inherit; font-size: 0.88rem; cursor: pointer; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
	button.artist:hover { color: var(--accent); text-decoration: underline; }
	.reason { color: var(--faint); font-size: 0.75rem; }
	.acts { display: flex; flex-shrink: 0; }
	.ic { background: none; border: 0; font-size: 1.1rem; padding: 0.25rem; border-radius: var(--r-sm); cursor: pointer; color: var(--text); }
	.ic:focus-visible { outline: 2px solid var(--accent); }
	.ic.on { animation: pop 0.35s ease; }
	@keyframes eq { to { height: 75%; } }
	@keyframes pop { 50% { transform: scale(1.4); } }
	@media (prefers-reduced-motion: reduce) { .eq i, .ic.on { animation: none; } }
</style>
