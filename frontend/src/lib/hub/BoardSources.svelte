<script lang="ts">
 import { onMount } from 'svelte';
 import Modal from '$lib/components/Modal.svelte';
 import { boardsApi, type BoardSources } from '$lib/hub/boards';

 let { boardId, boardName, onClose }: { boardId: number; boardName: string; onClose: () => void } = $props();
 let data = $state<BoardSources | null>(null), error = $state(''), busy = $state(false);
 const running = $derived(data?.hunt?.status === 'queued' || data?.hunt?.status === 'running');
 const kinds: Record<string, string> = {search_template: 'Site search', gallery: 'Gallery page', feed: 'Feed', subreddit: 'Subreddit', network_search: 'Platform search', commons_category: 'Commons category'};
 const message = (e: unknown) => e instanceof Error ? e.message : 'Could not load this board’s sources.';
 const ago = (t: number) => { const m = Math.round((Date.now() / 1000 - t) / 60); return m < 1 ? 'just now' : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} d ago`; };

 async function load() { try { data = await boardsApi.sources(boardId); error = ''; } catch (e) { error = message(e); } }
 async function act(run: () => Promise<BoardSources>) { busy = true; try { data = await run(); error = ''; } catch (e) { error = message(e); } finally { busy = false; } }

 onMount(() => {
  void load();
  const timer = window.setInterval(() => { if (running && document.visibilityState !== 'hidden') void load(); }, 4000);
  return () => window.clearInterval(timer);
 });
</script>

<Modal portal size="lg" title="Image sources" subtitle={boardName} {onClose}>
 <div class="sources">
  <p class="muted">The source curator searches the web for places that keep publishing images like this board’s (sites, subreddits, Commons categories and GIF platforms, opening blocked sites in an isolated browser), saves a small recipe for each one and checks a sample with the vision model. Board runs then pull fresh images from these sources first.</p>
  <div class="row">
   <button class="btn primary" disabled={busy || running} onclick={() => act(() => boardsApi.findSources(boardId))}>{running ? 'Researching sources…' : 'Find sources'}</button>
   {#if data?.hunt && !running}<span class="muted">Last research {ago(data.hunt.finished_at || data.hunt.created_at)}: {data.hunt.status === 'failed' ? data.hunt.error || 'failed' : `${data.hunt.found} new source${data.hunt.found === 1 ? '' : 's'}`}.</span>{/if}
  </div>
  {#if error}<p class="error" role="alert">{error}</p>{/if}
  {#if data?.hunt && (running || data.hunt.log.length)}
   <details class="log" open={running}>
    <summary>{running ? 'Curator progress' : 'Last research log'}</summary>
    <ol aria-live="polite">{#each data.hunt.log.slice(-12) as entry (entry.at + entry.message)}<li class="step-{entry.step}">{entry.message}</li>{/each}</ol>
   </details>
  {/if}
  {#if data && !data.sources.length}
   <p class="empty">{running ? 'No sources saved yet; they appear here as the curator finds them.' : 'No sources yet. Find sources to give this board its own image sites; until then it uses the household’s general image search.'}</p>
  {:else if data}
   <ul class="list">
    {#each data.sources as src (src.id)}
     <li class:off={!src.enabled || src.status !== 'ok'}>
      <div class="meta">
       <strong>{src.name}</strong>
       <span class="muted">{kinds[src.kind] ?? src.kind} · {#if src.url}<a href={src.url} target="_blank" rel="noopener noreferrer">{src.host}</a>{:else}{src.host}{/if}</span>
       <span class="muted small">Sample fit {src.fit}/{src.tried} · {src.kept} of {src.hits} found images kept{#if src.status === 'broken'} · <span class="warn">stopped working</span>{/if}</span>
      </div>
      <label class="toggle"><input type="checkbox" checked={src.enabled} disabled={busy} onchange={(e) => act(() => boardsApi.setSource(boardId, src.id, e.currentTarget.checked))} />Use</label>
      <button class="btn pill" disabled={busy} aria-label={`Remove ${src.name}`} onclick={() => act(() => boardsApi.removeSource(boardId, src.id))}>Remove</button>
     </li>
    {/each}
   </ul>
  {/if}
 </div>
</Modal>

<style>
 .sources{display:flex;flex-direction:column;gap:.9rem}
 .row{display:flex;flex-wrap:wrap;gap:.75rem;align-items:center}
 .log summary{cursor:pointer;font-weight:600}
 .log ol{margin:.5rem 0 0;padding-left:1.2rem;display:flex;flex-direction:column;gap:.2rem;font-size:.9rem}
 .log .step-saved{color:var(--ok, inherit);font-weight:600}
 .log .step-rejected{opacity:.7}
 .list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:.5rem}
 .list li{display:flex;gap:.75rem;align-items:center;padding:.6rem .8rem;border:1px solid var(--line);border-radius:var(--r-md)}
 .list li.off{opacity:.6}
 .meta{display:flex;flex-direction:column;gap:.15rem;flex:1;min-width:0}
 .meta strong{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .small{font-size:.85rem}
 .warn{color:var(--danger, #c33)}
 .toggle{display:flex;gap:.3rem;align-items:center;white-space:nowrap}
 .empty{padding:1rem;border:1px dashed var(--line);border-radius:var(--r-md)}
 .error{color:var(--danger, #c33)}
 @media (max-width:560px){.list li{flex-wrap:wrap}}
</style>
