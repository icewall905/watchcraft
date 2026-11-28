<script lang="ts">
	// Click-to-play YouTube: nothing loads from YouTube until asked, then the privacy-enhanced player.
	let { id, title = '' }: { id: string; title?: string } = $props();
	let playing = $state(false);
</script>
<div class="yt">
	{#if playing}
		<iframe src={`https://www.youtube-nocookie.com/embed/${id}?autoplay=1&rel=0`} title={title || 'YouTube video'}
			allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowfullscreen referrerpolicy="strict-origin-when-cross-origin"></iframe>
	{:else}
		<button type="button" class="play" onclick={() => { playing = true; }} aria-label="Play video here">
			<span class="btn">▶</span><span class="lbl">Play YouTube video here</span>
		</button>
	{/if}
</div>
<style>
	.yt{position:relative;aspect-ratio:16/9;border-radius:var(--r-md);overflow:hidden;background:#000}
	iframe{position:absolute;inset:0;width:100%;height:100%;border:0}
	.play{position:absolute;inset:0;display:grid;place-content:center;justify-items:center;gap:.5rem;border:0;cursor:pointer;color:#fff;
		background:radial-gradient(circle at center,rgba(255,0,0,.25),rgba(0,0,0,.85))}
	.btn{display:grid;place-items:center;width:4rem;height:2.8rem;border-radius:.8rem;background:#f00;font-size:1.4rem}
	.lbl{font-size:.85rem;font-weight:600}
</style>
