<script lang="ts">
	import Modal from '$lib/components/Modal.svelte';
	import { crApi, type Post, type PostDetail, type Comment } from '$lib/hub/commonroom';
	import Feedback from './Feedback.svelte';
	import { authSrc } from '$lib/hub/BoardImage.svelte';
	import CommentTree from './CommentTree.svelte';
	import { ago, compact, platformOf } from './util';
	let { post, onClose, onGone }: { post: Post; onClose: () => void; onGone?: (p: Post) => void } = $props();
	let detail = $state<PostDetail | null>(null), comments = $state<Comment[] | null>(null), cUrl = $state(''), err = $state(''), cErr = $state(''), cLoading = $state(false);
	const view = $derived(detail ?? post);
	const plat = $derived(platformOf(post.platform));
	$effect(() => {
		const id = post.post_id; let live = true;
		detail = null; comments = null; cErr = '';
		crApi.post(id).then((d) => { if (live) detail = d; }, (e) => { if (live) err = e instanceof Error ? e.message : 'Could not load post.'; });
		return () => { live = false; };
	});
	async function loadComments() {
		cLoading = true; cErr = '';
		try { const r = await crApi.comments(post.post_id); comments = r.comments; cUrl = r.url; }
		catch (e) { cErr = e instanceof Error ? e.message : 'Could not load comments.'; }
		finally { cLoading = false; }
	}
</script>
<Modal portal title={post.title} subtitle={`${plat.icon} ${plat.label}${post.community ? ' · ' + post.community : ''}${post.author ? ' · ' + post.author : ''}${post.published_at ? ' · ' + ago(post.published_at) : ''}`} size="drawer" {onClose}>
	<div class="pv">
		{#if err}<p class="err" role="alert">{err}</p>{/if}
		{#each post.media as m, i (i)}
			{#if m.kind === 'video'}
				<a class="vid" href={post.link_url || post.url} target="_blank" rel="noopener noreferrer">{#if m.poster}<img use:authSrc={m.poster} alt="" />{/if}<span class="playbtn">▶ Watch at source</span></a>
			{:else}<img class="gal" use:authSrc={m.url} alt="" loading="lazy" />{/if}
		{/each}
		{#if detail}
			{#if detail.body}<div class="text">{detail.body}</div>{:else if post.excerpt}<div class="text">{post.excerpt}</div>{/if}
		{:else if !err}<p class="muted">Loading…</p>{/if}
		{#if post.domain && post.link_url}<a class="out" href={post.link_url} target="_blank" rel="noopener noreferrer">↗ {post.domain}</a>{/if}
		{#if post.why}<p class="why">✨ {post.why}</p>{/if}
		<div class="stats muted small">{#if view.score != null}▲ {compact(view.score)}{/if}{#if view.comments != null}<span>💬 {compact(view.comments)}</span>{/if}</div>
		<Feedback {post} onDown={() => { onGone?.(post); onClose(); }} onHide={() => { onGone?.(post); onClose(); }} />
		<a class="btn" href={post.url} target="_blank" rel="noopener noreferrer">Open original</a>
		<section aria-label="Comments">
			<h3>Comments</h3>
			{#if comments === null}
				<button type="button" class="btn" disabled={cLoading} onclick={loadComments}>{cLoading ? 'Loading…' : 'Load comments'}</button>
				{#if cErr}<p class="err" role="alert">{cErr}</p>{/if}
			{:else if comments.length}<CommentTree {comments} />
			{:else}<p class="muted">No comments yet.{#if cUrl} <a href={cUrl} target="_blank" rel="noopener noreferrer">Open discussion</a>{/if}</p>{/if}
		</section>
	</div>
</Modal>
<style>
	.pv{display:grid;gap:.9rem;min-width:0}
	.gal,.vid img{display:block;width:100%;border-radius:var(--r-sm)}
	.vid{position:relative;display:block;color:#fff;text-decoration:none}
	.playbtn{position:absolute;inset:auto auto .6rem .6rem;padding:.3rem .8rem;border-radius:var(--r-pill);background:rgba(0,0,0,.7)}
	.text{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.5}
	.out{color:var(--info);overflow-wrap:anywhere}
	.why{margin:0;font-size:.8rem;color:var(--faint);font-style:italic}
	.stats{display:flex;gap:.8rem}.muted{color:var(--muted)}.small{font-size:.8rem}
	h3{margin:.4rem 0}
	.err{color:var(--bad);margin:0}
</style>
