<script lang="ts">
	import type { Comment } from '$lib/hub/commonroom';
	import { ago, compact } from './util';
	import CommentTree from './CommentTree.svelte';
	// Long threads stay readable: a first handful of comments per level, deep replies folded until asked for.
	let { comments, depth = 0, op = '' }: { comments: Comment[]; depth?: number; op?: string } = $props();
	const FIRST = 10, FIRST_REPLIES = 4, FOLD_DEPTH = 2;
	let closed = $state<Record<string, boolean>>({}), opened = $state<Record<string, boolean>>({}), more = $state(0);
	const limit = $derived((depth ? FIRST_REPLIES : FIRST) + more);
	const shown = $derived(comments.slice(0, limit));
	const replies = (c: Comment): number => (c.children ?? []).reduce((n, x) => n + 1 + replies(x), 0);
	const isOp = (c: Comment) => !!op && c.author.toLowerCase() === op.toLowerCase();
</script>
<ul class="tree" class:nested={depth > 0}>
	{#each shown as c (c.id)}
		<li>
			<div class="head">
				<button type="button" class="fold" aria-expanded={!closed[c.id]} aria-label={closed[c.id] ? 'Expand comment' : 'Collapse comment'} onclick={() => { closed[c.id] = !closed[c.id]; }}>{closed[c.id] ? '＋' : '−'}</button>
				<strong>{c.author || 'unknown'}</strong>
				{#if isOp(c)}<span class="op">OP</span>{/if}
				{#if c.score != null}<span class="muted">▲ {compact(c.score)}</span>{/if}
				{#if c.published_at}<span class="muted">· {ago(c.published_at)}</span>{/if}
				{#if closed[c.id] && replies(c)}<span class="muted">· {replies(c)} hidden</span>{/if}
			</div>
			{#if !closed[c.id]}
				<p class="body">{c.body}</p>
				{#if c.children?.length}
					{#if depth + 1 >= FOLD_DEPTH && !opened[c.id]}
						<button type="button" class="more" onclick={() => { opened[c.id] = true; }}>Show {replies(c)} {replies(c) === 1 ? 'reply' : 'replies'}</button>
					{:else}<CommentTree comments={c.children} depth={depth + 1} {op} />{/if}
				{/if}
			{/if}
		</li>
	{/each}
	{#if comments.length > shown.length}
		<li><button type="button" class="more" onclick={() => { more += depth ? 10 : 20; }}>Show {Math.min(comments.length - shown.length, depth ? 10 : 20)} more {depth ? 'replies' : 'comments'}</button></li>
	{/if}
</ul>
<style>
	.tree{list-style:none;margin:0;padding:0;display:grid;gap:.8rem}
	.nested{margin-top:.5rem;padding-left:.8rem;border-left:2px solid var(--line)}
	.head{display:flex;gap:.5rem;align-items:center;font-size:.8rem;flex-wrap:wrap}
	.muted{color:var(--muted)}
	.op{font-size:.7rem;font-weight:600;padding:0 .4rem;border-radius:var(--r-pill);background:var(--info);color:var(--bg)}
	.fold{width:1.6rem;height:1.6rem;border-radius:50%;border:1px solid var(--line);background:none;color:var(--muted);cursor:pointer;padding:0}
	.body{margin:.3rem 0 0;white-space:pre-wrap;overflow-wrap:anywhere;font-size:.9rem;line-height:1.45}
	.more{justify-self:start;background:none;border:0;padding:.2rem 0;color:var(--info);cursor:pointer;font:inherit;font-size:.8rem}
</style>
