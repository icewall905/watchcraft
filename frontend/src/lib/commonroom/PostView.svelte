<script lang="ts">
	import Modal from '$lib/components/Modal.svelte';
	import { crApi, type Post, type PostDetail, type Comment } from '$lib/hub/commonroom';
	import Feedback from './Feedback.svelte';
	import { authSrc } from '$lib/hub/BoardImage.svelte';
	import CommentTree from './CommentTree.svelte';
	import CardBadges from './cards/CardBadges.svelte';
	import ElsewhereChips from './cards/ElsewhereChips.svelte';
	import InlineVideo from './cards/InlineVideo.svelte';
	import { ago, compact, platformOf } from './util';
	let { post, onClose, onGone }: { post: Post; onClose: () => void; onGone?: (p: Post) => void } = $props();
	let detail = $state<PostDetail | null>(null), comments = $state<Comment[] | null>(null), cUrl = $state(''), err = $state(''), cErr = $state(''), cLoading = $state(false);
	let fetchedAt = $state(0), supported = $state(true), discussion = $state(''), sort = $state<'best' | 'new'>('best');
	let shown = $state(0), broken = $state<Record<number, boolean>>({});
	const view = $derived(detail ?? post);
	const plat = $derived(platformOf(post.platform));
	const pics = $derived(post.media.filter((_, i) => !broken[i]));
	const sorted = $derived.by(() => {
		if (!comments) return [];
		const key = sort === 'new' ? (c: Comment) => c.published_at ?? 0 : (c: Comment) => c.score ?? 0;
		return [...comments].sort((a, b) => key(b) - key(a));
	});
	const count = (list: Comment[]): number => list.reduce((n, c) => n + 1 + count(c.children ?? []), 0);
	const where = $derived(discussion ? new URL(discussion).hostname.replace(/^www\./, '') : '');
	$effect(() => {
		const id = post.post_id; let live = true;
		detail = null; comments = null; cErr = ''; shown = 0; broken = {};
		crApi.post(id).then((d) => { if (live) detail = d; }, (e) => { if (live) err = e instanceof Error ? e.message : 'Could not load post.'; });
		loadComments(id, false, () => live);
		return () => { live = false; };
	});
	async function loadComments(id = post.post_id, refresh = false, alive: () => boolean = () => true) {
		cLoading = true; cErr = '';
		try {
			const r = await crApi.comments(id, refresh);
			if (!alive()) return;
			comments = r.comments; cUrl = r.url; fetchedAt = r.fetched_at ?? 0; supported = r.supported ?? true; discussion = r.discussion ?? '';
		} catch (e) { if (alive()) cErr = e instanceof Error ? e.message : 'Could not load comments.'; }
		finally { if (alive()) cLoading = false; }
	}
</script>
<Modal portal title={post.title} size="drawer" {onClose}>
	<div class="pv">
		<div class="byline">
			<span class="plat">{plat.icon} {plat.label}</span>
			{#if post.community}<span class="comm">{post.community}</span>{/if}
			{#if post.author}<span class="who"><span class="av" aria-hidden="true">{post.author.slice(0, 1).toUpperCase()}</span>{post.author}</span>{/if}
			{#if post.published_at}<span class="muted">{ago(post.published_at)} ago</span>{/if}
		</div>
		<CardBadges {post} />
		{#if err}<p class="err" role="alert">{err}</p>{/if}

		{#if pics.length}
			<div class="gallery" class:multi={pics.length > 1}>
				{#if pics.length > 1}<span class="count">{shown + 1} / {pics.length}</span>{/if}
				{#each post.media as m, i (i)}
					{#if !broken[i] && (pics.length === 1 || post.media.indexOf(pics[shown]) === i)}
						{#if m.kind === 'video' && m.src}
							<div class="frame"><InlineVideo {m} href={post.link_url || post.url} full /></div>
						{:else if m.kind === 'video'}
							<a class="frame vid" href={post.link_url || post.url} target="_blank" rel="noopener noreferrer">
								{#if m.poster}<img use:authSrc={m.poster} alt="" onerror={() => { broken[i] = true; }} />{/if}<span class="playbtn">▶ Watch at source</span>
							</a>
						{:else}
							<a class="frame" href={m.url.startsWith('https://') ? m.url : post.url} target="_blank" rel="noopener noreferrer" aria-label="Open picture full size">
								<img use:authSrc={m.url} alt="" onerror={() => { broken[i] = true; }} />
							</a>
						{/if}
					{/if}
				{/each}
				{#if pics.length > 1}
					<div class="pager">
						<button type="button" aria-label="Previous picture" disabled={shown === 0} onclick={() => { shown -= 1; }}>‹</button>
						<button type="button" aria-label="Next picture" disabled={shown >= pics.length - 1} onclick={() => { shown += 1; }}>›</button>
					</div>
				{/if}
			</div>
		{/if}

		{#if detail}
			{#if detail.body}<div class="text">{detail.body}</div>{:else if post.excerpt}<div class="text">{post.excerpt}</div>{/if}
		{:else if !err}<div class="skeleton" aria-label="Loading post"><span></span><span></span><span></span></div>{/if}

		{#if post.domain && post.link_url}
			<a class="linkcard" href={post.link_url} target="_blank" rel="noopener noreferrer">
				<span class="lc-k">Linked article</span><span class="lc-d">{post.domain}</span><span class="lc-go" aria-hidden="true">↗</span>
			</a>
		{/if}
		{#if post.why}<p class="why"><span aria-hidden="true">✨</span> {post.why}</p>{/if}
		<ElsewhereChips list={post.elsewhere ?? []} />

		<div class="bar">
			<div class="pills">
				{#if view.score != null}<span class="pill">▲ {compact(view.score)}</span>{/if}
				{#if view.comments != null}<span class="pill">💬 {compact(view.comments)}</span>{/if}
			</div>
			<Feedback {post} onDown={() => { onGone?.(post); onClose(); }} onHide={() => { onGone?.(post); onClose(); }} />
		</div>
		<div class="actions">
			<a class="go" href={post.url} target="_blank" rel="noopener noreferrer">Open original ↗</a>
			{#if discussion}<a class="go ghost" href={discussion} target="_blank" rel="noopener noreferrer">Discussion on {where} ↗</a>{/if}
		</div>

		<section aria-label="Comments" class="talk">
			<div class="thead">
				<h3>Comments{#if comments?.length} <span class="n">{compact(Math.max(count(comments), view.comments ?? 0))}</span>{/if}</h3>
				{#if comments && comments.length > 1}
					<div class="sort" role="group" aria-label="Sort comments">
						<button type="button" aria-pressed={sort === 'best'} onclick={() => { sort = 'best'; }}>Best</button>
						<button type="button" aria-pressed={sort === 'new'} onclick={() => { sort = 'new'; }}>New</button>
					</div>
				{/if}
			</div>
			{#if comments === null}
				{#if cErr}<p class="err" role="alert">{cErr} <button type="button" class="link" onclick={() => loadComments()}>Try again</button></p>
				{:else}<div class="skeleton" aria-label="Loading comments"><span></span><span></span></div>{/if}
			{:else if !supported}
				<div class="empty">
					{#if discussion}
						<p><strong>The conversation is on {where}.</strong></p>
						<p class="muted small">Comments from {where} show here once Reddit keys are added in <a href="/settings?tab=commonroom">Settings → Common Room</a>.</p>
						<a class="go" href={discussion} target="_blank" rel="noopener noreferrer">Read the thread ↗</a>
					{:else}
						<p class="muted">This source has no comments to show here.</p>
					{/if}
				</div>
			{:else}
				{#if comments.length}<CommentTree comments={sorted} op={post.author} />
				{:else}<div class="empty"><p class="muted">No comments yet. Be the first on {plat.label}.</p></div>{/if}
				<p class="muted small fresh">
					{#if fetchedAt}Updated {ago(fetchedAt)} ago · {/if}
					<button type="button" class="link" disabled={cLoading} onclick={() => loadComments(post.post_id, true)}>{cLoading ? 'Refreshing…' : '↻ Refresh'}</button>
					{#if cUrl} · <a href={cUrl} target="_blank" rel="noopener noreferrer">Join the discussion</a>{/if}
				</p>
			{/if}
		</section>
	</div>
</Modal>
<style>
	.pv{display:grid;gap:1rem;min-width:0}
	.byline{display:flex;flex-wrap:wrap;align-items:center;gap:.45rem .7rem;font-size:.85rem;margin-top:-.4rem}
	.plat{padding:.15rem .6rem;border-radius:var(--r-pill);background:var(--surface-2);font-weight:600}
	.comm{color:var(--info);font-weight:600;overflow-wrap:anywhere}
	.who{display:inline-flex;align-items:center;gap:.35rem;color:var(--text)}
	.av{display:inline-grid;place-items:center;width:1.4rem;height:1.4rem;border-radius:50%;background:var(--ai);color:var(--bg);font-size:.7rem;font-weight:800}
	.muted{color:var(--muted)}.small{font-size:.8rem}
	.gallery{position:relative;border-radius:var(--r-md);overflow:hidden;background:rgba(0,0,0,.35);box-shadow:0 10px 30px rgba(0,0,0,.35)}
	.frame{display:grid;place-items:center;min-height:8rem;color:#fff;text-decoration:none}
	.frame img{display:block;max-width:100%;max-height:70vh;object-fit:contain}
	.vid{position:relative}
	.playbtn{position:absolute;left:.7rem;bottom:.7rem;padding:.35rem .9rem;border-radius:var(--r-pill);background:rgba(0,0,0,.75);font-weight:600}
	.count{position:absolute;top:.6rem;right:.6rem;z-index:1;padding:.15rem .55rem;border-radius:var(--r-pill);background:rgba(0,0,0,.65);color:#fff;font-size:.75rem}
	.pager{position:absolute;inset:auto 0 .6rem 0;display:flex;justify-content:center;gap:.5rem}
	.pager button{width:2.2rem;height:2.2rem;border-radius:50%;border:0;background:rgba(0,0,0,.65);color:#fff;font-size:1.3rem;cursor:pointer}
	.pager button:disabled{opacity:.35;cursor:default}
	.text{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.6;font-size:1rem;max-width:68ch}
	.linkcard{display:grid;grid-template-columns:1fr auto;gap:0 .8rem;padding:.8rem 1rem;border-radius:var(--r-md);background:var(--surface-2);border:1px solid var(--line);text-decoration:none;color:var(--text)}
	.linkcard:hover{border-color:var(--info)}
	.lc-k{font-size:.7rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
	.lc-d{grid-row:2;font-weight:600;color:var(--info);overflow-wrap:anywhere}
	.lc-go{grid-row:1/3;grid-column:2;align-self:center;font-size:1.2rem;color:var(--info)}
	.why{margin:0;padding:.6rem .9rem;border-left:3px solid var(--ai);border-radius:0 var(--r-sm) var(--r-sm) 0;background:rgba(157,134,255,.08);font-size:.85rem;color:var(--muted)}
	.bar{display:flex;align-items:center;justify-content:space-between;gap:.8rem;flex-wrap:wrap}
	.pills{display:flex;gap:.4rem}
	.pill{padding:.25rem .7rem;border-radius:var(--r-pill);background:var(--surface-2);font-size:.85rem;font-weight:600}
	.actions{display:flex;gap:.5rem;flex-wrap:wrap}
	.go{display:inline-flex;align-items:center;padding:.5rem 1rem;border-radius:var(--r-pill);background:var(--info);color:var(--bg);font-weight:700;font-size:.85rem;text-decoration:none}
	.go.ghost{background:none;color:var(--info);border:1px solid var(--info)}
	.talk{display:grid;gap:.8rem;padding-top:1rem;border-top:1px solid var(--line)}
	h3{margin:0;font-size:1.1rem}
	.n{font-size:.8rem;font-weight:600;color:var(--muted);padding:.05rem .5rem;border-radius:var(--r-pill);background:var(--surface-2);vertical-align:middle}
	.thead{display:flex;align-items:center;justify-content:space-between;gap:.6rem;flex-wrap:wrap}
	.sort{display:flex;border:1px solid var(--line);border-radius:var(--r-pill);overflow:hidden}
	.sort button{background:none;border:0;color:var(--muted);padding:.25rem .8rem;cursor:pointer;font:inherit;font-size:.8rem}
	.sort button[aria-pressed='true']{background:var(--surface-hi);color:var(--text)}
	.empty{display:grid;gap:.5rem;justify-items:start;padding:1rem;border-radius:var(--r-md);border:1px dashed var(--line)}
	.empty p{margin:0}
	.link{background:none;border:0;padding:0;color:var(--info);cursor:pointer;font:inherit}
	.fresh{margin:0}
	.err{color:var(--bad);margin:0}
	.skeleton{display:grid;gap:.5rem}
	.skeleton span{height:.8rem;border-radius:var(--r-pill);background:linear-gradient(90deg,var(--surface-2),var(--surface-hi),var(--surface-2));background-size:200% 100%;animation:shine 1.2s linear infinite}
	.skeleton span:nth-child(2){width:85%}.skeleton span:nth-child(3){width:60%}
	@keyframes shine{to{background-position:-200% 0}}
	@media (prefers-reduced-motion:reduce){.skeleton span{animation:none}}
</style>
