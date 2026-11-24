<script lang="ts">
	// A post's saved video: on a card it plays silently in a loop while on screen (like a gif), in the post view
	// with controls and sound available.
	import type { Media } from '$lib/hub/commonroom';
	let { m, href = '', full = false, onOpen }: { m: Media; href?: string; full?: boolean; onOpen?: () => void } = $props();
	let el = $state<HTMLVideoElement>(), failed = $state(false);
	const ratio = $derived(m.w && m.h ? `${m.w} / ${m.h}` : '');
	$effect(() => {
		const v = el;
		if (!v || full || typeof IntersectionObserver === 'undefined') return;
		if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
		const io = new IntersectionObserver(([e]) => { if (e.isIntersecting) void v.play().catch(() => {}); else v.pause(); }, { threshold: 0.5 });
		io.observe(v);
		return () => io.disconnect();
	});
</script>
{#if !failed}
	<!-- svelte-ignore a11y_media_has_caption: source clips carry no caption tracks -->
	<video bind:this={el} class="iv" class:full src={m.src} poster={m.poster || undefined} style:aspect-ratio={ratio}
		muted={!full} loop playsinline controls={full} autoplay={full} preload={full ? 'auto' : 'metadata'}
		aria-label="Post video" onerror={() => { failed = true; }} onclick={() => { if (!full) onOpen?.(); }}></video>
	{#if !full}<span class="gif" aria-hidden="true">▶ video</span>{/if}
{:else}
	<!-- The saved copy would not play here: the poster and a way to the original instead. -->
	<a class="fallback" {href} target="_blank" rel="noopener noreferrer">
		{#if m.poster}<img src={m.poster} alt="" />{/if}<span class="gif">▶ Watch at source</span>
	</a>
{/if}
<style>
	.iv{display:block;width:100%;max-height:28rem;object-fit:cover;background:#000;cursor:pointer}
	.iv.full{max-height:75vh;object-fit:contain;cursor:auto}
	.fallback{position:relative;display:block;min-height:6rem;background:#000}
	.fallback img{display:block;width:100%}
	.gif{position:absolute;left:.5rem;bottom:.5rem;padding:.1rem .45rem;border-radius:var(--r-pill);background:rgba(0,0,0,.65);color:#fff;font-size:.7rem;font-weight:700;pointer-events:none}
</style>
