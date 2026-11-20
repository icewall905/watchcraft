<script lang="ts">
 import { onMount } from 'svelte';
 import { page } from '$app/state';
 import { publicMedia, publicShare, type PublicPin, type PublicShare } from '$lib/hub/share';

 // Public, read-only view of one shared image or board. No cookies, no profile: the token is the only key.
 const token = page.params.token ?? '';
 let data = $state<PublicShare | null>(null);
 let pins = $state<PublicPin[]>([]);
 let view = $state<'loading' | 'ok' | 'gone' | 'busy'>('loading');
 let open = $state<PublicPin | null>(null);
 let loadingMore = $state(false);

 onMount(async () => {
  const r = await publicShare(token).catch(() => 404 as const);
  if (r === 404) view = 'gone';
  else if (r === 429) view = 'busy';
  else { data = r; pins = r.pins; view = 'ok'; }
 });
 async function more() {
  if (!data?.next_cursor || loadingMore) return;
  loadingMore = true;
  const r = await publicShare(token, data.next_cursor).catch(() => 404 as const);
  loadingMore = false;
  if (typeof r === 'object') { data = r; pins = [...pins, ...r.pins]; }
 }
 const until = (t: number) => new Date(t * 1000).toLocaleDateString(undefined, {day: 'numeric', month: 'long'});
 function key(e: KeyboardEvent) { if (e.key === 'Escape') open = null; }
</script>

<svelte:head>
 <title>{data?.title ? `${data.title} · ` : ''}Shared on The Hub</title>
 <meta name="robots" content="noindex, nofollow, noarchive" />
 <meta name="referrer" content="no-referrer" />
</svelte:head>
<svelte:window onkeydown={key} />

<main class="shared">
 {#if view === 'loading'}<p class="msg" role="status">Loading…</p>
 {:else if view === 'gone'}
  <section class="msg"><h1>This link is unavailable</h1><p>It may have expired, or the person who shared it turned it off.</p></section>
 {:else if view === 'busy'}
  <section class="msg"><h1>Too many requests</h1><p>Wait a few minutes, then reload this page.</p></section>
 {:else if data}
  <header>
   <p class="eyebrow">Shared {data.kind === 'board' ? 'mood board' : 'image'}</p>
   <h1>{data.title || 'Untitled'}</h1>
   {#if data.expires_at}<p class="muted">Available until {until(data.expires_at)}</p>{/if}
  </header>
  {#if data.kind === 'pin' && pins[0]}
   <figure class="single">
    <img src={publicMedia(token, pins[0].id)} alt={pins[0].title} width={pins[0].width || undefined} height={pins[0].height || undefined} referrerpolicy="no-referrer" />
    {#if pins[0].source_url}<figcaption><a href={pins[0].source_url} target="_blank" rel="noopener noreferrer">Original source ↗</a></figcaption>{/if}
   </figure>
  {:else if pins.length}
   <ul class="grid" aria-label="Images">
    {#each pins as p (p.id)}
     <li><button onclick={() => { open = p; }} aria-label={`Open ${p.title}`}>
      <img src={publicMedia(token, p.id)} alt={p.title} loading="lazy" width={p.width || undefined} height={p.height || undefined} referrerpolicy="no-referrer" />
     </button></li>
    {/each}
   </ul>
   {#if data.next_cursor}<button class="more" disabled={loadingMore} onclick={more}>{loadingMore ? 'Loading…' : 'Show more'}</button>{/if}
  {:else}<p class="muted">This board has no images yet.</p>{/if}
 {/if}
 <footer class="muted">Shared with The Hub</footer>
</main>

{#if open}
 <div class="lightbox" role="dialog" aria-modal="true" aria-label={open.title} tabindex="-1" onclick={(e) => { if (e.target === e.currentTarget) open = null; }} onkeydown={key}>
  <button class="close" aria-label="Close" onclick={() => { open = null; }}>×</button>
  <img src={publicMedia(token, open.id)} alt={open.title} referrerpolicy="no-referrer" />
  <p>{open.title}{#if open.source_url} · <a href={open.source_url} target="_blank" rel="noopener noreferrer">Original source ↗</a>{/if}</p>
 </div>
{/if}

<style>
 :global(body){margin:0}
 .shared{min-height:100dvh;box-sizing:border-box;max-width:1200px;margin:auto;padding:1.25rem 1rem 2rem;font-family:system-ui,sans-serif;color:var(--text,#f3f0ff)}
 header{margin-bottom:1.2rem}
 .eyebrow{margin:0;font-size:.75rem;letter-spacing:.1em;text-transform:uppercase;color:var(--muted,#aaa4d2)}
 h1{margin:.2rem 0;font-size:clamp(1.5rem,5vw,2.4rem);line-height:1.15;overflow-wrap:anywhere}
 .muted{color:var(--muted,#aaa4d2)}
 .msg{text-align:center;padding:20vh 1rem}
 .single{margin:0;display:flex;flex-direction:column;align-items:center;gap:.6rem}
 .single img{max-width:100%;max-height:80dvh;height:auto;border-radius:12px;object-fit:contain}
 .grid{list-style:none;margin:0;padding:0;columns:2 9rem;column-gap:.6rem}
 .grid li{break-inside:avoid;margin-bottom:.6rem}
 .grid button{display:block;width:100%;padding:0;border:0;background:none;cursor:zoom-in;border-radius:10px;overflow:hidden}
 .grid img{display:block;width:100%;height:auto;background:rgba(255,255,255,.05)}
 @media (min-width: 700px){ .grid{columns:4 12rem} }
 .more{display:block;margin:1rem auto;padding:.6rem 1.4rem;border-radius:999px;border:1px solid rgba(255,255,255,.2);background:transparent;color:inherit;font:inherit;cursor:pointer}
 a{color:var(--accent,#ffb547)}
 footer{margin-top:2rem;text-align:center;font-size:.8rem}
 .lightbox{position:fixed;inset:0;z-index:50;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:.6rem;padding:1rem;background:rgba(5,5,12,.92);color:#fff}
 .lightbox img{max-width:100%;max-height:82dvh;object-fit:contain}
 .lightbox p{margin:0;text-align:center;overflow-wrap:anywhere}
 .close{position:absolute;top:.6rem;right:.8rem;width:2.6rem;height:2.6rem;border:0;border-radius:50%;background:rgba(255,255,255,.12);color:#fff;font-size:1.6rem;cursor:pointer}
</style>
