<script lang="ts">
 import { session } from '$lib/stores/session.svelte';
 import { request } from '$lib/api';
 import { focusable } from '$lib/nav';
 import { ago } from './api';
 import type { Team, TeamAgent } from './types';
 import Row from '$lib/components/Row.svelte';
 let team = $state<Team | null>(null);
 let unavailable = $state(false);
 const working = $derived(team?.agents.filter(a => a.status === 'running' || a.status === 'waiting').slice(0, 3) ?? []);
 const findings = $derived((team?.agents.flatMap(a => (a.finds ?? []).map(f => ({ ...f, agent: a }))) ?? []).sort((a, b) => b.found_at - a.found_at).filter((f, i, all) => all.findIndex(x => x.id === f.id) === i).slice(0, 3));
 const agentHref = (a: TeamAgent) => a.kind === 'scout' ? '/sources' : a.kind === 'briefing' ? '/briefings' : a.role === 'research' ? `/research/${a.id}` : `/curators/${a.id}`;
 $effect(() => {
  const profileId = session.profileId;
  const allowed = session.hubAllowed && session.profile?.prefs?.scouts_home === true;
  team = null; unavailable = false;
  if (!allowed || !profileId) return;
  let stopped = false;
  let timer: ReturnType<typeof setTimeout>;
  const controller = new AbortController();
  const refresh = async () => {
   if (document.visibilityState === 'visible') {
    try { const result = await request<Team>('/hub/team', { signal: controller.signal, toast: false }); if (!stopped && session.profileId === profileId) { team = result; unavailable = false; } }
    catch { if (!stopped) unavailable = true; }
   }
   if (!stopped) timer = setTimeout(refresh, 30000);
  };
  void refresh();
  return () => { stopped = true; controller.abort(); clearTimeout(timer); };
 });
</script>
{#if session.hubAllowed && session.profile?.prefs?.scouts_home === true && (team || unavailable)}
 <Row title="Your scouts" emoji="🔭" color="var(--accent)">
  {#if working.length}
   {#each working as a (a.key)}
    <a class="scout-card" href={agentHref(a)} use:focusable={{ group: 'scouts-home' }}><span class="status">{a.status === 'running' ? 'Working now' : 'Waiting for a slot'}</span><strong>{a.icon} {a.name}</strong><span>{a.doing || a.mission || 'Getting ready to work.'}</span></a>
   {/each}
  {:else if findings.length}
   {#each findings as f (f.id)}
    <a class="scout-card" href="/item/{f.id}" use:focusable={{ group: 'scouts-home' }}><span class="status">Recent discovery · {ago(f.found_at)}</span><strong>{f.title}</strong><span>Found by {f.agent.name}</span></a>
   {/each}
  {:else}
   <div class="scout-card empty"><strong>{unavailable && !team ? 'Activity is unavailable' : 'Your scouts are taking a break'}</strong><span>{unavailable && !team ? 'Open Agent Activity to try again.' : 'Recent discoveries will appear here after your scouts find something.'}</span></div>
  {/if}
  <div class="scout-links"><a class="btn" href="/team" use:focusable={{ group: 'scouts-home' }}>View activity</a><a class="btn" href="/scout-report" use:focusable={{ group: 'scouts-home' }}>Scout Report</a></div>
 </Row>
{/if}
<style>
 .scout-card { flex: none; width: min(19rem, 75vw); min-height: 9rem; display: grid; align-content: start; gap: .55rem; padding: 1.1rem; border: 1px solid var(--line-strong); border-radius: var(--r-md); background: var(--surface); color: var(--text); }
 .scout-card strong { font-size: 1.05rem; }
 .scout-card span:last-child { color: var(--muted); line-height: 1.4; overflow-wrap: anywhere; }
 .status { color: var(--accent); font-size: .8rem; font-weight: 700; }
 .scout-links { flex: none; display: grid; align-content: center; gap: .6rem; }
 .empty { width: min(24rem, 75vw); }
</style>
