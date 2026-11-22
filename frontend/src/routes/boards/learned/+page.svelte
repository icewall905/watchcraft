<script lang="ts">
 import { onMount, tick } from 'svelte';
 import { page } from '$app/state';
 import { goto } from '$app/navigation';
 import { ApiError } from '$lib/api';
 import { session } from '$lib/stores/session.svelte';
 import { boardsApi, type BoardLearned, type LearnedIndex, type LearnedStatement, type LearnedChat } from '$lib/hub/boards';

 let index = $state<LearnedIndex | null>(null), data = $state<BoardLearned | null>(null);
 let selected = $state<number | null>(null), loading = $state(false), error = $state(''), busy = $state(''), notice = $state('');
 let briefEditing = $state(false), briefDraft = $state('');
 let addText = $state(''), addPolarity = $state<1 | -1>(-1);
 let editSid = $state<string | null>(null), editText = $state('');
 let chatDraft = $state(''), thinking = $state(false), thread = $state<HTMLElement>();
 let refiningAll = $state<{done: number; total: number} | null>(null);

 const msg = (e: unknown) => e instanceof Error ? e.message : 'Something went wrong.';
 const allowed = () => session.boardsAllowed && session.ready && !session.needsLogin && !session.choosing && !session.profile?.pin_locked;
 const BRIEF_LABEL = {none: 'Not written yet', fresh: 'Up to date', stale: 'Newer learnings to fold in'} as const;
 const CONF = {tentative: 'guess', established: 'learned', explicit: 'you said'} as const;
 const date = (t: number) => t ? new Date(t * 1000).toLocaleString(undefined, {dateStyle: 'medium', timeStyle: 'short'}) : '';
 const goodReviews = $derived(data?.reviews.filter(r => r.verdict === 'good') ?? []);
 const badReviews = $derived(data?.reviews.filter(r => r.verdict === 'bad') ?? []);
 const flagged = $derived(new Map((data?.brief.dropped ?? []).map(d => [d.sid, d.why])));
 const boardOnly = $derived(data?.statements.filter(s => s.scope === 'board') ?? []);
 const shared = $derived(data?.statements.filter(s => s.scope === 'all') ?? []);

 async function loadIndex() {
  if (!allowed()) return;
  loading = true; error = '';
  try {
   index = await boardsApi.learnedIndex();
   const want = Number(page.url.searchParams.get('board')) || null;
   const pick = index.boards.find(b => b.id === want)?.id ?? index.boards[0]?.id ?? null;
   if (pick) await choose(pick, false);
  } catch (e) { error = msg(e); } finally { loading = false; }
 }
 async function choose(id: number, push = true) {
  selected = id; data = null; briefEditing = false; editSid = null; error = ''; notice = '';
  if (push) void goto(`/boards/learned?board=${id}`, {replaceState: true, keepFocus: true, noScroll: true});
  try { const d = await boardsApi.learned(id); if (selected === id) data = d; } catch (e) { error = msg(e); }
  await scrollChat();
 }
 function refreshIndexRow(d: BoardLearned) {
  if (!index) return;
  const own = d.statements.filter(s => s.scope === 'board').length;
  index.boards = index.boards.map(b => b.id === d.board.id ? {...b, statements: own, brief: d.brief.state, flagged: d.brief.dropped.length, fits: d.review_counts.good, not_fits: d.review_counts.bad} : b);
  index.shared = d.statements.filter(s => s.scope === 'all');
 }
 async function act(key: string, fn: () => Promise<BoardLearned>, done = '') {
  if (busy || !selected) return;
  const id = selected;
  busy = key; error = ''; notice = '';
  try { const d = await fn(); if (selected === id) { data = d; refreshIndexRow(d); notice = done; } }
  catch (e) { error = msg(e); } finally { busy = ''; }
 }
 const refine = () => act('refine', () => boardsApi.refineBrief(selected!), 'Brief rewritten from everything the curator learned.');
 async function refineAll() {
  if (!index || refiningAll) return;
  const ids = index.boards.map(b => b.id);
  refiningAll = {done: 0, total: ids.length}; error = '';
  const failed: string[] = [];
  for (const id of ids) {
   try { const d = await boardsApi.refineBrief(id); refreshIndexRow(d); if (selected === id) data = d; }
   catch { failed.push(index.boards.find(b => b.id === id)?.name ?? `#${id}`); }
   refiningAll = {done: refiningAll.done + 1, total: ids.length};
  }
  refiningAll = null;
  if (failed.length) error = `Could not refine: ${failed.join(', ')}. Try again later.`; else notice = 'Every board brief is rewritten.';
 }
 function startBrief() { briefDraft = data?.brief.text ?? ''; briefEditing = true; }
 const saveBrief = () => act('brief', () => boardsApi.saveBrief(selected!, briefDraft), 'Brief saved. The curator uses your words from the next search.').then(() => { if (!error) briefEditing = false; });
 const clearBrief = () => act('brief', () => boardsApi.saveBrief(selected!, ''), 'Brief cleared. The curator reads the learned items directly until a new brief is written.');
 const add = (e: SubmitEvent) => { e.preventDefault(); const text = addText.trim(); if (text.length < 3) return; void act('add', () => boardsApi.learnAdd(selected!, text, addPolarity), 'Added.').then(() => { if (!error) addText = ''; }); };
 const remove = (st: LearnedStatement) => act(`rm-${st.sid}`, () => boardsApi.learnEdit(selected!, st.sid, {remove: true}), st.scope === 'all' ? 'Removed from all your boards. It will not be learned again.' : 'Removed. It will not be learned again.');
 const notHere = (st: LearnedStatement) => act(`off-${st.sid}`, () => boardsApi.learnEdit(selected!, st.sid, {scope: 'off'}), 'This board no longer uses it; your other boards still do.');
 const onlyHere = (st: LearnedStatement) => act(`here-${st.sid}`, () => boardsApi.learnEdit(selected!, st.sid, {scope: 'board'}), 'Now applies to this board only.');
 const flip = (st: LearnedStatement) => act(`flip-${st.sid}`, () => boardsApi.learnEdit(selected!, st.sid, {polarity: st.polarity === 1 ? -1 : 1}));
 function startEdit(st: LearnedStatement) { editSid = st.sid; editText = st.text; }
 const saveEdit = (st: LearnedStatement) => act(`ed-${st.sid}`, () => boardsApi.learnEdit(selected!, st.sid, {text: editText.trim()})).then(() => { if (!error) editSid = null; });
 const rejectPending = (tid: number) => act(`t-${tid}`, () => boardsApi.learnReject(selected!, tid), 'Discarded. It will not be learned.');
 async function undoReview(pin: number) {
  if (busy || !selected) return;
  busy = `r-${pin}`; error = '';
  try { await boardsApi.review(selected, pin, {verdict: ''}); data = await boardsApi.learned(selected); refreshIndexRow(data); notice = 'Review removed. A picture you marked Not a fit is back on the board.'; }
  catch (e) { error = msg(e); } finally { busy = ''; }
 }
 async function scrollChat() { await tick(); thread?.scrollTo({top: thread.scrollHeight}); }
 async function send(e?: SubmitEvent) {
  e?.preventDefault();
  const text = chatDraft.trim(); if (!text || thinking || !selected) return;
  const id = selected;
  thinking = true; error = '';
  try { const r = await boardsApi.learnChat(id, text); if (selected === id) { data = r; refreshIndexRow(r); chatDraft = ''; } }
  catch (err) { error = msg(err); } finally { thinking = false; void scrollChat(); }
 }
 async function decide(m: LearnedChat, accept: boolean) {
  if (busy || !selected) return;
  const id = selected;
  busy = `c-${m.id}`; error = '';
  try { const d = accept ? await boardsApi.learnAccept(id, m.id) : await boardsApi.learnDiscard(id, m.id); if (selected === id) { data = d; refreshIndexRow(d); } }
  catch (err) {
   if (err instanceof ApiError && err.status === 409 && data) data = {...data, chat: data.chat.map(x => x.id === m.id ? {...x, proposal_status: 'stale'} : x)};
   else error = msg(err);
  } finally { busy = ''; }
 }
 onMount(() => { void loadIndex(); });
 let seen = '';
 $effect(() => { const key = `${session.profileId}:${session.version}:${allowed()}`; if (key !== seen) { const first = !seen; seen = key; if (!first) { index = null; data = null; selected = null; void loadIndex(); } } });
</script>

<svelte:head><title>What your boards learned</title></svelte:head>

<div class="learned">
 <header class="top">
  <a class="btn pill back" href={selected ? `/boards?board=${selected}` : '/boards'}>← Boards</a>
  <div>
   <h1>What your board curators learned</h1>
   <p class="muted">Everything each board's curator believes about your taste, the brief it follows, and the reviews it learned from. Fix anything that's wrong here — or just tell it.</p>
  </div>
  {#if index?.boards.length}
   <button class="btn primary" disabled={!!refiningAll || !!busy} onclick={refineAll} title="Rewrite every board's brief from everything it learned">
    {refiningAll ? `Refining ${refiningAll.done + 1} of ${refiningAll.total}…` : 'Refine all briefs'}
   </button>
  {/if}
 </header>

 {#if error}<p class="feedback error" role="alert">{error}<button class="dismiss" aria-label="Dismiss" onclick={() => { error = ''; }}>×</button></p>{/if}
 {#if notice}<p class="feedback good" role="status">{notice}<button class="dismiss" aria-label="Dismiss" onclick={() => { notice = ''; }}>×</button></p>{/if}

 {#if loading && !index}<p class="muted">Loading…</p>
 {:else if index && !index.boards.length}<div class="empty"><h2>No boards of your own yet</h2><p class="muted">Create a board and review a few pictures; what its curator learns shows up here.</p><a class="btn primary" href="/boards">Go to Boards</a></div>
 {:else if index}
  <nav class="chips" aria-label="Boards">
   {#each index.boards as b (b.id)}
    <button class="chip" class:on={b.id === selected} aria-pressed={b.id === selected} onclick={() => choose(b.id)}>
     <span class="chip-name">{b.name}</span>
     <span class="chip-meta">{b.statements} learned · {b.fits}👍 {b.not_fits}👎</span>
     {#if b.flagged}<span class="dot warn" title="{b.flagged} possible mistake{b.flagged === 1 ? '' : 's'}">{b.flagged}</span>
     {:else if b.brief === 'stale'}<span class="dot" title="Brief has newer learnings to fold in">•</span>{/if}
    </button>
   {/each}
  </nav>

  {#if !data}<p class="muted">Loading board…</p>
  {:else}
   <section class="card purpose">
    <h2>{data.board.name}</h2>
    <dl>
     <dt>About</dt><dd>{data.board.interests}</dd>
     {#if data.board.instructions}<dt>Your instructions</dt><dd class="pre">{data.board.instructions}</dd>{/if}
     {#if data.board.avoid}<dt>Avoid list</dt><dd>{data.board.avoid}</dd>{/if}
    </dl>
    <p class="small muted">Change these with the board's ⚙ Edit curator button. They always win over anything learned.</p>
   </section>

   <div class="grid">
    <div class="col">
     <section class="card brief">
      <div class="row-head">
       <h2>Curator brief</h2>
       <span class="badge {data.brief.state}">{BRIEF_LABEL[data.brief.state]}</span>
      </div>
      <p class="small muted">One prompt compiled from your instructions, everything learned and your reviews. The curator reads it on every search. It is rewritten each night when something new was learned{data.brief.edited_at ? ', keeping your own edits' : ''}.</p>
      {#if briefEditing}
       <textarea class="input brief-text" rows="12" maxlength="3000" aria-label="Curator brief" bind:value={briefDraft}></textarea>
       <div class="hstack"><button class="btn primary" disabled={!!busy} onclick={saveBrief}>{busy === 'brief' ? 'Saving…' : 'Save brief'}</button><button class="btn" onclick={() => { briefEditing = false; }}>Cancel</button></div>
      {:else if data.brief.text}
       <pre class="brief-text">{data.brief.text}</pre>
       <p class="small muted">{data.brief.edited_at ? `Edited by you ${date(data.brief.edited_at)}` : `Written ${date(data.brief.compiled_at)}`}</p>
      {:else}
       <p class="muted">No brief yet. Until there is one, the curator reads the learned items below one by one.</p>
      {/if}
      {#if data.brief.error}<p class="small err">{data.brief.error}</p>{/if}
      {#if !briefEditing}
       <div class="hstack wrap">
        <button class="btn primary" disabled={!!busy || !!refiningAll} onclick={refine}>{busy === 'refine' ? 'Refining… (up to a few minutes)' : 'Refine master prompt now'}</button>
        <button class="btn" disabled={!!busy} onclick={startBrief}>{data.brief.text ? 'Edit' : 'Write my own'}</button>
        {#if data.brief.text}<button class="btn ghost" disabled={!!busy} onclick={clearBrief}>Clear</button>{/if}
       </div>
      {/if}
      {#if data.brief.dropped.length}
       <div class="flags">
        <h3>Possible mistakes</h3>
        <p class="small muted">While writing the brief, the curator judged these learned items off-topic for this board and left them out.</p>
        <ul>
         {#each data.brief.dropped as d (d.sid)}
          {@const st = data.statements.find(s => s.sid === d.sid)}
          {#if st}
           <li><strong>{st.polarity === 1 ? 'More' : 'Less'}: {st.text}</strong><span class="small muted">{d.why}</span>
            <div class="hstack"><button class="btn small" disabled={!!busy} onclick={() => remove(st)}>Remove it</button>{#if st.scope === 'all'}<button class="btn small ghost" disabled={!!busy} onclick={() => notHere(st)}>Not on this board</button>{/if}</div>
           </li>
          {/if}
         {/each}
        </ul>
       </div>
      {/if}
     </section>

     <section class="card">
      <h2>Learned about this board</h2>
      {#snippet item(st: LearnedStatement)}
       <li class="st" class:less={st.polarity === -1} class:flag={flagged.has(st.sid)}>
        {#if editSid === st.sid}
         <form class="hstack wrap" onsubmit={(e) => { e.preventDefault(); void saveEdit(st); }}>
          <input class="input grow" maxlength="300" minlength="3" aria-label="Edit learned item" bind:value={editText} />
          <button class="btn small primary" disabled={!!busy || editText.trim().length < 3}>Save</button><button type="button" class="btn small" onclick={() => { editSid = null; }}>Cancel</button>
         </form>
        {:else}
         <div class="st-text"><button class="tag {st.polarity === 1 ? 'more' : 'less-tag'}" title="Switch between more and less" disabled={!!busy} onclick={() => flip(st)}>{st.polarity === 1 ? 'More' : 'Less'}</button> {st.text}</div>
         <div class="st-meta small muted">{CONF[st.confidence]}{st.scope === 'all' ? ' · all your boards' : ''}{flagged.has(st.sid) ? ' · flagged as off-topic' : ''}</div>
         <div class="st-actions">
          <button class="link-btn" onclick={() => startEdit(st)}>Edit</button>
          {#if st.scope === 'all'}<button class="link-btn" disabled={!!busy} onclick={() => notHere(st)}>Not on this board</button><button class="link-btn" disabled={!!busy} onclick={() => onlyHere(st)}>Only this board</button>{/if}
          <button class="link-btn danger" disabled={!!busy} onclick={() => remove(st)}>Remove</button>
         </div>
        {/if}
       </li>
      {/snippet}
      {#if boardOnly.length}<ul class="list">{#each boardOnly as st (st.sid)}{@render item(st)}{/each}</ul>
      {:else}<p class="muted small">Nothing learned for this board yet. Mark pictures Fits or Not a fit and the nightly pass learns from them.</p>{/if}
      {#if shared.length}
       <h3>Also applied from all your boards</h3>
       <ul class="list">{#each shared as st (st.sid)}{@render item(st)}{/each}</ul>
      {/if}
      <form class="add" onsubmit={add}>
       <select class="input" aria-label="More or less" bind:value={addPolarity}><option value={1}>More of</option><option value={-1}>Less of</option></select>
       <input class="input grow" maxlength="300" placeholder="e.g. baby or nursery items" aria-label="New learned item" bind:value={addText} />
       <button class="btn primary" disabled={!!busy || addText.trim().length < 3}>Add</button>
      </form>
     </section>

     {#if data.pending.length}
      <section class="card">
       <h2>Waiting to be learned tonight</h2>
       <ul class="list">
        {#each data.pending as t (t.id)}
         <li class="st"><div class="st-text"><span class="tag {t.polarity > 0 ? 'more' : 'less-tag'}">{t.polarity > 0 ? 'More' : 'Less'}</span> {t.text}</div><div class="st-meta small muted">{t.reason}</div>
          <div class="st-actions"><button class="link-btn danger" disabled={!!busy} onclick={() => rejectPending(t.id)}>Wrong — don't learn this</button></div></li>
        {/each}
       </ul>
      </section>
     {/if}
    </div>

    <div class="col">
     <section class="card chat">
      <h2>Tell it what's wrong</h2>
      <ol class="thread" bind:this={thread} aria-live="polite">
       {#each data.chat as m (m.id)}
        <li class="msg {m.role}"><p>{m.text}</p>
         {#if m.proposal.ops?.length}
          <div class="proposal">
           <ul class="ops">
            {#each m.proposal.ops as op, i (i)}
             {#if op.op === 'add'}<li class="op-add">+ {op.polarity === 1 ? 'more of' : 'less of'}: {op.text}</li>
             {:else if op.op === 'edit'}<li class="op-edit">~ <del>{op.before}</del> → {op.text}</li>
             {:else if op.op === 'scope'}<li class="op-edit">↷ stop using here (keep on other boards): {op.before}</li>
             {:else}<li class="op-remove">− remove: {op.before}</li>{/if}
            {/each}
           </ul>
           {#if m.proposal_status === 'pending'}
            <div class="hstack"><button class="btn small primary" disabled={!!busy} onclick={() => decide(m, true)}>Apply</button><button class="btn small ghost" disabled={!!busy} onclick={() => decide(m, false)}>Discard</button></div>
           {:else}<p class="small status {m.proposal_status}">{m.proposal_status === 'accepted' ? 'Applied' : m.proposal_status === 'stale' ? 'Things changed since — ask again.' : 'Discarded'}</p>{/if}
          </div>
         {/if}
        </li>
       {:else}<li class="small muted">Say it plainly — "stop finding crochet and nursery things", "only things you can wear" — and it suggests a change for you to apply.</li>{/each}
       {#if thinking}<li class="msg curator small muted" role="status">Thinking…</li>{/if}
      </ol>
      <form class="stack" onsubmit={send}>
       <textarea class="input" rows="3" maxlength="2000" placeholder="What is it getting wrong on this board?" aria-label="Message to this board's curator" bind:value={chatDraft}
        onkeydown={(e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); void send(); } }}></textarea>
       <div><button class="btn primary" disabled={thinking || !chatDraft.trim()}>{thinking ? 'Thinking…' : 'Send'}</button></div>
      </form>
     </section>

     <section class="card">
      <h2>Your reviews <span class="muted small">({data.review_counts.good} fits · {data.review_counts.bad} not a fit, latest 120)</span></h2>
      <p class="small muted">The curator learns from these, and recent ones are shown to it as examples. Undo any you no longer agree with.</p>
      {#each [{label: 'Fits', rows: goodReviews}, {label: 'Not a fit', rows: badReviews}] as group (group.label)}
       {#if group.rows.length}
        <h3>{group.label}</h3>
        <ul class="reviews">
         {#each group.rows.slice(0, 40) as r (r.pin_id)}
          <li>
           {#if r.thumb_url}<img src={r.thumb_url} alt="" loading="lazy" />{:else}<span class="noimg" aria-hidden="true"></span>{/if}
           <div class="rv"><span class="rv-title">{r.picture || r.title || 'Untitled image'}</span>{#if r.note}<span class="small">“{r.note}”</span>{/if}
            <button class="link-btn" disabled={!!busy} onclick={() => undoReview(r.pin_id)}>{busy === `r-${r.pin_id}` ? 'Undoing…' : 'Undo review'}</button></div>
          </li>
         {/each}
        </ul>
       {/if}
      {:else}<p class="muted small">No reviews yet.</p>{/each}
     </section>

     {#if data.hosts.length}
      <section class="card">
       <h2>Sources held back</h2>
       <ul class="hosts">{#each data.hosts as h (h.host)}<li><strong>{h.host}</strong> <span class="small muted">{h.reason}{h.good || h.bad ? ` · ${h.good} kept, ${h.bad} rejected` : ''}</span></li>{/each}</ul>
      </section>
     {/if}

     <details class="card">
      <summary>Exact instructions the image judge reads</summary>
      <pre class="prompt">{data.prompt}</pre>
     </details>
    </div>
   </div>
  {/if}
 {/if}
</div>

<style>
 .learned { max-width: 1200px; margin: 0 auto; padding: 1rem 16px 3rem; display: grid; gap: 1rem; }
 .top { display: grid; grid-template-columns: auto 1fr auto; gap: 1rem; align-items: start; }
 .top h1 { margin: 0 0 0.25rem; font-size: 1.6rem; }
 .top p { margin: 0; max-width: 60ch; }
 @media (max-width: 720px) { .top { grid-template-columns: 1fr; } }
 .chips { display: flex; gap: 0.5rem; overflow-x: auto; padding-bottom: 0.25rem; }
 .chip { display: grid; grid-template-columns: 1fr auto; gap: 0 0.5rem; text-align: left; padding: 0.5rem 0.8rem; border-radius: var(--r-md, 10px); border: 1px solid var(--line); background: var(--surface); color: inherit; cursor: pointer; min-width: 10rem; }
 .chip.on { border-color: var(--accent, currentColor); box-shadow: inset 0 0 0 1px var(--accent, currentColor); }
 .chip-name { font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
 .chip-meta { grid-column: 1; font-size: 0.8rem; opacity: 0.7; }
 .dot { grid-row: 1 / span 2; grid-column: 2; align-self: center; min-width: 1.4rem; height: 1.4rem; border-radius: 999px; display: grid; place-items: center; font-size: 0.75rem; background: var(--surface-2); }
 .dot.warn { background: var(--warn); color: #000; font-weight: 700; }
 .grid { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr); gap: 1rem; align-items: start; }
 @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
 .col { display: grid; gap: 1rem; }
 .card { padding: 1rem; border: 1px solid var(--line); border-radius: var(--r-lg, 14px); background: var(--surface); display: grid; gap: 0.6rem; min-width: 0; }
 .card h2 { margin: 0; font-size: 1.15rem; }
 .card h3 { margin: 0.4rem 0 0; font-size: 0.95rem; }
 .purpose dl { margin: 0; display: grid; grid-template-columns: max-content 1fr; gap: 0.3rem 1rem; }
 .purpose dt { font-weight: 600; opacity: 0.75; }
 .purpose dd { margin: 0; overflow-wrap: anywhere; }
 @media (max-width: 560px) { .purpose dl { grid-template-columns: 1fr; } }
 .pre { white-space: pre-line; }
 .row-head { display: flex; justify-content: space-between; align-items: center; gap: 0.5rem; flex-wrap: wrap; }
 .badge { font-size: 0.8rem; padding: 0.15rem 0.6rem; border-radius: 999px; background: var(--surface-2); }
 .badge.fresh { color: var(--ok); }
 .badge.stale { color: var(--warn); }
 .brief-text { margin: 0; white-space: pre-wrap; font-family: inherit; line-height: 1.5; padding: 0.75rem; border-radius: var(--r-md, 10px); background: var(--surface-2); overflow-wrap: anywhere; }
 textarea.brief-text { width: 100%; min-height: 14rem; }
 .hstack { display: flex; gap: 0.5rem; align-items: center; }
 .wrap { flex-wrap: wrap; }
 .stack { display: grid; gap: 0.5rem; }
 .grow { flex: 1; min-width: 0; }
 .flags { border: 1px solid var(--warn); border-radius: var(--r-md, 10px); padding: 0.75rem; display: grid; gap: 0.4rem; }
 .flags ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 0.6rem; }
 .flags li { display: grid; gap: 0.25rem; }
 .list { list-style: none; margin: 0; padding: 0; display: grid; gap: 0.4rem; }
 .st { padding: 0.5rem 0.6rem; border-radius: var(--r-sm, 8px); background: var(--surface-2); display: grid; gap: 0.2rem; overflow-wrap: anywhere; }
 .st.flag { outline: 1px dashed var(--warn); }
 .st-actions { display: flex; gap: 0.75rem; flex-wrap: wrap; }
 .tag { font-size: 0.75rem; font-weight: 700; padding: 0.05rem 0.45rem; border-radius: 999px; border: 0; cursor: pointer; vertical-align: 1px; }
 span.tag { cursor: default; }
 .tag.more { background: color-mix(in srgb, var(--ok) 25%, transparent); color: var(--ok); }
 .tag.less-tag { background: color-mix(in srgb, var(--bad) 22%, transparent); color: var(--bad); }
 .link-btn { background: none; border: 0; padding: 0; color: var(--accent, inherit); cursor: pointer; font: inherit; font-size: 0.85rem; text-decoration: underline; }
 .link-btn.danger { color: var(--bad); }
 .add { display: flex; gap: 0.5rem; flex-wrap: wrap; }
 .thread { list-style: none; margin: 0; padding: 0; display: grid; gap: 0.6rem; max-height: min(50vh, 28rem); overflow-y: auto; align-content: start; }
 .msg { max-width: 92%; padding: 0.55rem 0.8rem; border-radius: var(--r-md, 10px); background: var(--surface-2); overflow-wrap: anywhere; }
 .msg p { margin: 0; white-space: pre-line; }
 .msg.user { justify-self: end; background: var(--surface-hi, var(--surface-2)); }
 .proposal { display: grid; gap: 0.5rem; margin-top: 0.5rem; padding: 0.6rem; border-radius: var(--r-sm, 8px); border: 1px solid var(--line-strong, var(--line)); }
 .ops { list-style: none; margin: 0; padding: 0; display: grid; gap: 0.3rem; font-size: 0.9rem; }
 .op-add { color: var(--ok); } .op-edit { color: var(--warn); } .op-remove { color: var(--bad); }
 .status { margin: 0; font-weight: 600; } .status.accepted { color: var(--ok); } .status.stale { color: var(--warn); }
 .reviews { list-style: none; margin: 0; padding: 0; display: grid; gap: 0.5rem; }
 .reviews li { display: grid; grid-template-columns: 56px 1fr; gap: 0.6rem; align-items: start; }
 .reviews img, .noimg { width: 56px; height: 56px; object-fit: cover; border-radius: var(--r-sm, 8px); background: var(--surface-2); display: block; }
 .rv { display: grid; gap: 0.15rem; min-width: 0; overflow-wrap: anywhere; }
 .rv-title { font-size: 0.9rem; }
 .hosts { list-style: none; margin: 0; padding: 0; display: grid; gap: 0.3rem; }
 summary { cursor: pointer; font-weight: 600; }
 .prompt { white-space: pre-wrap; font-size: 0.8rem; max-height: 30rem; overflow: auto; margin: 0; overflow-wrap: anywhere; }
 .small { font-size: 0.85rem; }
 .err { color: var(--bad); }
 .empty { text-align: center; padding: 3rem 1rem; display: grid; gap: 0.75rem; justify-items: center; }
 .feedback { display: flex; justify-content: space-between; gap: 1rem; padding: 0.6rem 0.9rem; border-radius: var(--r-md, 10px); background: var(--surface-2); margin: 0; }
 .feedback.error { color: var(--bad); }
 .feedback.good { color: var(--ok); }
 .dismiss { background: none; border: 0; color: inherit; cursor: pointer; font-size: 1.1rem; }
</style>
