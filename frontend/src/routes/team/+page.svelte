<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import { focusable } from '$lib/nav';
	import { agent, STATE_LABEL, cleanDetail } from '$lib/stores/agent.svelte';
	import { session } from '$lib/stores/session.svelte';
	import { hub, ago } from '$lib/hub/api';
	import { errMsg } from '$lib/hub/ui';
	import ErrorBox from '$lib/hub/ErrorBox.svelte';
	import AgentGlyph from '$lib/components/AgentGlyph.svelte';
	import type { Team, TeamAgent, TeamEntry, TeamStatus } from '$lib/hub/types';

	let team = $state<Team | null>(null);
	let error = $state('');
	let loading = $state(true);
	let now = $state(Date.now() / 1000);

	async function load() {
		try {
			team = await hub.team();
			error = '';
		} catch (e) {
			error = errMsg(e);
		} finally {
			loading = false;
		}
	}
	onMount(() => {
		load();
		api.agentStatus().then((s) => { if (s.downloader) agent.downloader = s.downloader; }).catch(() => {});
		// Poll faster while someone is working; the page never moves focus or scroll.
		let t: ReturnType<typeof setTimeout>;
		const tick = async () => {
			await load();
			t = setTimeout(tick, team && (team.running || team.waiting) ? 4000 : 15000);
		};
		t = setTimeout(tick, 4000);
		const clock = setInterval(() => (now = Date.now() / 1000), 1000);
		return () => { clearTimeout(t); clearInterval(clock); };
	});

	const LABEL: Record<TeamStatus, string> = { running: 'Working', waiting: 'Waiting for a free slot', idle: 'Resting', attention: 'Needs attention', paused: 'Paused' };
	const GLYPH: Record<TeamStatus, string> = { running: 'searching', waiting: 'waiting', idle: 'idle', attention: 'error', paused: 'paused' };
	const working = (a: TeamAgent) => a.status === 'running' || a.status === 'waiting';
	const agents = $derived(team ? [...team.agents].sort((a, b) => Number(working(b)) - Number(working(a))) : []);

	const video = $derived(agent.effective);
	const videoBusy = $derived(!['idle', 'sleeping', 'paused', 'offline', 'waiting', 'backoff'].includes(video));
	const DL: Record<string, string> = {
		offline: 'Not running', idle: 'Up to date', downloading: 'Downloading', transcoding: 'Re-encoding on the GPU', sleeping: 'Resting between downloads',
		full: 'Storage full', paused: 'Paused with the AI', disabled: 'Turned off', backoff: 'Waiting for YouTube'
	};
	const dlBusy = $derived(agent.downloader.state === 'downloading' || agent.downloader.state === 'transcoding');

	const elapsed = (since: number | null) => {
		if (!since) return '';
		const s = Math.max(0, Math.round(now - since));
		return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`;
	};
	const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? '' : 's'}`;

	// Deterministic, friendly narration built only from real state.
	function now_line(a: TeamAgent): string {
		if (a.status === 'waiting') return 'Ready to go as soon as another agent finishes.';
		if (a.status === 'running') {
			if (a.kind === 'curator' && a.doing) return a.doing;
			if (a.kind === 'scout') return a.doing || `Researching “${a.query}”.`;
			if (a.kind === 'briefing') return a.doing || 'Writing a briefing.';
			return a.role === 'research' ? 'Investigating its subject across sources.' : 'Checking its sources for new finds.';
		}
		if (a.status === 'attention') return a.error || 'The last run had problems. Open it to see why.';
		if (a.status === 'paused') return 'Paused. It will not run until you resume it.';
		if (a.kind === 'scout') {
			if (a.last) return a.last.status === 'complete' ? `Last trip: ${plural(a.last.suggestions ?? 0, 'validated source')} for “${a.last.query}”.` : `Last trip for “${a.last.query}” did not finish.`;
			return 'Has not been sent out yet. Give it a site or topic under Sources.';
		}
		if (a.kind === 'briefing') return a.last_run ? `Last briefing written ${ago(a.last_run)}.` : 'No briefing written yet.';
		if (a.found_today) return `Brought back ${plural(a.found_today, 'find')} today.`;
		if (a.last?.new_items) return `Last run brought back ${plural(a.last.new_items, 'new item')}.`;
		if (a.last) return a.last.status === 'ok' || a.last.status === 'partial' ? `Last run checked ${plural(a.last.seen_items ?? 0, 'item')}; nothing new.` : 'The last run failed.';
		return 'Has not run yet.';
	}
	function story(e: TeamEntry): string {
		if (e.kind === 'scout_run') return e.status === 'complete' ? `scouted “${e.query}” and validated ${plural(e.suggestions ?? 0, 'source')}.` : `could not finish scouting “${e.query}”.`;
		if (e.kind === 'briefing') return `wrote a new issue of “${e.title}”.`;
		if (e.status === 'failed') return 'ran into trouble on its last run.';
		const extra = e.errors ? ` ${plural(e.errors, 'source')} had problems.` : '';
		return (e.new_items ? `found ${plural(e.new_items, 'new item')}.` : `checked ${plural(e.seen_items ?? 0, 'item')}; nothing new.`) + extra;
	}
	const foot = (a: TeamAgent) => [
		a.next_run && !working(a) ? `Next run ${ago(a.next_run)}` : a.last_run ? `Last run ${ago(a.last_run)}` : '',
		a.kind === 'scout' && a.plans ? plural(a.plans, 'scheduled search') : ''
	].filter(Boolean).join(' · ');
	const href = (a: TeamAgent) => (a.kind === 'scout' ? '/sources' : a.kind === 'briefing' ? '/briefings' : a.role === 'research' ? `/research/${a.id}` : `/curators/${a.id}`);
	const entryHref = (e: TeamEntry) => (e.kind === 'scout_run' ? '/sources' : e.kind === 'briefing' ? '/briefings' : `/curators/${e.agent.split('-')[1]}`);
</script>

<svelte:head><title>Agent Activity · WatchCraft</title></svelte:head>

<div class="page team">
	<a class="btn report-shortcut" href="/scout-report" use:focusable>Open Scout Report →</a>
	<header class="head">
		<div>
			<p class="eyebrow">RESEARCH TEAM</p>
			<h1>Agent Activity</h1>
			<p class="muted">What your agents are doing right now, and what they brought back.</p>
		</div>
		{#if team}
			<a class="slots" href="/settings?tab=ai" use:focusable aria-label={`${team.running} of ${team.limit} agent slots in use. Change in Settings.`}>
				<span class="lamps" aria-hidden="true">
					{#each Array(team.limit) as _, i (i)}<span class="lamp" class:on={i < team.running}></span>{/each}
				</span>
				<span><b>{team.running}/{team.limit}</b>{` working${team.waiting ? ` · ${team.waiting} waiting` : ''}`}</span>
			</a>
		{/if}
	</header>

	{#if error}<ErrorBox message={error} onretry={load} />{/if}

	<section class="grid" aria-label="Agents" aria-busy={loading}>
		<a class="card" class:busy={videoBusy} style:--c="#ff6b9a" href="/agent" use:focusable>
			<div class="top">
				<span class="avatar"><AgentGlyph state={video} size="1.8rem" /></span>
				<div class="ttl"><h2>Video Curator</h2><span class="role">Finds videos for your playlists</span></div>
				<span class="st {videoBusy ? 'running' : video === 'backoff' || video === 'error' ? 'attention' : 'idle'}">{STATE_LABEL[video] ?? video}</span>
			</div>
			<p class="now">{video === 'offline' ? 'Its worker is not running right now.' : video === 'paused' ? 'Paused. No searches or AI calls.' : agent.state.detail ? cleanDetail(video, agent.state.detail) : 'Waiting for its next step.'}</p>
			<p class="foot muted small">Runs in its own worker, one step at a time.</p>
		</a>
		<a class="card" class:busy={dlBusy} style:--c="#7bd88f" href="/agent" use:focusable>
			<div class="top">
				<span class="avatar emoji">📥</span>
				<div class="ttl"><h2>Downloader</h2><span class="role">Keeps offline copies ready</span></div>
				<span class="st {dlBusy ? 'running' : agent.downloader.state === 'full' || agent.downloader.state === 'backoff' ? 'attention' : 'idle'}">{DL[agent.downloader.state] ?? agent.downloader.state}</span>
			</div>
			<p class="now">{dlBusy ? 'Fetching videos so they play instantly.' : 'Nothing to fetch right now.'}</p>
		</a>

		{#if loading}
			{#each [0, 1] as i (i)}<div class="skeleton" style="height:12rem"></div>{/each}
		{/if}
		{#each agents as a (a.key)}
			<article class="card" class:busy={a.status === 'running'} class:waiting={a.status === 'waiting'} style:--c={a.color}>
				<a class="card-link" href={href(a)} use:focusable>
				<div class="top">
					<span class="avatar emoji">{a.icon}{#if a.status === 'running'}<span class="spark" aria-hidden="true"><AgentGlyph state={GLYPH[a.status]} size="1rem" /></span>{/if}</span>
					<div class="ttl"><h2>{a.name}</h2><span class="role">{a.kind === 'scout' ? 'Source Scout' : a.kind === 'briefing' ? 'Briefings' : a.role === 'research' ? 'Researcher' : 'Watcher'}</span></div>
					<span class="st {a.status}">{LABEL[a.status] + (a.status === 'running' && a.since ? ` · ${elapsed(a.since)}` : '')}</span>
				</div>
				{#if a.mission}<p class="mission muted">{a.mission}</p>{/if}
				<p class="now">{now_line(a)}</p>
				{#if foot(a)}<p class="foot muted small">{foot(a)}</p>{/if}
				</a>
				{#if a.finds?.length}
					<div class="findings" aria-label="Recent discoveries">
						{#each a.finds as f (f.id)}
							<details class="finding">
								<summary use:focusable>{f.title}</summary>
								<p class="small muted">{f.source_name || f.publisher || 'Source not named'}{f.type ? ` · ${f.type}` : ''} · found {ago(f.found_at)}</p>
								{#if f.published_at}<p class="small muted">Published {ago(f.published_at)}</p>{/if}
								<p class="small"><b>Source summary:</b> {f.summary || 'No source summary provided.'}</p>
								{#if f.reason}<p class="small"><b>Curator note:</b> {f.reason}</p>{/if}
								<div class="finding-links"><a href={`/item/${f.id}`} use:focusable>View finding</a>{#if f.url}<a href={f.url} target="_blank" rel="noopener noreferrer" use:focusable>Open original source</a>{/if}</div>
							</details>
						{/each}
					</div>
				{/if}
				{#if a.timeline?.length}
					<details class="timeline">
						<summary use:focusable>Run timeline</summary>
						<ol>{#each a.timeline as step}
							<li><p>{step.message}</p><time class="small muted">{ago(step.at)}</time>
								{#if step.detail}<details><summary use:focusable>Technical details</summary><p class="small">{step.detail}</p></details>{/if}
							</li>
						{/each}</ol>
					</details>
				{/if}
			</article>
		{/each}
	</section>

	{#if team && !team.agents.some((a) => a.kind === 'curator')}
		<p class="muted hint">No curators yet. <a href="/curators/new" use:focusable>Create one</a> and it will show up here when it goes to work.</p>
	{/if}

	<section class="journal" aria-label="Research journal">
		<h2>Journal</h2>
		{#if team?.journal.length}
			<ol>
				{#each team.journal as e (e.kind + e.agent + e.at)}
					<li class:bad={e.status === 'failed'}>
						<span class="jic" aria-hidden="true">{e.icon}</span>
						<a href={entryHref(e)} use:focusable><b>{e.name}</b> {story(e)}</a>
						<time class="muted small">{ago(e.at)}</time>
					</li>
				{/each}
			</ol>
		{:else if team}
			<p class="muted">Nothing yet. Finished runs appear here.</p>
		{/if}
	</section>
	{#if session.isAdmin}<p class="muted small">Tip: allow more agents to work at once under <a href="/settings?tab=ai" use:focusable>Settings → AI and limits → Agent team</a>.</p>{/if}
</div>

<style>
	.report-shortcut { float: right; margin-bottom: 1rem; }
	.head { display: flex; justify-content: space-between; align-items: flex-end; gap: 1rem; flex-wrap: wrap; margin-bottom: 1.2rem; }
	.eyebrow { font-size: .7rem; letter-spacing: .15em; color: var(--accent); font-weight: 700; margin: 0; }
	h1 { font-size: clamp(1.8rem, 3vw, 2.8rem); margin: .2rem 0; }
	.slots { display: flex; align-items: center; gap: .7rem; padding: .6rem 1rem; border: 1px solid var(--line); border-radius: var(--r-pill); background: var(--surface); color: var(--text); min-height: 2.75rem; }
	.lamps { display: flex; gap: .3rem; }
	.lamp { width: .75rem; height: .75rem; border-radius: 50%; background: var(--line-strong); transition: background .3s, box-shadow .3s; }
	.lamp.on { background: var(--ok); box-shadow: 0 0 .6rem var(--ok); }
	.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 20rem), 1fr)); gap: 1.1rem; align-items: start; }
	.card { position: relative; display: grid; gap: .6rem; padding: 1.1rem 1.2rem; border: 1px solid var(--line); border-top: 3px solid var(--c); border-radius: var(--r-lg); background: var(--surface); color: var(--text); align-content: start; min-width: 0; overflow: hidden; }
	.card.busy { border-color: color-mix(in srgb, var(--c) 60%, var(--line)); box-shadow: 0 0 0 1px color-mix(in srgb, var(--c) 35%, transparent), 0 .6rem 2rem -1rem var(--c); }
	.card.busy::after { content: ''; position: absolute; left: 0; right: 0; top: 0; height: 3px; background: linear-gradient(90deg, transparent, #fff8, transparent); animation: sweep 1.8s linear infinite; }
	.card.waiting { opacity: .85; }
	.card-link { display: grid; gap: .6rem; color: inherit; }
	.timeline { border-top: 1px solid var(--line); padding-top: .6rem; }
	.timeline summary { cursor: pointer; padding: .3rem 0; }
	.timeline ol { padding-left: 1.2rem; margin: .5rem 0; }
	.timeline li { margin: .6rem 0; overflow-wrap: anywhere; }
	.timeline p { margin: .2rem 0; }
	.finding { border-top: 1px solid var(--line); padding: .5rem 0; overflow-wrap: anywhere; }
	.finding summary { cursor: pointer; color: var(--text); }
	.finding p { margin: .5rem 0; }
	.finding-links { display: flex; flex-wrap: wrap; gap: .8rem; font-size: .85rem; }
	.top { display: flex; gap: .8rem; align-items: center; }
	.avatar { position: relative; width: 3rem; height: 3rem; display: grid; place-items: center; border-radius: var(--r-md); background: color-mix(in srgb, var(--c) 26%, transparent); color: var(--c); flex: none; }
	.avatar.emoji { font-size: 1.6rem; }
	.busy .avatar { animation: bob 2.4s ease-in-out infinite; }
	.spark { position: absolute; right: -.35rem; bottom: -.35rem; width: 1.4rem; height: 1.4rem; display: grid; place-items: center; border-radius: 50%; background: var(--surface); color: var(--c); border: 1px solid var(--line); }
	.ttl { flex: 1; min-width: 0; }
	h2 { font-size: 1.05rem; margin: 0; overflow-wrap: anywhere; }
	.role { font-size: .72rem; color: var(--faint); text-transform: uppercase; letter-spacing: .08em; }
	.st { font-size: .72rem; font-weight: 700; padding: .2rem .6rem; border-radius: var(--r-pill); border: 1px solid var(--line-strong); white-space: nowrap; color: var(--muted); }
	.st.running { color: var(--info); border-color: var(--info); }
	.st.waiting { color: var(--warn, var(--accent)); border-color: currentColor; }
	.st.attention { color: var(--bad); border-color: var(--bad); }
	.mission { margin: 0; font-size: .85rem; display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
	.now { margin: 0; font-weight: 600; }
	.foot { margin: 0; }
	.hint { margin-top: 1rem; }
	.journal { margin-top: 2rem; }
	.journal h2 { font-size: 1.2rem; margin-bottom: .6rem; }
	.journal ol { list-style: none; margin: 0; padding: 0; display: grid; gap: .1rem; }
	.journal li { display: grid; grid-template-columns: 2rem 1fr auto; gap: .6rem; align-items: center; padding: .55rem .4rem; border-bottom: 1px solid var(--line); }
	.journal li a { color: var(--text); min-width: 0; }
	.journal li.bad a { color: var(--bad); }
	.jic { font-size: 1.2rem; text-align: center; }
	@keyframes sweep { from { transform: translateX(-100%); } to { transform: translateX(100%); } }
	@keyframes bob { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(-3px); } }
	@media (prefers-reduced-motion: reduce) { .card.busy::after, .busy .avatar { animation: none; } }
	@media (max-width: 600px) { .journal li { grid-template-columns: 1.6rem 1fr; } .journal time { grid-column: 2; } }
</style>
