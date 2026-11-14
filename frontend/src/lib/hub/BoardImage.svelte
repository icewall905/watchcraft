<script lang="ts">
 import { currentProfileId } from '$lib/api';
 let { url, alt = '', animated = false, eager = false }: {url: string; alt?: string; animated?: boolean; eager?: boolean} = $props();
 let node = $state<HTMLSpanElement>(), visible = $state(false);
 let src = $state(''), failed = $state(false), play = $state(false), reduced = $state(false);
 $effect(() => { const query = window.matchMedia('(prefers-reduced-motion: reduce)'); reduced = query.matches; const changed = () => { reduced = query.matches; }; query.addEventListener('change', changed); return () => query.removeEventListener('change', changed); });
 $effect(() => {
  if (eager) { visible = true; return; }
  const element = node;
  if (!element || visible) return;
  const observer = new IntersectionObserver(entries => {
   if (entries.some(entry => entry.isIntersecting)) { visible = true; observer.disconnect(); }
  }, {rootMargin: '300px'});
  observer.observe(element);
  return () => observer.disconnect();
 });
 $effect(() => {
  const controller = new AbortController(); let objectUrl = ''; src = ''; failed = false;
  // Only this authenticated local media route is allowed; never load a provider URL.
  if (!/^\/api\/hub\/boards\/\d+\/pins\/\d+\/media$/.test(url)) { failed = true; return; }
  if (!visible || (animated && reduced && !play)) return;
  const profile = currentProfileId();
  fetch(url, {credentials: 'same-origin', headers: profile ? {'X-Profile-Id': profile} : {}, signal: controller.signal})
   .then(r => { if (!r.ok) throw new Error('Image unavailable'); return r.blob(); })
   .then(blob => { if (!controller.signal.aborted) { objectUrl = URL.createObjectURL(blob); src = objectUrl; } })
   .catch(() => { if (!controller.signal.aborted) failed = true; });
  return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
 });
</script>
<span class="media" bind:this={node}>
{#if src}<img {src} {alt} loading={eager ? 'eager' : 'lazy'} onerror={() => { src = ''; failed = true; }} />
{:else if animated && reduced && !play}<span class="placeholder">Animated image · Open to play</span>
{:else if failed}<span class="placeholder">Image unavailable</span>
{:else}<span class="placeholder" aria-hidden="true">Loading image…</span>{/if}
{#if animated && reduced && eager && !play}<button onclick={(event) => { event.stopPropagation(); play = true; }}>Play GIF</button>{/if}
</span>
<style>.media{display:block}.placeholder{display:grid;place-items:center;min-height:10rem;color:var(--muted);font-size:.8rem;padding:1rem}img{display:block;width:100%;height:auto}button{display:block;margin:.5rem auto}</style>
