<script lang="ts">
	import type { Comment } from '$lib/hub/commonroom';
	import { ago, compact } from './util';
	import CommentTree from './CommentTree.svelte';
	let { comments, depth = 0 }: { comments: Comment[]; depth?: number } = $props();
	let closed = $state<Record<string, boolean>>({});
</script>
<ul class="tree" class:nested={depth > 0}>
	{#each comments as c (c.id)}
		<li>
			<div class="head">
				<button type="button" class="fold" aria-expanded={!closed[c.id]} aria-label={closed[c.id] ? 'Expand comment' : 'Collapse comment'} onclick={() => { closed[c.id] = !closed[c.id]; }}>{closed[c.id] ? '＋' : '−'}</button>
				<strong>{c.author || 'unknown'}</strong>
				{#if c.score != null}<span class="muted">▲ {compact(c.score)}</span>{/if}
				{#if c.published_at}<span class="muted">· {ago(c.published_at)}</span>{/if}
			</div>
			{#if !closed[c.id]}
				<p class="body">{c.body}</p>
				{#if c.children?.length}<CommentTree comments={c.children} depth={depth + 1} />{/if}
			{/if}
		</li>
	{/each}
</ul>
<style>
	.tree{list-style:none;margin:0;padding:0;display:grid;gap:.8rem}
	.nested{margin-top:.5rem;padding-left:.8rem;border-left:2px solid var(--line)}
	.head{display:flex;gap:.5rem;align-items:center;font-size:.8rem;flex-wrap:wrap}
	.muted{color:var(--muted)}
	.fold{width:1.6rem;height:1.6rem;border-radius:50%;border:1px solid var(--line);background:none;color:var(--muted);cursor:pointer;padding:0}
	.body{margin:.3rem 0 0;white-space:pre-wrap;overflow-wrap:anywhere;font-size:.9rem}
</style>
