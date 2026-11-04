<script lang="ts">
 import { onMount } from 'svelte';
 import { focusable } from '$lib/nav';
 import { api } from '$lib/api';
 import { session } from '$lib/stores/session.svelte';
 import type { SubtitleStats } from '$lib/types';
 let data = $state<SubtitleStats | null>(null);
 let error = $state(false);
 let mounted = false;
 let generation = 0;
 let active: Promise<void> | null = null;
 let identity = '';
 const key = () => `${session.profileId}:${session.version}:${session.isAdmin}:${session.profile?.pin_locked}:${session.needsLogin}`;
 async function refresh() {
  if (!mounted || document.hidden || session.profileId == null || session.profile?.pin_locked || session.needsLogin) return;
  if (active) return active;
  const captured = key(); const seq = generation;
  const task = (async () => {
   try { const result = await api.subtitleStats(); if (mounted && seq === generation && captured === key()) { data = result; error = false; } }
   catch { if (mounted && seq === generation && captured === key()) error = true; }
  })();
  active = task;
  await task;
  if (active === task) active = null;
 }
 $effect(() => {
  const current = key();
  if (identity !== current) { identity = current; generation++; data = null; error = false; active = null; if (mounted) void refresh(); }
 });
 onMount(() => {
  mounted = true; void refresh();
  const tick = setInterval(() => void refresh(), 10000);
  const visible = () => { if (!document.hidden) void refresh(); };
  document.addEventListener('visibilitychange', visible);
  return () => { mounted = false; generation++; clearInterval(tick); document.removeEventListener('visibilitychange', visible); };
 });
 const labels = {ready: 'Captioned', pending: 'Queued', running: 'Generating', retry_waiting: 'Waiting to retry', failed: 'Failed', missing: 'Awaiting queue'};
</script>
<section class="panel stack" aria-label="Reel subtitles">
 <div class="hstack"><h2>Reel subtitles</h2><button class="chip" use:focusable={{group:'stats-subtitles', onSelect: () => void refresh()}}>↻ Refresh subtitles</button></div>
 {#if error}<p role="status" class="muted">Subtitle statistics could not refresh.{data ? ' Showing the last update.' : ' Try Refresh subtitles.'}</p>{/if}
 {#if data}
  <p class="muted">{data.scope === 'household' ? 'All profiles' : 'Your feeds'} · Unwatched Reels stored locally, counted once per clip.</p>
  <div class="coverage"><strong>{data.coverage.ready} / {data.eligible} captioned</strong><span>{data.eligible ? Math.floor(data.coverage.ready / data.eligible * 100) + '%' : 'No eligible clips'}</span></div>
  <progress aria-label="Local Reel subtitle coverage" max={Math.max(1, data.eligible)} value={data.coverage.ready}></progress>
  <div class="counts">{#each Object.entries(data.coverage) as [state, count]}<div><strong>{count}</strong><span>{labels[state as keyof typeof labels]}</span></div>{/each}</div>
  <p class="small muted">STT {data.service.stt_enabled ? 'enabled' : 'not configured'} · {data.service.heartbeat_fresh ? 'Background service heartbeat is fresh' : 'Background service heartbeat is missing or stale'}{#if data.service.heartbeat_age_s !== null} ({Math.round(data.service.heartbeat_age_s)} seconds ago){/if}. Updates every 10 seconds while visible.</p>
  {#if data.active.length}<h3>Generating now</h3>{#each data.active as job}<div class="stack job"><a href="/watch/{job.video_id}" use:focusable={{group:'stats-subtitles'}}>{job.title}</a><progress aria-label="Subtitle progress for {job.title}" max="100" value={job.progress}></progress><span class="small muted">{job.progress}%</span></div>{/each}{/if}
  {#if data.recent_failures.length}<h3>Recent failures</h3>{#each data.recent_failures as job}<p class="small"><a href="/watch/{job.video_id}" use:focusable={{group:'stats-subtitles'}}>{job.title}</a> · {job.error}</p>{/each}{/if}
  <p class="small faint">Other subtitle jobs outside this Reel collection: {data.other_jobs.pending} queued · {data.other_jobs.running} generating · {data.other_jobs.retry_waiting} waiting to retry · {data.other_jobs.failed} failed.</p>
 {:else if !error}<p class="muted">Loading subtitle progress…</p>{/if}
</section>
<style>
 .coverage { display:flex; justify-content:space-between; gap:1rem; }
 progress {width:100%; accent-color:var(--accent);height:.75rem;}
 .counts {display:grid;grid-template-columns:repeat(auto-fit,minmax(7rem,1fr));gap:1rem;}
 .counts div {display:grid;gap:.2rem;} .counts strong{font-size:1.6rem;} .counts span{color:var(--muted);font-size:.85rem;}
 .job{gap:.3rem;} a{text-decoration:underline;text-underline-offset:.2rem;overflow-wrap:anywhere;}
</style>
