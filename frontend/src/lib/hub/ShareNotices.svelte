<script lang="ts">
 import { onMount } from 'svelte';
 import { shareApi, type ShareNotice } from '$lib/hub/share';

 // "Ann shared 3 images with you": newest first; opening or dismissing marks them seen.
 let { onOpen }: { onOpen: (boardId: number) => void } = $props();
 let notices = $state<ShareNotice[]>([]);

 onMount(() => { shareApi.notices().then(r => { notices = r.notices; }).catch(() => {}); });

 function done(ids: number[]) {
  notices = notices.filter(n => !ids.includes(n.id));
  void shareApi.seen(ids).catch(() => {});
 }
 function open(n: ShareNotice) { done(notices.filter(x => x.board_id === n.board_id).map(x => x.id)); onOpen(n.board_id); }
 const text = (n: ShareNotice) => `${n.sender_emoji ? n.sender_emoji + ' ' : ''}${n.sender} shared ${n.kind === 'board' ? 'a board' : n.count === 1 ? 'an image' : `${n.count} images`} with you`;
</script>

{#if notices.length}
 <section class="notices" aria-label="Shared with you">
  {#each notices.slice(0, 3) as n (n.id)}
   <p class="notice" role="status">
    <span>{text(n)} · <em>{n.board_name}</em></span>
    <button class="btn pill" onclick={() => open(n)}>Open</button>
   </p>
  {/each}
  <button class="dismiss" onclick={() => done(notices.map(n => n.id))}>{notices.length > 1 ? 'Dismiss all' : 'Dismiss'}</button>
 </section>
{/if}

<style>
 .notices{display:flex;flex-direction:column;gap:.4rem;margin:0 0 1rem;padding:.7rem .9rem;border:1px solid var(--line);border-radius:var(--r-md);background:var(--surface-2)}
 .notice{display:flex;align-items:center;justify-content:space-between;gap:.7rem;margin:0;flex-wrap:wrap}
 .dismiss{align-self:flex-end;border:0;background:none;color:var(--muted);font:inherit;font-size:.85rem;cursor:pointer;text-decoration:underline}
</style>
