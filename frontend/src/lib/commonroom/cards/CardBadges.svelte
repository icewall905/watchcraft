<script lang="ts">
	import type { Post } from '$lib/hub/commonroom';
	import { BADGES, kindOf } from '../util';
	let { post }: { post: Post } = $props();
	const kind = $derived(kindOf(post.kind || ''));
	const badges = $derived((post.badges ?? []).map((b) => ({ key: b, ...BADGES[b] })).filter((b) => b.label));
</script>
{#if kind || badges.length || post.wildcard}
	<div class="badges">
		{#each badges as b (b.key)}<span class="b {b.key}">{b.icon} {b.label}</span>{/each}
		{#if post.wildcard}<span class="b wild" title="A little off-topic, but your curator thought you'd enjoy it">🃏 Wildcard</span>{/if}
		{#if kind}<span class="b kind">{kind.icon} {kind.label}</span>{/if}
	</div>
{/if}
<style>
	.badges{display:flex;flex-wrap:wrap;gap:.3rem}
	.b{padding:.1rem .5rem;border-radius:var(--r-pill);font-size:.7rem;font-weight:700;border:1px solid var(--line);color:var(--muted);white-space:nowrap}
	.hot{color:#ff8a4c;border-color:#ff8a4c66;background:#ff8a4c1a}
	.rising{color:var(--good, #4ade80);border-color:currentColor}
	.fresh{color:var(--info)}
	.wild{color:var(--ai);border-color:var(--ai);background:rgba(157,134,255,.12)}
	.kind{font-weight:600}
</style>
