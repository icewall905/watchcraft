<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { focusable } from '$lib/nav';
	import Modal from '$lib/components/Modal.svelte';
	import { toast } from '$lib/stores/toast.svelte';
	import { music, type Curator, type FavArtist, type ForYou, type Mix, type OfflineSummary, type Track } from '$lib/music/api';
	import { player, shuffled } from '$lib/music/player.svelte';
	import TrackCard from '$lib/music/TrackCard.svelte';
	import Player from '$lib/music/Player.svelte';

	const TABS = [['foryou', '✨ For you'], ['mixes', '💿 Mixes'], ['favorites', '❤️ Favourites']] as const;
	const VIBES = [['Party', '🎉'], ['Dance', '🕺'], ['Throwback', '📼'], ['Chill', '🌙'], ['Hype', '⚡'], ['Singalong', '🎤']] as const;
	const IDEAS = ['80s roller disco', 'Sunny summer BBQ', 'Late night R&B', 'Girls night out anthems', 'Indie festival warm-up', 'Songs everyone screams along to'];

	const tab = $derived(page.url.searchParams.get('tab') ?? 'foryou');
	const q = $derived(page.url.searchParams.get('q') ?? '');
	const mixId = $derived(Number(page.url.searchParams.get('mix')) || 0);
	const artistName = $derived(page.url.searchParams.get('artist') ?? '');

	let ai = $state(false);
	let offline = $state<OfflineSummary | null>(null);
	let curators = $state<Curator[]>([]);
	let recent = $state<Track[]>([]);
	let recentMixes = $state<Mix[]>([]);
	let favTracks = $state<Track[]>([]);
	let favArtists = $state<FavArtist[]>([]);
	let mixes = $state<Mix[]>([]);
	let forYou = $state<ForYou | null>(null);
	let forYouLoading = $state(false);
	let results = $state<Track[]>([]);
	let searching = $state(false);
	let searchText = $state('');
	let loaded = $state(false);
	let failed = $state('');

	let openMix = $state<Mix | null>(null);
	let artist = $state<{ name: string; tracks: Track[] } | null>(null);
	let artistLoading = $state(false);
	let sheet = $state<Track | null>(null);
	let creating = $state(false);
	let newDesc = $state('');
	let newCurator = $state('disco');
	let newName = $state('');
	let newKind = $state<'curator' | 'manual'>('curator');
	let busy = $state(false);
	let poll: number | undefined;

	function setParams(patch: Record<string, string | null>) {
		const u = new URL(page.url);
		for (const [k, v] of Object.entries(patch)) if (v) u.searchParams.set(k, v); else u.searchParams.delete(k);
		goto(u, { replaceState: false, keepFocus: true, noScroll: true });
	}

	async function loadHome() {
		try {
			const h = await music.home();
			ai = h.ai;
			offline = h.offline;
			curators = h.curators;
			recent = h.recent;
			recentMixes = h.recent_mixes;
			favTracks = h.favorites.tracks;
			favArtists = h.favorites.artists;
			mixes = h.mixes;
			player.syncFavorites(h.favorites);
			loaded = true;
			failed = '';
		} catch (e) {
			failed = (e as Error).message || 'Could not load music.';
		}
	}
	async function loadForYou(refresh = false) {
		forYouLoading = true;
		try {
			forYou = await music.forYou(refresh);
			player.learn([...forYou.loved, ...forYou.fresh]);
		} catch {
			/* toast shown */
		} finally {
			forYouLoading = false;
		}
	}
	async function refreshFavorites() {
		const f = await music.favorites();
		favTracks = f.tracks;
		favArtists = f.artists;
		player.syncFavorites(f);
	}
	async function refreshMixes() {
		mixes = (await music.mixes()).mixes;
		if (openMix) openMix = await music.mix(openMix.id);
	}

	$effect(() => {
		const query = q;
		searchText = query;
		if (!query) return;
		searching = true;
		results = [];
		music.search(query).then((r) => { if (query === q) { results = r.tracks; player.learn(r.tracks); } }).catch(() => {}).finally(() => { if (query === q) searching = false; });
	});
	$effect(() => {
		const id = mixId;
		if (!id) { openMix = null; return; }
		music.mix(id).then((m) => { if (id === mixId) openMix = m; }).catch(() => setParams({ mix: null }));
	});
	$effect(() => {
		const name = artistName;
		if (!name) { artist = null; return; }
		artistLoading = true;
		artist = { name, tracks: [] };
		music.artist(name).then((r) => { if (name === artistName) { artist = { name, tracks: r.tracks }; player.learn(r.tracks); } }).catch(() => {}).finally(() => (artistLoading = false));
	});
	// Mixes build in the background: poll while any is still building.
	$effect(() => {
		const building = mixes.some((m) => m.status === 'building') || openMix?.status === 'building';
		window.clearInterval(poll);
		if (building) poll = window.setInterval(() => void refreshMixes().catch(() => {}), 2500);
		return () => window.clearInterval(poll);
	});
	// Favourites changed somewhere (a heart on a card, the player): keep the Favourites tab honest.
	$effect(() => {
		void player.favVersion;
		if (loaded && tab === 'favorites') void refreshFavorites().catch(() => {});
	});

	onMount(async () => {
		player.savedMix = (m) => {
			mixes = [m, ...mixes.filter((x) => x.id !== m.id)];
			recentMixes = [m, ...recentMixes.filter((x) => x.id !== m.id)].slice(0, 8);
		};
		await loadHome();
		void loadForYou();
	});
	onDestroy(() => {
		window.clearInterval(poll);
		player.savedMix = null;
	});

	/** Play a mix and remember it for "Recent mixes". */
	function playMix(m: Mix, how: 'play' | 'shuffle' | 'dj', start = 0) {
		const tracks = m.tracks ?? [];
		if (!tracks.length) return;
		if (how === 'play') player.play(tracks, start, { source: m.name });
		else if (how === 'shuffle') player.shuffleAll(tracks, m.name);
		else {
			player.party = true;
			void player.startDj(m.name, shuffled(tracks));
		}
		void music.mixPlayed(m.id).catch(() => {});
		m.last_played_at = Date.now() / 1000;
		recentMixes = [m, ...recentMixes.filter((x) => x.id !== m.id)].slice(0, 8);
	}
	async function keepCopy(m: Mix) {
		const copy = await music.copyMix(m.id).catch(() => null);
		if (!copy) return;
		mixes = [copy, ...mixes];
		toast.success(`💾 Kept "${copy.name}" — refreshes will not change it`);
		setParams({ mix: String(copy.id) });
	}
	async function renameMix(m: Mix) {
		const name = prompt('Rename mix', m.name)?.trim();
		if (!name || name === m.name) return;
		const r = await music.renameMix(m.id, name).catch(() => null);
		if (!r) return;
		mixes = mixes.map((x) => (x.id === m.id ? { ...x, name: r.name } : x));
		recentMixes = recentMixes.map((x) => (x.id === m.id ? { ...x, name: r.name } : x));
		if (openMix?.id === m.id) openMix = { ...openMix, name: r.name };
	}

	function doSearch(e: Event) {
		e.preventDefault();
		const t = searchText.trim();
		setParams({ q: t || null });
	}
	const showArtist = (name: string) => { sheet = null; setParams({ artist: name, mix: null }); };

	async function startDj(vibe: string) {
		player.party = true;
		await player.startDj(vibe);
	}
	function djFromFavorites() {
		player.party = true;
		// Your favourites play first, shuffled; the DJ keeps going with songs like them.
		void player.startDj('', shuffled(favTracks));
	}

	async function radioMix(t: Track) {
		sheet = null;
		try {
			const m = await music.createMix({ kind: 'seed', seed: t });
			mixes = [m, ...mixes.filter((x) => x.id !== m.id)];
			toast.success(`📻 Building a mix around ${t.title}…`);
			setParams({ mix: String(m.id), artist: null });
		} catch { /* toast shown */ }
	}
	async function addTo(m: Mix, t: Track) {
		sheet = null;
		try {
			await music.addToMix(m.id, t);
			toast.success(`Added to ${m.name}`);
			await refreshMixes();
		} catch { /* toast shown */ }
	}
	async function createMix(e: Event) {
		e.preventDefault();
		busy = true;
		try {
			const m = newKind === 'curator'
				? await music.createMix({ kind: 'curator', description: newDesc, curator: newCurator })
				: await music.createMix({ kind: 'manual', name: newName });
			mixes = [m, ...mixes];
			creating = false;
			newDesc = '';
			newName = '';
			setParams({ tab: 'mixes', mix: String(m.id) });
		} catch { /* toast shown */ } finally {
			busy = false;
		}
	}
	async function removeMix(m: Mix) {
		if (!confirm(`Delete the mix "${m.name}"?`)) return;
		await music.deleteMix(m.id);
		mixes = mixes.filter((x) => x.id !== m.id);
		setParams({ mix: null });
	}
	async function refreshMix(m: Mix) {
		openMix = await music.refreshMix(m.id).then(() => music.mix(m.id));
		mixes = mixes.map((x) => (x.id === m.id ? { ...x, status: 'building' } : x));
	}
	async function removeFromMix(m: Mix, t: Track) {
		openMix = await music.removeFromMix(m.id, t.video_id);
	}
	const curatorOf = (key: string) => curators.find((c) => c.key === key);
	const manualMixes = $derived(mixes.filter((m) => m.kind === 'manual')); // curated mixes get rebuilt, so hand-picks go in your own
</script>

<svelte:head><title>Music Videos · WatchCraft</title></svelte:head>

{#snippet grid(tracks: Track[], source: string)}
	<div class="grid">
		{#each tracks as t, i (t.video_id)}
			<TrackCard track={t} onPlay={() => player.play(tracks, i, { source })} onMore={(x) => (sheet = x)} onArtist={showArtist} />
		{/each}
	</div>
{/snippet}

{#snippet shelf(title: string, tracks: Track[], source: string, extra?: () => any)}
	<section class="shelf">
		<header>
			<h2>{title}</h2>
			<div class="acts">
				{#if extra}{@render extra()}{/if}
				{#if tracks.length}
					<button class="btn" use:focusable onclick={() => player.play(tracks, 0, { source })}>▶ Play</button>
					<button class="btn" use:focusable onclick={() => player.shuffleAll(tracks, source)}>🔀 Shuffle</button>
				{/if}
			</div>
		</header>
		{@render grid(tracks, source)}
	</section>
{/snippet}

<div class="music" class:docked={player.active}>
	<header class="top">
		<div class="title">
			<h1>🎶 Music Videos</h1>
			{#if offline && offline.wanted}
				<span class="stored" title="Favourites, mixes and suggestions are stored on the server so they play without YouTube">💾 {offline.saved} of {offline.wanted} songs saved offline{offline.pending ? ` · ${offline.pending} on the way` : ''}</span>
			{/if}
		</div>
		<form class="search" onsubmit={doSearch} role="search">
			<input class="input" type="search" placeholder="Search songs or artists…" bind:value={searchText} use:focusable aria-label="Search music videos" />
			<button class="btn primary" use:focusable>Search</button>
		</form>
	</header>

	<section class="hero">
		<div class="disc" class:spinning={player.active && player.mode === 'dj'} aria-hidden="true"><span></span></div>
		<div class="pitch">
			<h2>Let the DJ run the party</h2>
			<p>Pick a vibe. The DJ plays official music videos, takes requests and lines up fresh tracks while you dance. {ai ? '' : '(Radio mode: add an AI model in settings for a chatty DJ.)'}</p>
			<div class="vibes">
				{#each VIBES as [v, e]}
					<button class="vibe" use:focusable onclick={() => startDj(v)}><span>{e}</span>{v}</button>
				{/each}
				<button class="vibe fav" use:focusable onclick={djFromFavorites}><span>❤️</span>My faves</button>
			</div>
		</div>
	</section>

	{#if q}
		<section class="shelf">
			<header>
				<h2>Results for “{q}”</h2>
				<div class="acts">
					{#if results.length}<button class="btn" use:focusable onclick={() => player.shuffleAll(results, q)}>🔀 Shuffle</button>{/if}
					<button class="btn ghost" use:focusable onclick={() => setParams({ q: null })}>✕ Clear</button>
				</div>
			</header>
			{#if searching}<div class="sk-grid">{#each Array(8) as _}<div class="skeleton sk"></div>{/each}</div>
			{:else if !results.length}<p class="muted">No official music videos found. Try the artist and song name.</p>
			{:else}{@render grid(results, q)}{/if}
		</section>
	{/if}

	<nav class="tabs" aria-label="Music sections">
		{#each TABS as [key, label]}
			<button class="tab" class:on={tab === key} use:focusable aria-current={tab === key ? 'page' : undefined} onclick={() => setParams({ tab: key === 'foryou' ? null : key })}>{label}</button>
		{/each}
	</nav>

	{#if failed}
		<p class="err">{failed} <button class="btn small" onclick={loadHome}>Retry</button></p>
	{:else if tab === 'foryou'}
		{#if recentMixes.length}
			<section class="shelf">
				<header><h2>💿 Recent mixes</h2><div class="acts"><button class="btn ghost" use:focusable onclick={() => setParams({ tab: 'mixes' })}>All mixes →</button></div></header>
				<div class="strip">
					{#each recentMixes as m (m.id)}
						<button class="mix small" use:focusable style:--mix={m.color || '#ff4fa3'} onclick={() => setParams({ mix: String(m.id) })}>
							<div class="covers">{#each m.covers.slice(0, 4) as c}<img src={c} alt="" loading="lazy" />{:else}<span class="big-emoji">{m.emoji}</span>{/each}</div>
							<strong>{m.emoji} {m.name}</strong>
							<small>{m.status === 'building' ? 'Curating…' : `${m.count} videos`}</small>
						</button>
					{/each}
				</div>
			</section>
		{/if}
		{#if recent.length}{@render shelf('🕘 Recently played', recent.slice(0, 8), 'Recently played')}{/if}
		{#if forYouLoading && !forYou}
			<div class="sk-grid">{#each Array(8) as _}<div class="skeleton sk"></div>{/each}</div>
		{:else if forYou}
			{#if forYou.loved.length}
				{@render shelf('💖 Songs you love', forYou.loved, 'Songs you love')}
			{:else}
				<p class="hint">Tap 🤍 on songs you like — they show up here and teach the DJ your taste.</p>
			{/if}
			{#snippet refreshBtn()}
				<button class="btn ghost" use:focusable disabled={forYouLoading} onclick={() => loadForYou(true)}>{forYouLoading ? '…' : '🔄 New picks'}</button>
			{/snippet}
			{@render shelf('🌱 New for you', forYou.fresh, 'New for you', refreshBtn)}
		{/if}
	{:else if tab === 'mixes'}
		<div class="mixes">
			<button class="mix new" use:focusable onclick={() => (creating = true)}>
				<span class="plus">＋</span><strong>New mix</strong><small>Describe it — a curator builds it</small>
			</button>
			{#each mixes as m (m.id)}
				<button class="mix" use:focusable style:--mix={m.color || '#ff4fa3'} onclick={() => setParams({ mix: String(m.id) })}>
					<div class="covers">
						{#each m.covers.slice(0, 4) as c}<img src={c} alt="" loading="lazy" />{:else}<span class="big-emoji">{m.emoji}</span>{/each}
					</div>
					<strong>{m.emoji} {m.name}</strong>
					<small>
						{#if m.status === 'building'}<span class="pulse">Curating…</span>
						{:else if m.status === 'error'}⚠️ {m.error}
						{:else}{m.count} videos{m.curator_name ? ` · by ${m.curator_name}` : m.kind === 'seed' ? ' · track radio' : ''}{/if}
					</small>
				</button>
			{/each}
		</div>
	{:else if tab === 'favorites'}
		{#if favArtists.length}
			<section class="shelf">
				<header><h2>⭐ Artists</h2></header>
				<div class="artists">
					{#each favArtists as a (a.key)}
						<button class="artist-chip" use:focusable onclick={() => showArtist(a.name)}>{a.name}</button>
					{/each}
				</div>
			</section>
		{/if}
		{#if favTracks.length}
			{@render shelf('❤️ Tracks', favTracks, 'Your favourites')}
		{:else}
			<p class="hint">No favourites yet. Tap 🤍 on any song, or ⭐ an artist from their page.</p>
		{/if}
	{/if}

	<Player onArtist={showArtist} onMore={(t) => (sheet = t)} />
</div>

{#if sheet}
	{@const t = sheet}
	<Modal portal title={t.title} subtitle={t.artist} size="sm" onClose={() => (sheet = null)}>
		<div class="sheet">
			<button class="btn" use:focusable onclick={() => { player.play([t]); sheet = null; }}>▶ Play now</button>
			<button class="btn" use:focusable onclick={() => { player.playNext(t); sheet = null; }}>⏭ Play next</button>
			<button class="btn" use:focusable onclick={() => { player.enqueue(t); sheet = null; }}>➕ Add to queue</button>
			<button class="btn" use:focusable onclick={() => { sheet = null; player.party = true; void player.startDj(player.vibe, [t]); }}>🎧 DJ set from this song</button>
			<button class="btn" use:focusable onclick={() => radioMix(t)}>📻 Make a mix from this song</button>
			<button class="btn" use:focusable onclick={() => player.toggleFav(t)}>{player.isFav(t) ? '💔 Remove from favourites' : '❤️ Favourite song'}</button>
			<button class="btn" use:focusable onclick={() => player.toggleFavArtist(t.artist, t.artist_id)}>{player.isFavArtist(t.artist) ? '☆ Unfavourite artist' : `⭐ Favourite ${t.artist}`}</button>
			<button class="btn" use:focusable onclick={() => showArtist(t.artist)}>🎤 More from {t.artist}</button>
			{#if manualMixes.length}
				<p class="muted small">Add to a mix</p>
				<div class="addto">
					{#each manualMixes as m (m.id)}
						<button class="chip" use:focusable onclick={() => addTo(m, t)}>{m.emoji} {m.name}</button>
					{/each}
				</div>
			{/if}
		</div>
	</Modal>
{/if}

{#if openMix}
	{@const m = openMix}
	<Modal portal title={`${m.emoji} ${m.name}`} subtitle={m.curator_name ? `Curated by ${m.curator_name}` : m.kind === 'seed' ? 'Track radio' : 'Your mix'} size="lg" onClose={() => setParams({ mix: null })}>
		<div class="mixview">
			{#if m.blurb || m.description}
				<p class="blurb">{m.blurb}{#if m.description}<br /><small class="muted">“{m.description}”</small>{/if}</p>
			{/if}
			<div class="acts">
				{#if m.tracks?.length}
					<button class="btn primary" use:focusable onclick={() => { playMix(m, 'play'); setParams({ mix: null }); }}>▶ Play</button>
					<button class="btn" use:focusable onclick={() => { playMix(m, 'shuffle'); setParams({ mix: null }); }}>🔀 Shuffle</button>
					<button class="btn" use:focusable onclick={() => { setParams({ mix: null }); playMix(m, 'dj'); }}>🎧 DJ from this mix</button>
					{#if m.kind !== 'manual' && m.status === 'ready'}<button class="btn" use:focusable title="Keep these exact songs as your own mix" onclick={() => keepCopy(m)}>💾 Keep a copy</button>{/if}
				{/if}
				<button class="btn ghost" use:focusable onclick={() => renameMix(m)}>✏️ Rename</button>
				{#if m.kind !== 'manual'}<button class="btn ghost" use:focusable disabled={m.status === 'building'} onclick={() => refreshMix(m)}>🔄 Fresh picks</button>{/if}
				<button class="btn ghost danger" use:focusable onclick={() => removeMix(m)}>🗑 Delete</button>
			</div>
			{#if m.status === 'building'}
				<div class="building"><span class="disc small spinning"><span></span></span>{m.curator_name || 'The curator'} is digging through YouTube Music…</div>
			{:else if m.status === 'error'}
				<p class="err">{m.error}</p>
			{/if}
			{#if m.tracks?.length}
				<ol class="tracklist">
					{#each m.tracks as t, i (t.video_id)}
						<li class:now={player.current?.video_id === t.video_id}>
							<button class="row" use:focusable onclick={() => playMix(m, 'play', i)}>
								<span class="n">{i + 1}</span>
								<img src={t.thumbnail} alt="" loading="lazy" />
								<span class="tt"><strong>{t.title}</strong><small>{t.offline ? '💾 ' : ''}{t.artist}{t.reason ? ` · ${t.reason}` : ''}</small></span>
							</button>
							<button class="ic" use:focusable aria-label="Favourite" onclick={() => player.toggleFav(t)}>{player.isFav(t) ? '❤️' : '🤍'}</button>
							<button class="ic" use:focusable aria-label="More actions" onclick={() => (sheet = t)}>⋯</button>
							{#if m.kind === 'manual'}<button class="ic" use:focusable aria-label="Remove from mix" onclick={() => removeFromMix(m, t)}>✕</button>{/if}
						</li>
					{/each}
				</ol>
			{:else if m.kind === 'manual' && m.status === 'ready'}
				<p class="hint">Empty for now. Use ⋯ on any song → “Add to a mix”.</p>
			{/if}
		</div>
	</Modal>
{/if}

{#if artist}
	{@const a = artist}
	<Modal portal title={`🎤 ${a.name}`} size="lg" onClose={() => setParams({ artist: null })}>
		<div class="mixview">
			<div class="acts">
				<button class="btn" class:primary={!player.isFavArtist(a.name)} use:focusable onclick={() => player.toggleFavArtist(a.name, a.tracks[0]?.artist_id ?? '')}>{player.isFavArtist(a.name) ? '⭐ Favourite artist' : '☆ Favourite artist'}</button>
				{#if a.tracks.length}
					<button class="btn" use:focusable onclick={() => { player.play(a.tracks, 0, { source: a.name }); setParams({ artist: null }); }}>▶ Play all</button>
					<button class="btn" use:focusable onclick={() => { player.shuffleAll(a.tracks, a.name); setParams({ artist: null }); }}>🔀 Shuffle</button>
				{/if}
			</div>
			{#if artistLoading}<div class="sk-grid">{#each Array(6) as _}<div class="skeleton sk"></div>{/each}</div>
			{:else if !a.tracks.length}<p class="muted">No official music videos found for {a.name}.</p>
			{:else}{@render grid(a.tracks, a.name)}{/if}
		</div>
	</Modal>
{/if}

{#if creating}
	<Modal portal title="New mix" subtitle="Describe the mood and pick a curator — or start an empty mix" size="md" onClose={() => (creating = false)}>
		<form class="create" onsubmit={createMix}>
			<div class="kind">
				<button type="button" class="chip" class:on={newKind === 'curator'} use:focusable onclick={() => (newKind = 'curator')}>✨ Curated</button>
				<button type="button" class="chip" class:on={newKind === 'manual'} use:focusable onclick={() => (newKind = 'manual')}>💿 Build it myself</button>
			</div>
			{#if newKind === 'curator'}
				<textarea class="input" rows="3" maxlength="500" bind:value={newDesc} placeholder="e.g. Sunny 90s party songs everyone knows the words to" use:focusable></textarea>
				<div class="ideas">
					{#each IDEAS as idea}<button type="button" class="chip" use:focusable onclick={() => (newDesc = idea)}>{idea}</button>{/each}
				</div>
				<p class="muted small">Curator</p>
				<div class="curators">
					{#each curators as c (c.key)}
						<button type="button" class="curator" class:on={newCurator === c.key} style:--c={c.color} use:focusable onclick={() => (newCurator = c.key)}>
							<span class="ce">{c.emoji}</span><strong>{c.name}</strong><small>{c.taste}</small>
						</button>
					{/each}
				</div>
				<button class="btn primary big" use:focusable disabled={busy || !newDesc.trim()}>{busy ? 'Starting…' : `Ask ${curatorOf(newCurator)?.name ?? 'the curator'}`}</button>
			{:else}
				<input class="input" maxlength="80" bind:value={newName} placeholder="Mix name" use:focusable />
				<button class="btn primary big" use:focusable disabled={busy}>Create mix</button>
			{/if}
		</form>
	</Modal>
{/if}

<style>
	.music { display: grid; gap: 1.4rem; align-content: start; padding-bottom: 1rem; }
	.music.docked { padding-bottom: 11rem; } /* room for the docked player */
	.music :global(h1), .music :global(h2), .music :global(h3) { font-family: var(--font); }
	.top { display: flex; justify-content: space-between; align-items: center; gap: 1rem; flex-wrap: wrap; }
	.title { display: flex; align-items: baseline; gap: 0.8rem; flex-wrap: wrap; }
	.stored { color: var(--muted); font-size: 0.85rem; }
	.top h1 { margin: 0; font-size: var(--fs-2xl); font-weight: 800; letter-spacing: -0.02em; }
	.search { display: flex; gap: 0.5rem; flex: 1; max-width: 32rem; min-width: min(100%, 18rem); }
	.search .input { min-height: 2.9rem; flex: 1; }

	.hero { display: flex; gap: clamp(1rem, 3vw, 2.5rem); align-items: center; padding: clamp(1rem, 3vw, 2rem); border-radius: var(--r-lg); color: #fff; background: linear-gradient(120deg, #ff4fa3, #8f7cff 55%, #3fb6ff); box-shadow: 0 20px 50px -25px #ff4fa3; overflow: hidden; }
	.hero h2 { margin: 0 0 0.3rem; font-size: clamp(1.4rem, 2.4vw, 2.2rem); font-weight: 800; }
	.hero p { margin: 0 0 1rem; max-width: 46rem; opacity: 0.92; }
	.pitch { min-width: 0; }
	.disc { flex-shrink: 0; width: clamp(5rem, 11vw, 9rem); aspect-ratio: 1; border-radius: 50%; display: grid; place-items: center; background: repeating-radial-gradient(circle, #111 0 2px, #1d1d1d 3px 4px); box-shadow: 0 10px 30px rgba(0, 0, 0, 0.45); }
	.disc span { width: 34%; aspect-ratio: 1; border-radius: 50%; background: radial-gradient(circle, #111 0 12%, var(--accent) 13%); }
	.disc.spinning { animation: spin 2.4s linear infinite; }
	.disc.small { width: 2rem; display: inline-grid; vertical-align: middle; margin-right: 0.6rem; }
	.vibes { display: flex; gap: 0.5rem; flex-wrap: wrap; }
	.vibe { display: inline-flex; gap: 0.4rem; align-items: center; min-height: 2.9rem; padding: 0.45rem 1rem; border-radius: var(--r-pill); border: 2px solid rgba(255, 255, 255, 0.55); background: rgba(255, 255, 255, 0.16); color: #fff; font: inherit; font-weight: 700; cursor: pointer; transition: 0.15s ease; }
	.vibe span { font-size: 1.2rem; }
	.vibe:hover, .vibe:focus-visible { background: #fff; color: #3a1340; transform: translateY(-2px) rotate(-1deg); outline: none; }
	.vibe.fav { border-style: dashed; }

	.tabs { display: flex; gap: 0.5rem; flex-wrap: wrap; }
	.tab { min-height: 2.75rem; padding: 0.45rem 1.1rem; border-radius: var(--r-pill); border: 1px solid var(--line-strong); background: var(--surface); color: var(--text); font: inherit; font-weight: 600; cursor: pointer; }
	.tab.on { background: var(--accent); color: var(--accent-ink); border-color: transparent; }
	.tab:focus-visible { outline: 3px solid var(--accent); outline-offset: 2px; }

	.shelf { display: grid; gap: 0.8rem; }
	.shelf header { display: flex; justify-content: space-between; align-items: center; gap: 0.8rem; flex-wrap: wrap; }
	.shelf h2 { margin: 0; font-size: 1.3rem; font-weight: 800; }
	.acts { display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center; }
	.grid, .sk-grid { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fill, minmax(12.5rem, 1fr)); }
	.sk { aspect-ratio: 16 / 12; border-radius: var(--r-md); }
	.hint { margin: 0; padding: 1rem 1.2rem; border-radius: var(--r-md); border: 1px dashed var(--line-strong); color: var(--muted); }
	.err { color: var(--bad); }

	.mixes { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fill, minmax(13rem, 1fr)); }
	.mix { display: grid; gap: 0.35rem; align-content: start; text-align: start; padding: 0.7rem; border-radius: var(--r-lg); border: 1px solid var(--line-strong); background: linear-gradient(160deg, color-mix(in srgb, var(--mix, #ff4fa3) 26%, var(--surface-solid)), var(--surface-solid)); color: var(--text); font: inherit; cursor: pointer; transition: 0.15s ease; }
	.mix:hover, .mix:focus-visible { transform: translateY(-3px); outline: 3px solid var(--accent); outline-offset: 2px; }
	.mix small { color: var(--muted); }
	.covers { display: grid; grid-template-columns: 1fr 1fr; aspect-ratio: 16 / 10; border-radius: var(--r-md); overflow: hidden; background: rgba(0, 0, 0, 0.3); }
	.covers img { width: 100%; height: 100%; object-fit: cover; }
	.big-emoji { grid-column: 1 / -1; display: grid; place-items: center; font-size: 3rem; }
	.strip { display: grid; grid-auto-flow: column; grid-auto-columns: minmax(11rem, 13rem); gap: 0.8rem; overflow-x: auto; padding-bottom: 0.3rem; }
	.mix.small strong { font-size: 0.92rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
	.mix.new { place-content: center; justify-items: center; text-align: center; border-style: dashed; background: var(--surface); min-height: 12rem; }
	.plus { font-size: 2.6rem; line-height: 1; color: var(--accent); }
	.pulse { animation: blink 1.2s infinite; color: var(--accent); }

	.artists { display: flex; gap: 0.5rem; flex-wrap: wrap; }
	.artist-chip { min-height: 2.75rem; padding: 0.4rem 1.1rem; border-radius: var(--r-pill); border: 0; background: linear-gradient(120deg, #ffb020, #ff4fa3); color: #fff; font: inherit; font-weight: 700; cursor: pointer; }
	.artist-chip:focus-visible { outline: 3px solid var(--accent); outline-offset: 2px; }

	.sheet { display: grid; gap: 0.5rem; }
	.sheet .btn { justify-content: flex-start; min-height: 2.9rem; }
	.addto { display: flex; gap: 0.4rem; flex-wrap: wrap; }
	.mixview { display: grid; gap: 1rem; }
	.blurb { margin: 0; font-size: 1.05rem; }
	.building { display: flex; align-items: center; color: var(--muted); }
	.tracklist { list-style: none; margin: 0; padding: 0; display: grid; gap: 0.25rem; }
	.tracklist li { display: flex; align-items: center; gap: 0.2rem; border-radius: var(--r-sm); }
	.tracklist li.now { background: color-mix(in srgb, #ff4fa3 18%, transparent); }
	.row { flex: 1; display: flex; gap: 0.7rem; align-items: center; min-width: 0; padding: 0.35rem; background: none; border: 1px solid transparent; border-radius: var(--r-sm); color: inherit; font: inherit; text-align: start; cursor: pointer; }
	.row:hover, .row:focus-visible { background: var(--surface-2); border-color: var(--line-strong); }
	.row .n { width: 1.6rem; text-align: end; color: var(--faint); font-variant-numeric: tabular-nums; }
	.row img { width: 5rem; aspect-ratio: 16 / 9; object-fit: cover; border-radius: 0.35rem; }
	.tt { display: grid; min-width: 0; }
	.tt strong, .tt small { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
	.tt small { color: var(--muted); }
	.ic { background: none; border: 0; font-size: 1.1rem; padding: 0.35rem; border-radius: var(--r-sm); cursor: pointer; color: var(--text); }
	.ic:focus-visible { outline: 2px solid var(--accent); }

	.create { display: grid; gap: 0.8rem; }
	.kind, .ideas { display: flex; gap: 0.4rem; flex-wrap: wrap; }
	.curators { display: grid; gap: 0.5rem; grid-template-columns: repeat(auto-fill, minmax(10rem, 1fr)); }
	.curator { display: grid; gap: 0.15rem; text-align: start; padding: 0.6rem 0.7rem; border-radius: var(--r-md); border: 2px solid var(--line); background: var(--surface); color: var(--text); font: inherit; cursor: pointer; }
	.curator.on { border-color: var(--c); background: color-mix(in srgb, var(--c) 22%, var(--surface)); }
	.curator:focus-visible { outline: 3px solid var(--accent); }
	.curator small { color: var(--muted); font-size: 0.75rem; }
	.ce { font-size: 1.5rem; }

	@keyframes spin { to { transform: rotate(360deg); } }
	@keyframes blink { 50% { opacity: 0.35; } }
	@media (max-width: 640px) {
		.hero { flex-direction: column; align-items: start; }
		.grid, .sk-grid { grid-template-columns: repeat(auto-fill, minmax(10rem, 1fr)); }
	}
	@media (prefers-reduced-motion: reduce) { .disc.spinning, .pulse { animation: none; } }
</style>
