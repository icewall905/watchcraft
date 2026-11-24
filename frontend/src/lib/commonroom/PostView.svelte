<script lang="ts">
	import Modal from '$lib/components/Modal.svelte';
	import { crApi, type Post, type PostDetail, type Comment } from '$lib/hub/commonroom';
	import Feedback from './Feedback.svelte';
	import { authSrc } from '$lib/hub/BoardImage.svelte';
	import CommentTree from './CommentTree.svelte';
	import { ago, compact, platformOf } from './util';
	let { post, onClose, onGone }: { post: Post; onClose: () => void; onGone?: (p: Post) => void } = $props();
	let detail = $state<PostDetail | null>(null), comments = $state<Comment[] | null>(null), cUrl = $state(''), err = $state(''), cErr = $state(''), cLoading = $state(false);
	let fetchedAt = $state(0), supported = $state(true), sort = $state<'best' | 'new'>('best');
	const sorted = $derived.by(() => {
		if (!comments) return [];
		const key = sort === 'new' ? (c: Comment) => c.published_at ?? 0 : (c: Comment) => c.score ?? 0;
		return [...comments].sort((a, b) => key(b) - key(a));
	});
	const count = (list: Comment[]): number => list.reduce((n, c) => n + 1 + count(c.children ?? []), 0);
	const view = $derived(detail ?? post);
	const plat = $derived(platformOf(post.platform));
	$effect(() => {
		const id = post.post_id; let live = true;
		detail = null; comments = null; cErr = '';
		crApi.post(id).then((d) => { if (live) detail = d; }, (e) => { if (live) err = e instanceof Error ? e.message : 'Could not load post.'; });
		loadComments(id, false, () => live);
		return () => { live = false; };
	});
	async function loadComments(id = post.post_id, refresh = false, alive: () => boolean = () => true) {
		cLoading = true; cErr = '';
		try {
			const r = await crApi.comments(id, refresh);
			if (!alive()) return;
			comments = r.comments; cUrl = r.url; fetchedAt = r.fetched_at ?? 0; supported = r.supported ?? true;
		} catch (e) { if (alive()) cErr = e instanceof Error ? e.message : 'Could not load comments.'; }
		finally { if (alive()) cLoading = false; }
	}
</script>
<Modal portal title={post.title} subtitle={`${plat.icon} ${plat.label}${post.community ? ' · ' + post.community : ''}${post.author ? ' · ' + post.author : ''}${post.published_at ? ' · ' + ago(post.published_at) : ''}`} size="drawer" {onClose}>
	<div class="pv">
		{#if err}<p class="err" role="alert">{err}</p>{/if}
		{#each post.media as m, i (i)}
			{#if m.kind === 'video'}
				<a class="vid" href={post.link_url || post.url} target="_blank" rel="noopener noreferrer">{#if m.poster}<img use:authSrc={m.poster} alt="" onerror={(e) => { (e.currentTarget as HTMLElement).hidden = true; }} />{/if}<span class="playbtn">▶ Watch at source</span></a>
			{:else}<img class="gal" use:authSrc={m.url} alt="" loading="lazy" onerror={(e) => { (e.currentTarget as HTMLElement).hidden = true; }} />{/if}
		{/each}
		{#if detail}
			{#if detail.body}<div class="text">{detail.body}</div>{:else if post.excerpt}<div class="text">{post.excerpt}</div>{/if}
		{:else if !err}<p class="muted">Loading…</p>{/if}
		{#if post.domain && post.link_url}<a class="out" href={post.link_url} target="_blank" rel="noopener noreferrer">↗ {post.domain}</a>{/if}
		{#if post.why}<p class="why">✨ {post.why}</p>{/if}
		<div class="stats muted small">{#if view.score != null}▲ {compact(view.score)}{/if}{#if view.comments != null}<span>💬 {compact(view.comments)}</span>{/if}</div>
		<Feedback {post} onDown={() => { onGone?.(post); onClose(); }} onHide={() => { onGone?.(post); onClose(); }} />
		<a class="btn" href={post.url} target="_blank" rel="noopener noreferrer">Open original</a>
		<section aria-label="Comments" class="talk">
			<div class="thead">
				<h3>Comments{#if comments?.length} <span class="muted small">{compact(Math.max(count(comments), view.comments ?? 0))}</span>{/if}</h3>
				{#if comments && comments.length > 1}
					<div class="sort" role="group" aria-label="Sort comments">
						<button type="button" aria-pressed={sort === 'best'} onclick={() => { sort = 'best'; }}>Best</button>
						<button type="button" aria-pressed={sort === 'new'} onclick={() => { sort = 'new'; }}>New</button>
					</div>
				{/if}
			</div>
			{#if comments === null}
				{#if cErr}<p class="err" role="alert">{cErr} <button type="button" class="link" onclick={() => loadComments()}>Try again</button></p>
				{:else}<p class="muted">Loading comments…</p>{/if}
			{:else if !supported}
				<p class="muted">This source has no comments to show here. <a href={post.url} target="_blank" rel="noopener noreferrer">Open original</a></p>
			{:else}
				{#if comments.length}<CommentTree comments={sorted} op={post.author} />
				{:else}<p class="muted">No comments yet.</p>{/if}
				<p class="muted small fresh">
					{#if fetchedAt}Updated {ago(fetchedAt)} · {/if}
					<button type="button" class="link" disabled={cLoading} onclick={() => loadComments(post.post_id, true)}>{cLoading ? 'Refreshing…' : '↻ Refresh'}</button>
					{#if cUrl} · <a href={cUrl} target="_blank" rel="noopener noreferrer">Join the discussion</a>{/if}
				</p>
			{/if}
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
	.thead{display:flex;align-items:center;justify-content:space-between;gap:.6rem;flex-wrap:wrap}
	.sort{display:flex;border:1px solid var(--line);border-radius:var(--r-pill);overflow:hidden}
	.sort button{background:none;border:0;color:var(--muted);padding:.25rem .8rem;cursor:pointer;font:inherit;font-size:.8rem}
	.sort button[aria-pressed='true']{background:var(--line);color:var(--text)}
	.link{background:none;border:0;padding:0;color:var(--info);cursor:pointer;font:inherit}
	.fresh{margin:.6rem 0 0}
	.err{color:var(--bad);margin:0}
</style>
