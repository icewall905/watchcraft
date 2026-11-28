<script lang="ts" generics="T extends {id: number; width?: number; height?: number}">
 // Masonry with fixed placement. Each image keeps the column it was first put in, so loading more,
 // background finds, hiding or reviewing never shuffles images on screen (CSS `columns` re-balances
 // everything on every change). Columns reset only when their number changes or the list is replaced.
 import type { Snippet } from 'svelte';
 let { items, item, resetKey = '' }: {items: T[]; item: Snippet<[T]>; resetKey?: string | number} = $props();
 let width = $state(typeof window === 'undefined' ? 1200 : Math.min(window.innerWidth, 1400) - 64);
 let narrow = $state(typeof window !== 'undefined' && window.matchMedia('(max-width: 650px)').matches);
 $effect(() => {
  const query = window.matchMedia('(max-width: 650px)');
  const changed = () => { narrow = query.matches; };
  changed(); query.addEventListener('change', changed);
  return () => query.removeEventListener('change', changed);
 });
 // Same rules as the old `columns: 5 15rem` (gap 1rem); two columns on phones.
 const count = $derived(narrow ? 2 : Math.max(1, Math.min(5, Math.floor((width + 16) / 256))));
 let placed = new Map<number, number>(), placedFor = '';
 const size = (it: T) => (it.width && it.height ? Math.min(3, Math.max(0.3, it.height / it.width)) : 1) + 0.35;
 const columns = $derived.by(() => {
  const key = `${count}|${resetKey}`;
  if (key !== placedFor) { placed = new Map(); placedFor = key; }
  const heights = new Array<number>(count).fill(0);
  const out = Array.from({length: count}, () => [] as T[]);
  for (const it of items) { const c = placed.get(it.id); if (c !== undefined) heights[c] += size(it); }
  for (const it of items) {
   let c = placed.get(it.id);
   if (c === undefined) { c = heights.indexOf(Math.min(...heights)); placed.set(it.id, c); heights[c] += size(it); }
   out[c].push(it);
  }
  return out;
 });
</script>
<div class="pin-columns" bind:clientWidth={width} style:--cols={count}>
 {#each columns as column, i (i)}<div class="column">{#each column as it (it.id)}{@render item(it)}{/each}</div>{/each}
</div>
<style>
 .pin-columns{display:grid;grid-template-columns:repeat(var(--cols),minmax(0,1fr));column-gap:1rem;align-items:start}
 .column{min-width:0;display:flex;flex-direction:column}
 @media(max-width:650px){.pin-columns{column-gap:.6rem}}
</style>
