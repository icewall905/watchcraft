<script lang="ts">
	import Modal from '$lib/components/Modal.svelte';
	import { crApi, type Thread, type PlatformInfo, type ThreadSource, type Strictness } from '$lib/hub/commonroom';
	import { platformOf } from './util';
	let { thread = null, platforms, child = false, onSaved, onDeleted, onClose }: { thread?: Thread | null; platforms: PlatformInfo[]; child?: boolean; onSaved: (t: Thread) => void; onDeleted?: (id: number) => void; onClose: () => void } = $props();
	// svelte-ignore state_referenced_locally
	const init = thread;
	let name = $state(init?.name ?? ''), interests = $state(init?.interests ?? ''), instructions = $state(init?.instructions ?? ''), avoid = $state(init?.avoid ?? '');
	let strictness = $state<Strictness>(init?.strictness ?? 'balanced'), busy = $state(false), err = $state(''), confirmDelete = $state(false);
	let sources = $state<ThreadSource[]>([]), learned = $state<{lessons: string[]; up: number; down: number} | null>(null);
	// svelte-ignore state_referenced_locally
	let addPlatform = $state(platforms[0]?.key ?? ''), addLocator = $state(''), addBusy = $state(false);
	const example = $derived(platforms.find((p) => p.key === addPlatform)?.example ?? '');
	const id = init?.id;
	$effect(() => {
		if (id == null) return;
		let live = true;
		crApi.sources(id).then((r) => { if (live) sources = r.sources; }, () => {});
		crApi.learned(id).then((r) => { if (live) learned = r; }, () => {});
		return () => { live = false; };
	});
	async function save(e: SubmitEvent) {
		e.preventDefault(); if (busy) return;
		busy = true; err = '';
		try {
			const body = {name: name.trim(), interests: interests.trim(), instructions: instructions.trim(), avoid: avoid.trim(), strictness};
			onSaved(id == null ? await crApi.create(body) : await crApi.update(id, body));
		} catch (e) { err = e instanceof Error ? e.message : 'Could not save thread.'; }
		finally { busy = false; }
	}
	async function addSrc() {
		if (id == null || !addLocator.trim() || addBusy) return;
		addBusy = true; err = '';
		try { sources = [...sources, await crApi.addSource(id, {platform: addPlatform, locator: addLocator.trim()})]; addLocator = ''; }
		catch (e) { err = e instanceof Error ? e.message : 'Could not add source.'; }
		finally { addBusy = false; }
	}
	async function toggle(s: ThreadSource) {
		if (id == null) return;
		try { const u = await crApi.setSource(id, s.id, !s.enabled); sources = sources.map((x) => x.id === s.id ? {...x, ...u} : x); } catch (e) { err = e instanceof Error ? e.message : 'Could not update source.'; }
	}
	async function drop(s: ThreadSource) {
		if (id == null) return;
		try { await crApi.removeSource(id, s.id); sources = sources.filter((x) => x.id !== s.id); } catch (e) { err = e instanceof Error ? e.message : 'Could not remove source.'; }
	}
	async function del() {
		if (id == null) return;
		try { await crApi.remove(id); onDeleted?.(id); } catch (e) { err = e instanceof Error ? e.message : 'Could not delete thread.'; }
	}
	const groups = $derived([{title: 'Yours', list: sources.filter((s) => s.origin === 'user')}, {title: 'Suggested by AI', list: sources.filter((s) => s.origin !== 'user')}]);
</script>
<Modal portal title={id == null ? 'New thread' : 'Edit thread'} size="lg" {onClose}>
	<form class="ed" onsubmit={save}>
		<label>Thread name<input bind:value={name} required maxlength="80" placeholder="Retro computing" /></label>
		<label>Interests<textarea bind:value={interests} required rows="2" placeholder="Amiga, demoscene, 8-bit homebrew"></textarea></label>
		<label>What to look for<textarea bind:value={instructions} rows="2" placeholder="Deep dives and project write-ups"></textarea></label>
		<label>What to avoid<textarea bind:value={avoid} rows="2" placeholder="Memes, hardware shopping"></textarea></label>
		{#if !child}<label>Strictness
			<select bind:value={strictness}><option value="strict">Strict: only clear matches</option><option value="balanced">Balanced</option><option value="loose">Loose: more variety</option></select>
		</label>{/if}
		{#if err}<p class="err" role="alert">{err}</p>{/if}
		<div class="row"><button class="btn primary" disabled={busy || !name.trim() || !interests.trim()}>{id == null ? 'Create thread' : 'Save changes'}</button></div>
	</form>
	{#if id != null}
		<section class="blk" aria-label="Sources">
			<h3>Sources</h3>
			<p class="muted small">Your curator also finds sources by itself. Add your own, or switch any off.</p>
			{#each groups as g (g.title)}
				{#if g.list.length}
					<h4>{g.title}</h4>
					<ul class="src">{#each g.list as s (s.id)}
						<li class:off={!s.enabled}>
							<span class="lbl">{platformOf(s.platform).icon} <strong>{s.label || s.locator}</strong> <span class="muted small">{platformOf(s.platform).label}</span></span>
							{#if s.error}<span class="bad small" title={s.error}>error</span>{/if}
							<button type="button" class="btn sm" onclick={() => toggle(s)} aria-label={`${s.enabled ? 'Disable' : 'Enable'} ${s.label || s.locator}`}>{s.enabled ? 'On' : 'Off'}</button>
							<button type="button" class="btn sm" onclick={() => drop(s)} aria-label={`Remove ${s.label || s.locator}`}>✕</button>
						</li>{/each}</ul>
				{/if}
			{/each}
			{#if !sources.length}<p class="muted small">No sources yet. Run the thread to let the curator pick some.</p>{/if}
			<div class="add">
				<select bind:value={addPlatform} aria-label="Platform">{#each platforms as p (p.key)}<option value={p.key}>{p.label}</option>{/each}</select>
				<input bind:value={addLocator} aria-label="Source locator" placeholder={example} onkeydown={(e) => { if (e.key === 'Enter') { e.preventDefault(); void addSrc(); } }} />
				<button type="button" class="btn" disabled={addBusy || !addLocator.trim()} onclick={addSrc}>Add source</button>
			</div>
		</section>
		<section class="blk" aria-label="What I've learned">
			<h3>What I've learned</h3>
			{#if learned}
				<p class="muted small">From {learned.up} 👍 and {learned.down} 👎.</p>
				{#if learned.lessons.length}<ul class="less">{#each learned.lessons as l (l)}<li>{l}</li>{/each}</ul>{:else}<p class="muted small">Nothing yet. Rate a few posts.</p>{/if}
			{:else}<p class="muted small">Loading…</p>{/if}
		</section>
		<section class="blk danger">
			{#if confirmDelete}
				<p>Delete this thread and its picks?</p>
				<button type="button" class="btn" onclick={del}>Yes, delete</button> <button type="button" class="btn" onclick={() => { confirmDelete = false; }}>Cancel</button>
			{:else}<button type="button" class="btn" onclick={() => { confirmDelete = true; }}>Delete thread</button>{/if}
		</section>
	{/if}
</Modal>
<style>
	.ed{display:grid;gap:.8rem}
	label{display:grid;gap:.3rem;font-size:.85rem;color:var(--muted)}
	input,textarea,select{padding:.55rem .75rem;border-radius:var(--r-sm);border:1px solid var(--line);background:transparent;color:var(--text);font:inherit;min-width:0;width:100%;box-sizing:border-box}
	.blk{margin-top:1.4rem;padding-top:1rem;border-top:1px solid var(--line)}
	h3{margin:0 0 .3rem;font-size:1rem}h4{margin:.8rem 0 .3rem;font-size:.8rem;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}
	.src{list-style:none;margin:0;padding:0;display:grid;gap:.4rem}
	.src li{display:flex;gap:.5rem;align-items:center;padding:.4rem .6rem;border:1px solid var(--line);border-radius:var(--r-sm)}
	.src li.off{opacity:.55}.lbl{flex:1;min-width:0;overflow-wrap:anywhere}
	.add{display:grid;grid-template-columns:minmax(0,9rem) 1fr auto;gap:.5rem;margin-top:.8rem}
	@media (max-width:560px){.add{grid-template-columns:1fr}}
	.less{margin:.3rem 0 0;padding-left:1.2rem;display:grid;gap:.3rem;font-size:.9rem}
	.muted{color:var(--muted)}.small{font-size:.8rem}.bad,.err{color:var(--bad)}
</style>
