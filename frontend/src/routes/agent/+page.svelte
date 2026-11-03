<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import { agent, STATE_LABEL, toneOf, cleanDetail } from '$lib/stores/agent.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { dialogs } from '$lib/stores/dialogs.svelte';
	import { session } from '$lib/stores/session.svelte';
	import { toast } from '$lib/stores/toast.svelte';
	import { focusable } from '$lib/nav';
	import { clockTime, fmtBytes, fmtCountdown, timeAgo } from '$lib/util';
	import { preview } from '$lib/preview';
	import type { AgentEvent, AgentQueue } from '$lib/types';
	import AgentGlyph from '$lib/components/AgentGlyph.svelte';

	let queues = $state<AgentQueue[] | null>(null);
	let filter = $state<'all' | 'picks' | 'problems'>('all');
	let shown = $state(80);

	async function refresh() {
		try {
			const s = await api.agentStatus();
			queues = s.queues ?? [];
			if (s.budget) agent.budget = s.budget;
			if (s.downloader) agent.downloader = s.downloader;
		} catch {
			queues = queues ?? [];
		}
	}
	onMount(() => {
		refresh();
		agent.backfill();
		const t = setInterval(refresh, 15000);
		const off = agent.onEvent((e) => (e.kind === 'accept' || e.kind === 'reject') && refresh());
		return () => {
			clearInterval(t);
			off();
		};
	});

	const st = $derived(agent.effective);
	const tone = $derived(toneOf(st));
	const cat = $derived(session.categoryById(agent.state.category_id ?? null));
	const catName = $derived(cat ? `${cat.emoji} ${cat.name}` : (agent.state.category_name as string | undefined));
	const b = $derived(agent.budget);
	const events = $derived(
		agent.events.filter((e) => e.level !== 'debug' && (filter === 'all' || (filter === 'picks' ? e.kind === 'accept' || e.kind === 'reject' : e.level === 'warn' || e.level === 'error')))
	);
	const totalSecs = $derived.by(() => {
		if (st === 'backoff') return null; // the end time is what matters; a bar would reset every wake
		const u = agent.state.until;
		const upd = agent.state.updated_at;
		if (!u || !upd) return null;
		const a = Date.parse(upd.endsWith('Z') ? upd : upd + 'Z');
		const z = Date.parse(u.endsWith('Z') || u.includes('+') ? u : u + 'Z');
		return z > a ? (z - a) / 1000 : null;
	});

	async function runNow(categoryId?: number) {
		await api.agentRun(categoryId ?? null);
		toast.success(agent.control.paused ? 'Queued. Resume the AI so it can start.' : 'The AI will start right away.');
	}

	async function retryBackoff() {
		await api.agentResetBackoff();
		toast.warn('Backoff cleared. The AI will try YouTube again now.');
	}

	async function openEvent(e: AgentEvent) {
		const cid = (e.payload?.candidate_id as number | undefined) ?? null;
		if (cid) return dialogs.openCandidate(cid);
		if (!e.video_id || !e.category_id) return;
		try {
			const list = await api.categoryFeed(e.category_id, 'all', { limit: 300, include_watched: true }, e.profile_id);
			const c = list.find((x) => x.video.id === e.video_id);
			if (c) dialogs.openCandidate(c.id);
			else toast.info('That candidate is no longer stored.');
		} catch {
			toast.info('That video belongs to another profile.');
		}
	}

	const KIND_ICON: Record<string, string> = {
		plan: '🧭', search: '🔎', rss: '📰', metadata: '📄', transcript: '📜', comments: '💬', judge: '🤔',
		accept: '✅', reject: '❌', learn: '🧠', sleep: '😴', backoff: '🛑', error: '⚠️', control: '⏯️'
	};
	const pct = (a: number, z: number) => (z > 0 ? Math.min(100, (a / z) * 100) : 0);
	const dl = $derived(agent.downloader);
	const DL_LABEL: Record<string, string> = {
		offline: 'Not running', idle: 'Up to date', downloading: 'Downloading', transcoding: 'Re-encoding on the GPU', sleeping: 'Resting between downloads',
		full: 'Storage full', paused: 'Paused with the AI', disabled: 'Turned off', backoff: 'Waiting for YouTube'
	};
</script>


<div class="page agent workshop-page">
	<section class="status panel {tone}">
		<div class="glyph"><AgentGlyph state={st} size="clamp(3rem, 5vw, 5.5rem)" /></div>
		<div class="main">
			<p class="kicker">The curator is</p>
			<h1>{STATE_LABEL[st] ?? st}</h1>
			{#if agent.state.detail && st !== 'paused'}<p class="detail">{cleanDetail(st, agent.state.detail)}</p>{/if}
			{#if catName && st !== 'paused' && st !== 'offline'}<p class="muted">For {catName}</p>{/if}
			{#if st === 'offline'}
				<p class="muted">{agent.connected ? 'The worker process is not running. Check the bt-worker container.' : 'Lost the live connection to the server. Reconnecting…'}</p>
			{:else if st === 'paused'}
				<p class="muted">No YouTube searches and no AI calls happen while paused.</p>
			{/if}
			{#if agent.secondsLeft != null && (st === 'sleeping' || st === 'backoff' || st === 'waiting')}
				<div class="count">
					<span class="num">{fmtCountdown(agent.secondsLeft)}</span>
					<span class="muted small">{st === 'backoff' ? `YouTube asked us to slow down. Resuming at ${clockTime(agent.state.backoff_until || agent.state.until_end || agent.state.until || '')}.` : 'until the next step. Short random pauses keep YouTube from flagging the server.'}</span>
				</div>
				{#if totalSecs}
					<div class="cbar"><span style:width="{100 - pct(agent.secondsLeft, totalSecs)}%"></span></div>
				{/if}
			{/if}
			<div class="hstack acts">
				<button class="btn big" class:primary={agent.control.paused} use:focusable={{ group: 'a-acts', id: 'a-pause', autofocus: true, onSelect: () => agent.toggle() }}>
					<Icon name={agent.control.paused ? 'play' : 'pause'} />{agent.control.paused ? 'Resume AI' : 'Pause AI'}
				</button>
				<button class="btn big" use:focusable={{ group: 'a-acts', id: 'a-run', onSelect: () => runNow() }}>✦ Run now</button>
				{#if st === 'backoff'}
					<button class="btn big danger" use:focusable={{ group: 'a-acts', id: 'a-retry', onSelect: retryBackoff }}>Retry now</button>
				{/if}
			</div>
			{#if st === 'backoff'}
				<p class="small warnline">Retrying early can make YouTube block the server again, for longer. Waiting is safer.</p>
			{/if}
		</div>
		{#if agent.state.video_id}
			<div class="now">
				<div class="pvw"><img src={api.thumb(String(agent.state.video_id))} alt="" /></div>
				<span class="small">{(agent.state.title as string) ?? ''}</span>
			</div>
		{/if}
	</section>

	<div class="cols">
		<section class="panel stack">
			<h2>YouTube budget</h2>
			<p class="muted small">Requests the AI made to YouTube. Limits keep the server from being blocked; change them in Settings, AI.</p>
			{#if b}
				{#each [{ l: 'This hour', v: b.hour, m: b.max_hour }, { l: 'Today', v: b.day, m: b.max_day }] as m}
					<div class="meter">
						<div class="ml"><span>{m.l}</span><span class="num-s">{m.v} / {m.m}</span></div>
						<div class="mbar" class:hot={m.v / m.m > 0.85}><span style:width="{pct(m.v, m.m)}%"></span></div>
					</div>
				{/each}
			{:else}
				<div class="skeleton" style="height:4rem"></div>
			{/if}
		</section>

		<section class="panel stack downloads">
			<div class="hstack">
				<h2>Offline copies</h2>
				<span class="spacer"></span>
				<a class="btn mini" href="/settings?tab=storage" use:focusable={{ group: 'dl-card' }}>Storage</a>
			</div>
			<p class="muted small">Picked videos are saved on the server so they play instantly without YouTube.</p>
			<div class="dls">
				<strong>{DL_LABEL[dl.state] ?? dl.state}</strong>
				{#if dl.detail && dl.state !== 'downloading' && dl.state !== 'transcoding'}<span class="small muted">{dl.detail}</span>{/if}
			</div>
			{#if dl.state === 'downloading' || dl.state === 'transcoding'}
				<div class="dlnow">
					{#if dl.video_id}<div class="pvw"><img src={api.thumb(dl.video_id, 'mq')} alt="" /></div>{/if}
					<div class="grow">
						<span class="small">{dl.title || dl.video_id}</span>
						<div class="mbar"><span style:width="{(dl.progress ?? 0) * 100}%"></span></div>
						<span class="small muted num-s">{Math.round((dl.progress ?? 0) * 100)}%{dl.speed ? `, ${fmtBytes(dl.speed)}/s` : ''}{dl.eta ? `, ${fmtCountdown(Math.round(dl.eta))} left` : ''}</span>
					</div>
				</div>
			{/if}
			<div class="meter">
				<div class="ml"><span>Space used</span><span class="num-s">{fmtBytes(dl.used_bytes ?? 0)} / {dl.max_bytes ? fmtBytes(dl.max_bytes) : '–'}</span></div>
				<div class="mbar" class:hot={(dl.used_bytes ?? 0) / (dl.max_bytes || 1) > 0.9}><span style:width="{pct(dl.used_bytes ?? 0, dl.max_bytes ?? 0)}%"></span></div>
			</div>
			<p class="small muted">{dl.queue ?? 0} waiting to download</p>
		</section>

		<section class="panel stack queues">
			<h2>Playlist queues</h2>
			{#if queues === null}
				<div class="skeleton" style="height:6rem"></div>
			{:else if queues.length === 0}
				<p class="muted">No playlists yet. Create one in Settings.</p>
			{:else}
				{#each queues as q (q.category_id)}
					<div class="q" style:--cat={q.color} class:off={!q.enabled}>
						<div class="qh">
							<span class="qn"><a class="qlink" href="/category/{q.category_id}" use:focusable={{ group: `q-${q.category_id}`, id: `q-name-${q.category_id}` }}>{q.emoji} {q.name}</a></span>
							{#if !q.enabled}<span class="faint small">disabled</span>{/if}
							<span class="spacer"></span>
							<a class="btn mini" href="/category/{q.category_id}" use:focusable={{ group: `q-${q.category_id}` }}>Open</a>
							<button class="btn mini" use:focusable={{ group: `q-${q.category_id}`, onSelect: () => runNow(q.category_id) }}>Find more</button>
						</div>
						<div class="gauge"><span style:width="{pct(q.accepted, q.target)}%"></span></div>
						<div class="qs small muted">
							<span><strong>{q.accepted}</strong>/{q.target} ready</span>
							<span>{q.pending} in review</span>
							<span>{q.rejected} rejected</span>
							{#if q.last_worked_at}<span class="faint">worked {timeAgo(q.last_worked_at)}</span>{/if}
						</div>
					</div>
				{/each}
			{/if}
		</section>
	</div>

	<section class="panel log">
		<div class="hstack">
			<h2>Live log</h2>
			<span class="live-dot" class:on={agent.connected}></span>
			<span class="spacer"></span>
			{#each [['all', 'Everything'], ['picks', 'Verdicts'], ['problems', 'Problems']] as [k, l]}
				<button class="chip" class:on={filter === k} use:focusable={{ group: 'log-filter', onSelect: () => (filter = k as typeof filter) }}>{l}</button>
			{/each}
		</div>
		{#if events.length === 0}
			<p class="muted">Nothing yet. Events show up here the moment the AI does something.</p>
		{:else}
			<ol class="events">
				{#each events.slice(0, shown) as e (e.id)}
					<li class="ev {e.level} k-{e.kind}">
						{#if e.video_id}
							<button class="evb" use:focusable={{ group: 'log', scroll: 'center', onSelect: () => openEvent(e) }}>
								<span class="t">{clockTime(e.ts)}</span>
								<span class="i">{KIND_ICON[e.kind] ?? '•'}</span>
								<span class="m">{e.message.replace(/^\p{Extended_Pictographic}️?\s*/u, '')}</span>
								<span class="pvw"><img src={api.thumb(e.video_id, 'mq')} alt="" loading="lazy" /></span>
							</button>
						{:else}
							<div class="evb static">
								<span class="t">{clockTime(e.ts)}</span>
								<span class="i">{KIND_ICON[e.kind] ?? '•'}</span>
								<span class="m">{e.message.replace(/^\p{Extended_Pictographic}️?\s*/u, '')}</span>
							</div>
						{/if}
					</li>
				{/each}
			</ol>
			{#if events.length > shown}
				<button class="btn" use:focusable={{ group: 'log-more', onSelect: () => (shown += 100) }}>Show older</button>
			{/if}
		{/if}
	</section>
</div>

<style>
	.agent {
		display: grid;
		/* Explicit track: an implicit auto track would size to the log's no-wrap max-content and push the page sideways. */
		grid-template-columns: minmax(0, 1fr);
		gap: clamp(1rem, 1.6vw, 1.8rem);
	}
	.status {
		display: flex;
		gap: clamp(1.2rem, 2.5vw, 3rem);
		align-items: flex-start;
		flex-wrap: wrap;
		padding: clamp(1.4rem, 2.5vw, 2.8rem);
		position: relative;
		overflow: hidden;
	}
	.status::before {
		content: '';
		position: absolute;
		inset: -40% auto auto -10%;
		width: 50%;
		aspect-ratio: 1;
		background: radial-gradient(circle, color-mix(in srgb, var(--tone) 25%, transparent), transparent 70%);
		pointer-events: none;
	}
	.work {
		--tone: var(--accent);
	}
	.rest {
		--tone: var(--info);
	}
	.bad {
		--tone: var(--bad);
	}
	.paused,
	.off {
		--tone: var(--muted);
	}
	.idle {
		--tone: var(--ai);
	}
	.glyph {
		color: var(--tone);
		display: grid;
		place-items: center;
		width: clamp(5rem, 8vw, 9rem);
		aspect-ratio: 1;
		border-radius: 50%;
		background: color-mix(in srgb, var(--tone) 14%, transparent);
		border: 1px solid color-mix(in srgb, var(--tone) 40%, transparent);
	}
	.main {
		flex: 1;
		min-width: min(100%, 22rem);
		display: grid;
		gap: 0.5rem;
		position: relative;
	}
	.kicker {
		color: var(--muted);
		font-weight: 600;
	}
	.detail {
		font-size: 1.2rem;
		color: #e6e0ff;
	}
	.count {
		display: flex;
		align-items: baseline;
		gap: 1rem;
		flex-wrap: wrap;
		margin-top: 0.4rem;
	}
	.num {
		font-size: clamp(2rem, 3vw, 3.6rem);
		font-weight: 800;
		font-variant-numeric: tabular-nums;
		letter-spacing: -0.03em;
		color: var(--tone);
	}
	.cbar,
	.mbar,
	.gauge {
		height: 0.5rem;
		border-radius: 1rem;
		background: rgba(255, 255, 255, 0.1);
		overflow: hidden;
	}
	.cbar span,
	.mbar span,
	.gauge span {
		display: block;
		height: 100%;
		border-radius: inherit;
		transition: width 1s linear;
	}
	.cbar span {
		background: var(--tone);
	}
	.acts {
		margin-top: 0.8rem;
	}
	.warnline {
		color: var(--bad);
	}
	.now {
		width: clamp(14rem, 20vw, 24rem);
		display: grid;
		gap: 0.5rem;
		position: relative;
	}
	.now .pvw,
	.dlnow .pvw,
	.evb .pvw {
		position: relative;
		display: block;
		flex: none;
		overflow: hidden;
		border-radius: var(--r-md);
	}
	.dlnow .pvw {
		border-radius: 0.4rem;
	}
	.evb .pvw {
		border-radius: 0.35rem;
	}
	.now img {
		width: 100%;
		aspect-ratio: 16/9;
		object-fit: cover;
		border-radius: var(--r-md);
		box-shadow: var(--shadow-lg);
	}
	.cols {
		display: grid;
		grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) minmax(0, 1.6fr);
		gap: clamp(1rem, 1.6vw, 1.8rem);
	}
	.dls {
		display: grid;
		gap: 0.2rem;
	}
	.dlnow {
		display: flex;
		gap: 0.8rem;
		align-items: center;
	}
	.dlnow img {
		width: 6rem;
		aspect-ratio: 16/9;
		object-fit: cover;
		border-radius: 0.4rem;
	}
	.dlnow .grow {
		flex: 1;
		min-width: 0;
		display: grid;
		gap: 0.3rem;
	}
	@media (max-width: 1400px) {
		.cols {
			grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
		}
		.queues {
			grid-column: 1 / -1;
		}
	}
	@media (max-width: 1000px) {
		.cols {
			grid-template-columns: 1fr;
		}
	}
	.meter {
		display: grid;
		gap: 0.4rem;
	}
	.ml {
		display: flex;
		justify-content: space-between;
		font-weight: 600;
	}
	.num-s {
		font-variant-numeric: tabular-nums;
	}
	.mbar span {
		background: linear-gradient(90deg, var(--ok), var(--info));
	}
	.mbar.hot span {
		background: linear-gradient(90deg, var(--warn), var(--bad));
	}
	.q {
		display: grid;
		gap: 0.45rem;
		padding: 0.8rem 0;
		border-top: 1px solid var(--line);
	}
	.q.off {
		opacity: 0.55;
	}
	.qh {
		display: flex;
		align-items: center;
		gap: 0.6rem;
	}
	.qn {
		font-weight: 650;
	}
	.qn .qlink {
		color: inherit;
	}
	.qn .qlink:hover {
		text-decoration: underline;
		text-decoration-color: var(--cat, var(--ai));
		text-underline-offset: 0.3rem;
	}
	.gauge span {
		background: var(--cat);
		box-shadow: 0 0 12px var(--cat);
	}
	.qs {
		display: flex;
		gap: 1rem;
		flex-wrap: wrap;
	}
	.btn.mini {
		min-height: 2rem;
		padding: 0.2rem 0.8rem;
		font-size: 0.8rem;
	}
	.log {
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		gap: 1rem;
	}
	.live-dot {
		width: 0.6rem;
		height: 0.6rem;
		border-radius: 50%;
		background: var(--faint);
	}
	.live-dot.on {
		background: var(--ok);
		animation: pulse 1.6s infinite;
	}
	.events {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		grid-template-columns: minmax(0, 1fr); /* keep rows at panel width so .m can ellipsize */
		gap: 0.25rem;
	}
	.ev {
		animation: rise 0.3s var(--ease);
		content-visibility: auto;
		contain-intrinsic-size: auto 2.8rem;
	}
	.evb {
		--focus-scale: 1.01;
		width: 100%;
		display: flex;
		align-items: center;
		gap: 0.8rem;
		padding: 0.5rem 0.8rem;
		border-radius: var(--r-sm);
		border: 1px solid transparent;
		background: transparent;
		text-align: left;
		cursor: pointer;
		font-size: 0.92rem;
	}
	.evb.static {
		cursor: default;
	}
	button.evb:hover {
		background: rgba(255, 255, 255, 0.05);
	}
	.evb .t {
		color: var(--faint);
		font-variant-numeric: tabular-nums;
		font-size: 0.8rem;
		width: 3.4rem;
		flex: none;
	}
	.evb .i {
		width: 1.4rem;
		flex: none;
		text-align: center;
	}
	.evb .m {
		flex: 1;
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.evb img {
		width: 4.2rem;
		aspect-ratio: 16/9;
		object-fit: cover;
		border-radius: 0.35rem;
		flex: none;
	}
	.ev.success .m {
		color: #b9f5d8;
	}
	.ev.warn .m {
		color: #ffe3a3;
	}
	.ev.error .m {
		color: #ffb3c1;
	}
	.ev.error .evb {
		background: rgba(255, 92, 122, 0.08);
	}
	.k-reject .m {
		color: var(--muted);
	}
</style>
