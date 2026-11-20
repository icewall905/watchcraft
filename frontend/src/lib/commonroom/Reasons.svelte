<script lang="ts">
	import { crApi, type Post, type Verdict } from '$lib/hub/commonroom';
	import { REASONS } from './util';
	let { post, verdict, onDone }: { post: Post; verdict: Verdict; onDone?: () => void } = $props();
	let picked = $state<string[]>([]), note = $state(''), busy = $state(false), sent = $state(false), err = $state('');
	const toggle = (r: string) => { picked = picked.includes(r) ? picked.filter((x) => x !== r) : [...picked, r]; };
	async function send() {
		busy = true; err = '';
		try { await crApi.feedback(post.pick_id, {verdict, reasons: picked, note: note.trim()}); sent = true; onDone?.(); }
		catch (e) { err = e instanceof Error ? e.message : 'Could not save.'; }
		finally { busy = false; }
	}
</script>
<div class="reasons">
	<div class="chips">{#each REASONS as r (r)}<button type="button" class="chip" class:on={picked.includes(r)} aria-pressed={picked.includes(r)} onclick={() => toggle(r)}>{r}</button>{/each}</div>
	<div class="row">
		<input type="text" placeholder="Optional note for your curator" aria-label="Note for your curator" bind:value={note} maxlength="300" />
		<button type="button" class="btn" disabled={busy} onclick={send}>{sent ? 'Sent' : 'Send'}</button>
	</div>
	{#if err}<p class="err" role="alert">{err}</p>{/if}
</div>
<style>
	.reasons{display:grid;gap:.5rem;padding:.6rem;border:1px solid var(--line);border-radius:var(--r-md);background:var(--surface)}
	.chips{display:flex;flex-wrap:wrap;gap:.4rem}
	.chip{padding:.3rem .7rem;border-radius:var(--r-pill);border:1px solid var(--line);background:transparent;color:var(--text);font:inherit;font-size:.8rem;cursor:pointer}
	.chip.on{border-color:var(--ai);background:rgba(157,134,255,.18)}
	.row{display:flex;gap:.5rem}
	.row input{flex:1;min-width:0;padding:.45rem .7rem;border-radius:var(--r-pill);border:1px solid var(--line);background:transparent;color:var(--text);font:inherit}
	.err{margin:0;color:var(--bad);font-size:.8rem}
</style>
