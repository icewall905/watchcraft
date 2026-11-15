<script lang="ts" module>
 import { currentProfileId } from '$lib/api';
 // Blob URLs shared across mounts: polling, moving between views or reopening the viewer never refetches
 // an image already on screen. Bounded; the oldest entries are revoked. Keyed per profile.
 const MAX = 400;
 const cache = new Map<string, Promise<string>>();
 const LOCAL = /^\/api\/hub\/boards\/\d+\/(?:pins\/\d+\/media|reference\/media)(?:\?v=\d+)?$/;
 export function boardMediaUrl(url: string): Promise<string> {
  // Only this authenticated local media route is allowed; never load a provider URL.
  if (!LOCAL.test(url)) return Promise.reject(new Error('Image unavailable'));
  const profile = currentProfileId();
  const key = `${profile ?? ''}|${url}`;
  const hit = cache.get(key);
  if (hit) { cache.delete(key); cache.set(key, hit); return hit; }
  const job = fetch(url, {credentials: 'same-origin', headers: profile ? {'X-Profile-Id': profile} : {}})
   .then(r => { if (!r.ok) throw new Error('Image unavailable'); return r.blob(); })
   .then(blob => URL.createObjectURL(blob));
  job.catch(() => cache.delete(key));
  cache.set(key, job);
  while (cache.size > MAX) {
   const [oldest, old] = cache.entries().next().value as [string, Promise<string>];
   cache.delete(oldest); old.then(u => setTimeout(() => URL.revokeObjectURL(u), 30000), () => {});
  }
  return job;
 }
 export async function boardMediaBlob(url: string): Promise<Blob> {
  return (await fetch(await boardMediaUrl(url))).blob();
 }
</script>
<script lang="ts">
 let { url, alt = '', animated = false, eager = false, width = 0, height = 0, fit = false }:
  {url: string; alt?: string; animated?: boolean; eager?: boolean; width?: number; height?: number; fit?: boolean} = $props();
 let node = $state<HTMLSpanElement>(), visible = $state(false);
 let src = $state(''), failed = $state(false), play = $state(false), reduced = $state(false), shown = $state(false);
 const ratio = $derived(width > 0 && height > 0 ? `${width} / ${height}` : '');
 $effect(() => { const query = window.matchMedia('(prefers-reduced-motion: reduce)'); reduced = query.matches; const changed = () => { reduced = query.matches; }; query.addEventListener('change', changed); return () => query.removeEventListener('change', changed); });
 $effect(() => {
  if (eager) { visible = true; return; }
  const element = node;
  if (!element || visible) return;
  const observer = new IntersectionObserver(entries => {
   if (entries.some(entry => entry.isIntersecting)) { visible = true; observer.disconnect(); }
  }, {rootMargin: '600px'});
  observer.observe(element);
  return () => observer.disconnect();
 });
 $effect(() => {
  let live = true; src = ''; failed = false; shown = false;
  if (!LOCAL.test(url)) { failed = true; return; }
  if (!visible || (animated && reduced && !play)) return;
  boardMediaUrl(url).then(u => { if (live) src = u; }, () => { if (live) failed = true; });
  return () => { live = false; };
 });
</script>
<span class="media" class:fit class:sized={!!ratio} bind:this={node} style:aspect-ratio={!src && ratio ? ratio : null}>
{#if src}<img {src} {alt} class:shown loading={eager ? 'eager' : 'lazy'} draggable="false" onload={() => { shown = true; }} onerror={() => { src = ''; failed = true; }} />
{:else if animated && reduced && !play}<span class="placeholder">Animated image · Open to play</span>
{:else if failed}<span class="placeholder">Image unavailable</span>
{:else}<span class="placeholder shimmer" aria-hidden="true"></span>{/if}
{#if animated && reduced && eager && !play}<button onclick={(event) => { event.stopPropagation(); play = true; }}>Play GIF</button>{/if}
</span>
<style>
 .media{display:block;position:relative}
 .media:not(.fit):not(.sized) .placeholder{min-height:10rem}
 .placeholder{display:grid;place-items:center;height:100%;color:var(--muted);font-size:.8rem;padding:1rem;box-sizing:border-box}
 .shimmer{background:linear-gradient(100deg,rgba(255,255,255,.03) 30%,rgba(255,255,255,.08) 50%,rgba(255,255,255,.03) 70%);background-size:200% 100%;animation:shimmer 1.4s linear infinite}
 img{display:block;width:100%;height:auto;opacity:0;transition:opacity .35s var(--ease,ease)}
 img.shown{opacity:1}
 .fit img{width:auto;height:auto;max-width:100%;max-height:100%;margin:auto}
 button{display:block;margin:.5rem auto}
 @keyframes shimmer{to{background-position:-200% 0}}
 @media (prefers-reduced-motion: reduce){.shimmer{animation:none}img{transition:none}}
</style>
