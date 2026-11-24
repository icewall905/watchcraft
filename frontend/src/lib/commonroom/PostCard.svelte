<script lang="ts">
	import type { Post } from '$lib/hub/commonroom';
	import { crApi } from '$lib/hub/commonroom';
	import Feedback from './Feedback.svelte';
	import { authSrc } from '$lib/hub/BoardImage.svelte';
	import Reasons from './Reasons.svelte';
	import TopReplies from './cards/TopReplies.svelte';
	import ElsewhereChips from './cards/ElsewhereChips.svelte';
	import CardBadges from './cards/CardBadges.svelte';
	import InlineVideo from './cards/InlineVideo.svelte';
	import { ago, compact, platformOf, youtubeId } from './util';
	let { post, onOpen, onGone }: { post: Post; onOpen: (p: Post) => void; onGone?: (p: Post) => void } = $props();
	const plat = $derived(platformOf(post.platform));
	let revealed = $state(false);
	// svelte-ignore state_referenced_locally
	let gone = $state(post.verdict === 'down');
	const blurred = $derived((post.nsfw || post.spoiler) && !revealed);
	const first = $derived(post.media[0]);
	let broken = $state(false); // the picture could not be loaded: show the post without a frame
	const top = $derived(post.top ?? []);
	const yt = $derived(!!(youtubeId(post.link_url) || youtubeId(post.url)));
	// Each kind of post gets its own shape, so the wall has texture instead of one repeated card.
	const variant = $derived.by(() => {
		if (first && !broken && !blurred) return 'media';
		if (post.kind === 'question') return 'question';
		if ((post.kind === 'discussion' || post.kind === 'humor') && top.length) return 'take';
		if (post.link_url && post.domain) return 'link';
		return 'text';
	});
	const open = () => onOpen(post);
	function down() {
		gone = true;
		void crApi.pick(post.pick_id, {hidden: true}).catch(() => {});
	}
</script>
{#if gone}
	<article class="card gone" data-pick={post.pick_id}>
		<p class="muted small">Hidden. Tell your curator why? <button type="button" class="link" onclick={() => onGone?.(post)}>Dismiss</button></p>
		<Reasons {post} verdict="down" onDone={() => onGone?.(post)} />
	</article>
{:else}
	<article class="card v-{variant}" class:wild={post.wildcard} class:hot={post.badges?.includes('hot')} data-pick={post.pick_id} data-seen={post.pick_id} data-variant={variant}>
		<header class="meta">
			<span class="plat" title={plat.label}>{plat.icon} {plat.label}</span>
			{#if post.community}<span class="comm">{post.community}</span>{/if}
			{#if post.author}<span class="muted">by {post.author}</span>{/if}
			{#if post.published_at}<span class="muted">· {ago(post.published_at)}</span>{/if}
			{#if post.nsfw}<span class="tag">NSFW</span>{/if}{#if post.spoiler}<span class="tag">Spoiler</span>{/if}
		</header>
		<CardBadges {post} />
		<h3><a href={post.url} onclick={(e) => { if (e.metaKey || e.ctrlKey || e.shiftKey) return; e.preventDefault(); onOpen(post); }}>{post.title}</a></h3>
		{#if blurred}
			<button type="button" class="reveal" onclick={() => { revealed = true; }}>{post.nsfw ? 'NSFW' : 'Spoiler'} content hidden. Click to show.</button>
		{:else}
			{#if post.excerpt}<p class="excerpt">{post.excerpt}</p>{/if}
			{#if first && !broken}
				<div class="media">
					{#if first.kind === 'video' && first.src}
						<InlineVideo m={first} href={post.link_url || post.url} onOpen={open} />
					{:else if first.kind === 'video'}
						<a class="vid" href={post.link_url || post.url} target="_blank" rel="noopener noreferrer" aria-label="Play video (opens original)">
							{#if first.poster}<img use:authSrc={first.poster} alt="" loading="lazy" onerror={() => { broken = true; }} />{:else}<span class="noposter"></span>{/if}<span class="playbtn">▶</span>
						</a>
					{:else}
						<button type="button" class="imgbtn" aria-label="Open post" onclick={() => onOpen(post)}><img use:authSrc={first.thumb || first.url} alt="" loading="lazy" onerror={() => { broken = true; }} /></button>
					{/if}
					{#if yt && first.kind !== 'video'}<span class="ytbadge" aria-hidden="true">▶ YouTube</span>{/if}
					{#if post.media.length > 1}<span class="count">{post.media.length} images</span>{/if}
				</div>
			{/if}
		{/if}
		{#if variant === 'question'}
			<TopReplies {top} label="Top answer" big onOpen={open} />
		{:else if variant === 'take'}
			<TopReplies {top} big max={2} onOpen={open} />
		{:else}
			<TopReplies {top} onOpen={open} />
		{/if}
		{#if post.domain && post.link_url}<a class="domain" href={post.link_url} target="_blank" rel="noopener noreferrer">↗ {post.domain}</a>{/if}
		<ElsewhereChips list={post.elsewhere ?? []} />
		{#if post.why}<p class="why">✨ {post.why}</p>{/if}
		<footer>
			<span class="stats muted small">{#if post.score != null}▲ {compact(post.score)}{/if}{#if post.comments != null}<button type="button" class="link" onclick={() => onOpen(post)}>💬 {compact(post.comments)}</button>{/if}</span>
			<Feedback {post} onDown={down} onHide={() => onGone?.(post)} />
		</footer>
	</article>
{/if}
<style>
	.card{display:grid;gap:.6rem;padding:.9rem 1rem;border:1px solid var(--line);border-radius:var(--r-md);background:var(--surface);min-width:0;overflow:hidden}
	.card.gone{background:transparent}
	.meta{display:flex;flex-wrap:wrap;gap:.35rem .6rem;align-items:center;font-size:.8rem}
	.plat{font-weight:600}.comm{color:var(--ai);font-weight:600}
	.muted{color:var(--muted)}.small{font-size:.8rem}
	.tag{padding:.05rem .45rem;border-radius:var(--r-pill);border:1px solid var(--bad);color:var(--bad);font-size:.7rem}
	h3{margin:0;font-size:1.05rem;line-height:1.3;overflow-wrap:anywhere}
	.card{overflow-wrap:anywhere}
	h3 a{color:var(--text);text-decoration:none}h3 a:hover{text-decoration:underline}
	.excerpt{margin:0;color:var(--muted);font-size:.9rem;display:-webkit-box;-webkit-line-clamp:4;line-clamp:4;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}
	.ytbadge{position:absolute;left:.5rem;bottom:.5rem;padding:.15rem .55rem;border-radius:var(--r-pill);background:#e00;color:#fff;font-size:.72rem;font-weight:800;pointer-events:none}
	.media{position:relative;border-radius:var(--r-sm);overflow:hidden;background:#0006}
	.media img{display:block;width:100%;max-height:28rem;object-fit:cover}
	.imgbtn{display:block;width:100%;padding:0;border:0;background:none;cursor:pointer}
	.vid{display:block;position:relative;min-height:8rem}
	.noposter{display:block;height:10rem}
	.playbtn{position:absolute;inset:0;display:grid;place-items:center;font-size:2.5rem;color:#fff;text-shadow:0 2px 12px #000}
	.count{position:absolute;right:.5rem;bottom:.5rem;padding:.15rem .55rem;border-radius:var(--r-pill);background:rgba(0,0,0,.7);color:#fff;font-size:.75rem}
	.reveal{padding:1.5rem 1rem;border:1px dashed var(--line-strong);border-radius:var(--r-sm);background:var(--surface-hi);color:var(--muted);font:inherit;cursor:pointer}
	.domain{font-size:.8rem;color:var(--info);text-decoration:none;overflow-wrap:anywhere}
	.why{margin:0;font-size:.78rem;color:var(--faint);font-style:italic}
	footer{display:grid;gap:.4rem}
	.stats{display:flex;gap:.8rem;align-items:center}
	.link{background:none;border:0;padding:0;color:inherit;font:inherit;cursor:pointer;text-decoration:underline}
	/* Variants: same parts, different emphasis. */
	.v-media .media{order:-1;margin:-.9rem -1rem 0;border-radius:0}
	.v-media .media img{max-height:34rem}
	.v-media .excerpt{-webkit-line-clamp:2;line-clamp:2}
	.v-question h3{font-size:1.2rem;font-weight:700;padding-left:.9rem;border-left:4px solid var(--info)}
	.v-question h3::before{content:'❝ ';color:var(--info)}
	.v-question .excerpt{-webkit-line-clamp:3;line-clamp:3}
	.v-take .excerpt{-webkit-line-clamp:2;line-clamp:2}
	.v-take h3{font-size:1.1rem}
	.v-link .domain{display:inline-flex;align-self:start;padding:.2rem .6rem;border-radius:var(--r-pill);background:var(--surface-hi);font-weight:600}
	.v-text h3{font-size:1.08rem}
	.wild{border-color:rgba(157,134,255,.55)}
	.hot{box-shadow:0 0 0 1px #ff8a4c55, 0 6px 24px -12px #ff8a4c}
</style>
