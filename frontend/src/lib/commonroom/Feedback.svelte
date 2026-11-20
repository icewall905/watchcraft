<script lang="ts">
	import { crApi, type Post, type Verdict } from '$lib/hub/commonroom';
	import Reasons from './Reasons.svelte';
	let { post, onDown, onHide }: { post: Post; onDown?: () => void; onHide?: () => void } = $props();
	// svelte-ignore state_referenced_locally
	let verdict = $state<Verdict>(post.verdict), saved = $state(post.saved), panel = $state(false), menu = $state(false), msg = $state('');
	async function vote(v: 'up' | 'down') {
		const next: Verdict = verdict === v ? '' : v;
		const prev = verdict; verdict = next; msg = '';
		try { await crApi.feedback(post.pick_id, {verdict: next, reasons: [], note: ''}); }
		catch { verdict = prev; msg = 'Could not save feedback.'; return; }
		post.verdict = next;
		if (next === 'down') onDown?.();
		else panel = next === 'up';
	}
	async function save() {
		const next = !saved; saved = next;
		try { await crApi.pick(post.pick_id, {saved: next}); post.saved = next; } catch { saved = !next; msg = 'Could not save.'; }
	}
	async function mute(kind: 'community' | 'author' | 'domain', value: string) {
		menu = false;
		try { await crApi.block(kind, value); await crApi.pick(post.pick_id, {hidden: true}); onHide?.(); } catch { msg = 'Could not mute.'; }
	}
</script>
<div class="fb">
	<div class="bar">
		<button type="button" class="act" class:on={verdict === 'up'} aria-pressed={verdict === 'up'} aria-label="Good pick" onclick={() => vote('up')}>👍</button>
		<button type="button" class="act" class:on={verdict === 'down'} aria-pressed={verdict === 'down'} aria-label="Bad pick" onclick={() => vote('down')}>👎</button>
		<button type="button" class="act" class:on={saved} aria-pressed={saved} aria-label={saved ? 'Unsave' : 'Save'} onclick={save}>{saved ? '★' : '☆'}</button>
		<span class="gap"></span>
		<div class="mutewrap">
			<button type="button" class="act" aria-label="Mute…" aria-expanded={menu} onclick={() => { menu = !menu; }}>⋯</button>
			{#if menu}
				<div class="menu" role="menu">
					{#if post.community}<button type="button" role="menuitem" onclick={() => mute('community', post.community)}>Mute {post.community}</button>{/if}
					{#if post.author}<button type="button" role="menuitem" onclick={() => mute('author', post.author)}>Mute {post.author}</button>{/if}
					{#if post.domain}<button type="button" role="menuitem" onclick={() => mute('domain', post.domain)}>Mute {post.domain}</button>{/if}
				</div>
			{/if}
		</div>
	</div>
	{#if msg}<p class="err" role="alert">{msg}</p>{/if}
	{#if panel && verdict === 'up'}<Reasons {post} verdict="up" onDone={() => { panel = false; }} />{/if}
</div>
<style>
	.fb{display:grid;gap:.5rem}
	.bar{display:flex;align-items:center;gap:.35rem}
	.gap{flex:1}
	.act{display:inline-grid;place-items:center;min-width:2.2rem;height:2.2rem;padding:0 .4rem;border-radius:var(--r-pill);border:1px solid var(--line);background:transparent;color:var(--muted);cursor:pointer;font:inherit}
	.act:hover{background:var(--surface-hi);color:var(--text)}
	.act.on{border-color:var(--ok);color:var(--ok);background:rgba(61,220,151,.12)}
	.mutewrap{position:relative}
	.menu{position:absolute;right:0;bottom:2.5rem;z-index:6;display:grid;min-width:12rem;max-width:80vw;padding:.3rem;border:1px solid var(--line-strong);border-radius:var(--r-md);background:var(--overlay-surface);box-shadow:var(--shadow-lg)}
	.menu button{text-align:left;padding:.5rem .7rem;border:0;background:none;color:var(--text);font:inherit;font-size:.85rem;border-radius:var(--r-sm);cursor:pointer;overflow-wrap:anywhere}
	.menu button:hover{background:var(--surface-hi)}
	.err{margin:0;color:var(--bad);font-size:.8rem}
</style>
