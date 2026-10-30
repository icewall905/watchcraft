<script lang="ts">
 import { onMount } from 'svelte';
 import { request } from '$lib/api';
 import { focusable } from '$lib/nav';
 type Curator = { id:number; name:string; instructions:string; provider:string; source:string; enabled:boolean; error:string; last_success:number|null; next_run:number };
 type Item = { id:number; title:string; url:string; provider:string; saved:boolean; discovered_at:number };
 let curators = $state<Curator[]>([]), items = $state<Item[]>([]);
 let name = $state(''), instructions = $state(''), source = $state(''), provider = $state('rss');
 let saved = $state(false), busy = $state(false), error = $state('');
 async function load() {
  try { [curators, items] = await Promise.all([request<Curator[]>('/curation/curators'), request<Item[]>(`/curation/discoveries?saved=${saved}`)]); error=''; }
  catch(e) { error=String(e); }
 }
 onMount(() => { load(); const timer=setInterval(load,15000); return () => clearInterval(timer); });
 async function create(event:SubmitEvent) {
  event.preventDefault(); busy=true;
  try { await request('/curation/curators',{method:'POST',body:{name,instructions,source,provider,interval_hours:6}}); name=''; source=''; instructions=''; await load(); }
  catch(e) { error=String(e); } finally { busy=false; }
 }
 async function control(id:number, action:string) { try { await request(`/curation/curators/${id}/${action}`,{method:'POST'}); await load(); } catch(e) { error=String(e); } }
 async function save(id:number) { try { await request(`/curation/discoveries/${id}/save`,{method:'POST'}); await load(); } catch(e) { error=String(e); } }
</script>
<svelte:head><title>Discover · WatchCraft</title></svelte:head>
<div class="hub">
 <div class="intro"><img src="/favicon.svg" alt="" width="80" height="80"/><div><p class="eyebrow">YOUR PERSONAL DISCOVERY HUB</p><h1>Keep watch over your world.</h1><p>Give your curiosity a home. Collect new articles and releases alongside your video library.</p></div></div>
 {#if error}<p role="alert">{error}</p>{/if}
 <div class="workspace">
 <aside>
 <h2>Your curators</h2>
 <p>Public RSS / Atom feeds and GitHub releases. Checked every six hours, even after a restart.</p>
 {#each curators as c}<section class="curator"><h3>{c.name}</h3><p>{c.provider} · {c.enabled ? 'Watching' : 'Paused'}</p><p>{c.error || (c.last_success ? `Updated ${new Date(c.last_success*1000).toLocaleString()}` : 'Awaiting first discovery')}</p><button class="btn" use:focusable onclick={() => control(c.id,'run')}>Run now</button> <button class="btn" use:focusable onclick={() => control(c.id,c.enabled?'pause':'resume')}>{c.enabled?'Pause':'Resume'}</button></section>{/each}
 <form onsubmit={create}>
 <h2>Craft a new curator</h2>
 <label>Name<input required maxlength="100" bind:value={name} placeholder="Open-source AI releases" /></label>
 <label>Your interests<textarea maxlength="4000" bind:value={instructions} placeholder="What would you like to keep watch over?"></textarea></label>
 <p class="hint">Interests are stored as notes. This first version collects source entries without AI relevance filtering.</p>
 <label>Source type<select bind:value={provider}><option value="rss">RSS / Atom feed</option><option value="github">GitHub repository releases</option></select></label>
 <label>Public HTTPS source<input required type="url" bind:value={source} placeholder={provider==='rss'?'https://example.org/feed.xml':'https://github.com/owner/repository'} /></label>
 <button class="btn primary" disabled={busy}>Start watching</button>
 </form>
 </aside>
 <div><div class="feed-title"><h2>{saved?'Your library':'Fresh discoveries'}</h2><button class="btn" use:focusable onclick={() => { saved=!saved; load(); }}>{saved?'Show discoveries':'Saved library'}</button></div>
 {#if !items.length}<section class="empty"><span>✧</span><h2>{saved?'Your collection starts here.':'Let curiosity take the first step.'}</h2><p>{saved?'Save a discovery to find it here later.':'Add a curator with a feed or repository. New finds will appear here, with a direct link to their source.'}</p></section>{/if}
 <div class="cards">{#each items as item}<article><p class="eyebrow">{item.provider} · SOURCE TITLE</p><h3><a href={item.url} target="_blank" rel="noopener noreferrer" use:focusable>{item.title}</a></h3><p class="source">{new URL(item.url).hostname}</p><button class="btn" use:focusable onclick={() => save(item.id)}>{item.saved?'Remove from library':'Save to library'}</button></article>{/each}</div>
 </div></div>
</div>
<style>
 .hub{max-width:1600px;margin:auto;padding:2.5rem var(--gutter)}.intro{display:flex;align-items:center;gap:1.5rem;padding:1rem 0 3rem}.eyebrow{font-size:.7rem;letter-spacing:.15em;color:var(--accent);font-weight:700}h1{font-family:Georgia,serif;font-size:clamp(2rem,4vw,3.5rem);line-height:1.1;margin:.5rem 0}p{color:var(--muted)}.workspace{display:grid;grid-template-columns:340px 1fr;gap:2.5rem}aside{min-width:0}form,.curator,article,.empty{border:1px solid var(--line-strong);border-radius:var(--r-lg);padding:1.4rem;background:var(--surface);margin-bottom:1rem}label{display:grid;gap:.4rem;margin:1rem 0;font-size:.85rem}input,textarea,select{width:100%;padding:.7rem;background:var(--bg-0);color:var(--text);border:1px solid var(--line-strong);border-radius:.5rem;font:inherit}textarea{min-height:90px}.hint,.source{font-size:.8rem}.feed-title{display:flex;align-items:center;justify-content:space-between;gap:1rem;margin-bottom:1rem}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:1rem}article h3{font-size:1.2rem}.empty{text-align:center;padding:4rem 2rem}.empty>span{font-size:4rem;color:var(--accent)}@media(max-width:850px){.workspace{grid-template-columns:1fr}.intro>img{display:none}.intro{padding-bottom:1rem}.hub{padding-top:1rem}}
</style>
