<script lang="ts">
 import type { Snippet } from 'svelte';
 import Modal from '$lib/components/Modal.svelte';
 import { nav } from '$lib/nav';
 import BoardImage, { boardMediaBlob } from '$lib/hub/BoardImage.svelte';
 import type { BoardPin } from '$lib/hub/boards';

 type Pin = BoardPin & {board_name?: string};
 let { pin, pins, boardName = '', busy = false, onClose, onNavigate, onSave, onFits, onNotFit, onHide, onShare, actions }: {
  pin: Pin; pins: Pin[]; boardName?: string; busy?: boolean;
  onClose: () => void; onNavigate: (p: Pin) => void;
  onSave: (p: Pin) => void; onFits: (p: Pin) => void; onNotFit: (p: Pin) => void; onHide: (p: Pin) => void;
  onShare?: (p: Pin) => void;  // Opens the household/link share dialog; without it, the device share sheet.
  actions?: Snippet;
 } = $props();

 const index = $derived(pins.findIndex(p => p.id === pin.id));
 const prev = $derived(index > 0 ? pins[index - 1] : null);
 const next = $derived(index >= 0 && index < pins.length - 1 ? pins[index + 1] : null);
 const uploaded = $derived(!/^https?:\/\//i.test(pin.source_url));

 let scale = $state(1), x = $state(0), y = $state(0), stage = $state<HTMLDivElement>();
 let drag: {px: number; py: number; x: number; y: number} | null = null;
 const pointers = new Map<number, {x: number; y: number}>();
 let pinchStart = 0, pinchScale = 1;
 let status = $state('');
 const MIN = 1, MAX = 6;

 $effect(() => { void pin.id; scale = 1; x = 0; y = 0; status = ''; });

 function zoomTo(value: number, cx = 0, cy = 0) {
  const next = Math.min(MAX, Math.max(MIN, value));
  // Keep the point under the cursor fixed while zooming.
  x = cx - (cx - x) * (next / scale); y = cy - (cy - y) * (next / scale);
  scale = next;
  if (scale === 1) { x = 0; y = 0; }
 }
 function centre(e: {clientX: number; clientY: number}) {
  const r = stage?.getBoundingClientRect();
  return r ? [e.clientX - r.left - r.width / 2, e.clientY - r.top - r.height / 2] : [0, 0];
 }
 function wheel(e: WheelEvent) { e.preventDefault(); const [cx, cy] = centre(e); zoomTo(scale * (e.deltaY < 0 ? 1.2 : 1 / 1.2), cx, cy); }
 function dbl(e: MouseEvent) { const [cx, cy] = centre(e); zoomTo(scale > 1 ? 1 : 2.5, cx, cy); }
 function down(e: PointerEvent) {
  if ((e.target as HTMLElement | null)?.closest('button')) { swipe = 0; return; }  // Controls keep their own clicks.
  pointers.set(e.pointerId, {x: e.clientX, y: e.clientY});
  (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  if (pointers.size === 2) { const [a, b] = [...pointers.values()]; pinchStart = Math.hypot(a.x - b.x, a.y - b.y); pinchScale = scale; drag = null; }
  else if (scale > 1) drag = {px: e.clientX, py: e.clientY, x, y};
 }
 function move(e: PointerEvent) {
  if (!pointers.has(e.pointerId)) return;
  pointers.set(e.pointerId, {x: e.clientX, y: e.clientY});
  if (pointers.size === 2 && pinchStart) { const [a, b] = [...pointers.values()]; zoomTo(pinchScale * Math.hypot(a.x - b.x, a.y - b.y) / pinchStart); }
  else if (drag) { x = drag.x + e.clientX - drag.px; y = drag.y + e.clientY - drag.py; }
 }
 function up(e: PointerEvent) {
  const start = drag; pointers.delete(e.pointerId); if (pointers.size < 2) pinchStart = 0;
  // A horizontal swipe at normal size moves between images.
  if (start === null && scale === 1 && pointers.size === 0 && swipe && e.clientX - swipe > 60 && prev) onNavigate(prev);
  else if (start === null && scale === 1 && pointers.size === 0 && swipe && swipe - e.clientX > 60 && next) onNavigate(next);
  drag = null; swipe = 0;
 }
 let swipe = 0, side = $state<HTMLElement>();
 // Arrow keys go through the TV-remote engine, which otherwise uses them to move focus. Browse images unless
 // focus is in the side panel (where a remote still needs left/right between buttons); media keys always browse.
 $effect(() => nav.handleKeys((k) => {
  const inSide = !!side && side.contains(document.activeElement);
  const back = k === 'prev' || (k === 'left' && !inSide), fwd = k === 'next' || (k === 'right' && !inSide);
  if (scale !== 1 && (k === 'left' || k === 'right')) return false;
  if (back && prev) { onNavigate(prev); return true; }
  if (fwd && next) { onNavigate(next); return true; }
  return false;
 }));
 function key(e: KeyboardEvent) {
  const target = e.target as HTMLElement | null;
  if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return;
  if (e.key === '+' || e.key === '=') zoomTo(scale * 1.4);
  else if (e.key === '-') zoomTo(scale / 1.4);
  else if (e.key === '0') zoomTo(1);
 }

 function filename(blob: Blob) {
  const ext = ({'image/jpeg': 'jpg', 'image/png': 'png', 'image/gif': 'gif', 'image/webp': 'webp'} as Record<string, string>)[blob.type] ?? 'jpg';
  const base = (pin.title || 'image').replace(/[^\p{L}\p{N} _-]+/gu, ' ').trim().replace(/\s+/g, '-').slice(0, 60) || 'image';
  return `${base}.${ext}`;
 }
 async function download() {
  try {
   const blob = await boardMediaBlob(pin.image_url);
   const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = filename(blob);
   document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 10000);
   status = 'Download started.';
  } catch { status = 'Could not download this image.'; }
 }
 // The share sheet must open straight from the tap: Safari and Chrome drop the tap's permission
 // ("NotAllowedError") if we first wait for a multi-megabyte download. So the file is prepared as
 // soon as the image opens, and the tap only hands it over.
 let shareFile = $state<{id: number; file: File} | null>(null);
 $effect(() => {
  const id = pin.id, url = pin.image_url;
  if (onShare || typeof navigator === 'undefined' || !('share' in navigator)) return;
  let live = true;
  boardMediaBlob(url).then(blob => { if (live) shareFile = {id, file: new File([blob], filename(blob), {type: blob.type || 'image/jpeg'})}; }).catch(() => {});
  return () => { live = false; };
 });
 const why = (e: unknown) => { const err = e as Error; return [err?.name, err?.message].filter(Boolean).join(': ').slice(0, 120); };
 async function share() {
  const link = uploaded ? undefined : pin.source_url;
  let file = shareFile?.id === pin.id ? shareFile.file : null;
  try {
   if (!file && 'share' in navigator) { const blob = await boardMediaBlob(pin.image_url); file = new File([blob], filename(blob), {type: blob.type || 'image/jpeg'}); }
   if (file && navigator.canShare?.({files: [file]})) await navigator.share({files: [file], title: pin.title});
   else if ('share' in navigator && link) await navigator.share({title: pin.title, url: link});
   else if (link && navigator.clipboard) { await navigator.clipboard.writeText(link); status = 'Link to the original copied.'; }
   else status = 'Sharing is not available here. Use Download instead.';
  } catch (e) {
   const name = (e as Error)?.name;
   if (name === 'AbortError') return;
   console.warn('Share failed', e);
   // The tap's permission ran out while the image downloaded; it is ready now.
   if (name === 'NotAllowedError' && !shareFile && file) { shareFile = {id: pin.id, file}; status = 'Image ready. Tap Share again.'; return; }
   try { if (link && navigator.clipboard) { await navigator.clipboard.writeText(link); status = `Could not open the share sheet (${why(e)}). Link to the original copied instead.`; return; } } catch { /* fall through */ }
   status = `Could not share this image (${why(e)}). Use Download instead.`;
  }
 }
</script>

<svelte:window onkeydown={key} />
<Modal title={pin.title} size="full" portal {onClose}>
 <div class="viewer">
  <div class="stage" bind:this={stage} class:zoomed={scale > 1} role="img" aria-label={pin.title}
       onwheel={wheel} ondblclick={dbl} onpointerdown={(e) => { if (scale === 1) swipe = e.clientX; down(e); }}
       onpointermove={move} onpointerup={up} onpointercancel={up}>
   <div class="layer" style:transform="translate({x}px, {y}px) scale({scale})">
    {#key pin.id}<BoardImage url={pin.image_url} placeholder={pin.thumb_url ?? ''} alt={pin.title} animated={pin.media_type === 'gif'} width={pin.width} height={pin.height} eager fit />{/key}
   </div>
   {#if prev}<button class="nav left" aria-label="Previous image" onclick={(e) => { e.stopPropagation(); onNavigate(prev); }} onpointerdown={(e) => e.stopPropagation()}>‹</button>{/if}
   {#if next}<button class="nav right" aria-label="Next image" onclick={(e) => { e.stopPropagation(); onNavigate(next); }} onpointerdown={(e) => e.stopPropagation()}>›</button>{/if}
   <div class="zoom" role="group" aria-label="Zoom" onpointerdown={(e) => e.stopPropagation()}>
    <button aria-label="Zoom out" disabled={scale <= MIN} onclick={() => zoomTo(scale / 1.4)}>−</button>
    <button class="pct" aria-label="Fit to screen" onclick={() => zoomTo(1)}>{Math.round(scale * 100)}%</button>
    <button aria-label="Zoom in" disabled={scale >= MAX} onclick={() => zoomTo(scale * 1.4)}>+</button>
   </div>
   {#if index >= 0}<span class="count">{index + 1} / {pins.length}</span>{/if}
  </div>

  <aside class="side" bind:this={side}>
   <p class="where">{pin.board_name || boardName}{#if pin.width && pin.height} · {pin.width}×{pin.height}{/if}{#if pin.media_type === 'gif'} · GIF{/if}</p>
   <section class="why" aria-label="Why this image is here">
    {#if pin.vision_seen || pin.vision_why}
     <h3>Why your curator picked it</h3>
     {#if pin.vision_seen}<p><span class="label">Sees</span>{pin.vision_seen}</p>{/if}
     {#if pin.vision_why}<p><span class="label">Fits because</span>{pin.vision_why}</p>{/if}
    {:else if uploaded}<h3>Added by you</h3><p class="muted">You uploaded this image.</p>
    {:else}<h3>No curator notes</h3><p class="muted">Added before notes were kept, or saved by hand.</p>{/if}
    {#if pin.feedback === 'good'}<p class="verdict good">You confirmed it fits.</p>{/if}
    {#if pin.feedback_note}<p class="muted">Your note: {pin.feedback_note}</p>{/if}
   </section>

   <div class="primary">
    <button class="btn primary" class:saved={pin.saved} aria-pressed={pin.saved} disabled={busy} onclick={() => onSave(pin)}>{pin.saved ? '♥ Saved' : '♡ Save'}</button>
    <button class="btn" onclick={download}>Download</button>
    <button class="btn" onclick={() => onShare ? onShare(pin) : share()}>Share</button>
   </div>
   <div class="review" role="group" aria-label="Teach the curator">
    <button class="btn pill" class:on={pin.feedback === 'good'} aria-pressed={pin.feedback === 'good'} disabled={busy} onclick={() => onFits(pin)}>✓ Fits this board</button>
    <button class="btn pill" disabled={busy} onclick={() => onNotFit(pin)}>✕ Not a fit</button>
    <button class="btn pill" disabled={busy} title="Remove from this board without teaching the curator" onclick={() => onHide(pin)}>Remove</button>
   </div>
   {@render actions?.()}
   {#if status}<p class="status" role="status">{status}</p>{/if}
   <div class="credit">
    {#if pin.attribution}<p>{pin.attribution}{#if pin.license} · {pin.license}{/if}</p>{/if}
    {#if !uploaded}<a href={pin.source_url} target="_blank" rel="noopener noreferrer">View original source ↗</a>{/if}
   </div>
   <p class="hint muted">Scroll or pinch to zoom · double-click to toggle · ← → to browse</p>
  </aside>
 </div>
</Modal>

<style>
 .viewer{display:grid;grid-template-columns:minmax(0,1fr) 20rem;gap:1.2rem;height:min(80dvh,60rem)}
 .stage{position:relative;overflow:hidden;border-radius:var(--r-md);background:rgba(0,0,0,.35);display:grid;place-items:center;touch-action:none;user-select:none;cursor:zoom-in}
 .stage.zoomed{cursor:grab}
 .layer{width:100%;height:100%;display:grid;place-items:center;transform-origin:center;transition:transform .08s linear;will-change:transform}
 .layer :global(.media){width:100%;height:100%;display:grid;place-items:center}
 .layer :global(img){width:100%;height:100%;max-width:100%;max-height:min(80dvh,60rem);object-fit:contain}
 .nav{position:absolute;top:50%;transform:translateY(-50%);width:2.8rem;height:2.8rem;border-radius:50%;border:1px solid rgba(255,255,255,.2);background:rgba(10,10,20,.55);color:#fff;font-size:1.8rem;line-height:1;cursor:pointer;display:grid;place-items:center}
 .nav:hover{background:rgba(10,10,20,.8)}
 .nav.left{left:.7rem}.nav.right{right:.7rem}
 .zoom{position:absolute;bottom:.7rem;left:50%;transform:translateX(-50%);display:flex;gap:.2rem;padding:.25rem;border-radius:var(--r-pill);background:rgba(10,10,20,.6);backdrop-filter:blur(6px)}
 .zoom button{min-width:2.2rem;height:2.2rem;border:0;border-radius:var(--r-pill);background:transparent;color:#fff;font:inherit;cursor:pointer}
 .zoom button:hover:not(:disabled){background:rgba(255,255,255,.12)}
 .zoom button:disabled{opacity:.4}
 .zoom .pct{min-width:3.6rem;font-size:.8rem}
 .count{position:absolute;top:.7rem;left:.8rem;font-size:.8rem;color:#fff;background:rgba(10,10,20,.55);padding:.2rem .6rem;border-radius:var(--r-pill)}
 .side{display:flex;flex-direction:column;gap:1rem;overflow-y:auto;padding-right:.2rem}
 .where{margin:0;color:var(--muted);font-size:.85rem}
 .why{padding:.9rem 1rem;border:1px solid var(--line);border-radius:var(--r-md);background:rgba(255,255,255,.03)}
 .why h3{margin:0 0 .5rem;font-size:.95rem}
 .why p{margin:.35rem 0;line-height:1.45}
 .label{display:block;font-size:.7rem;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-bottom:.1rem}
 .verdict.good{color:#3ddc97}
 .primary,.review{display:flex;flex-wrap:wrap;gap:.5rem}
 .primary .btn{flex:1 1 auto}
 .btn.saved{background:linear-gradient(120deg,#ff8fab,#ffb547);color:#1b1020}
 .pill.on{border-color:rgba(61,220,151,.5);color:#3ddc97}
 .status{margin:0;font-size:.85rem;color:var(--muted)}
 .credit{font-size:.85rem;color:var(--muted)}
 .credit p{margin:0 0 .3rem}
 .credit a{color:var(--text)}
 .hint{font-size:.75rem;margin-top:auto}
 @media (max-width: 860px){
  .viewer{grid-template-columns:1fr;grid-template-rows:minmax(45dvh,1fr) auto;height:auto}
  .stage{min-height:45dvh}
  .layer :global(img){max-height:60dvh}
  .side{overflow:visible}
  .hint{display:none}
 }
</style>
