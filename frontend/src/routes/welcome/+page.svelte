<script lang="ts">
	import { goto } from '$app/navigation';
	import { api } from '$lib/api';
	import { focusable } from '$lib/nav';
	import { session } from '$lib/stores/session.svelte';
	import TextField from '$lib/components/form/TextField.svelte';
	import AiTab from '$lib/settings/AiTab.svelte';

	let step = $state(0);
	let name = $state(session.profile?.name ?? 'Family');
	let playlist = $state('');
	let brief = $state('');
	let busy = $state(false);
	let error = $state('');
	let connecting = $state(false);
	const steps = ['Who is watching?', 'Connect your AI', 'Your first playlist'];

	async function run(action: () => Promise<void>) {
		if (busy) return;
		busy = true; error = '';
		try { await action(); }
		catch (e) { error = (e as Error).message; }
		finally { busy = false; }
	}
	async function saveProfile() {
		if (!name.trim()) return;
		await run(async () => {
			if (session.profile) await api.updateProfile(session.profile.id, { name: name.trim() });
			else {
				const profile = await api.createProfile({ name: name.trim(), emoji: '🏠', color: '#7c5cff' });
				await session.loadProfiles();
				await session.switchProfile(profile.id);
			}
			await session.loadProfiles();
			step = 1;
		});
	}
	async function finish(destination = '/') {
		const setup = await api.saveSettings('setup', { completed: true });
		if (session.settings) session.settings = { ...session.settings, setup };
		await goto(destination);
	}
	async function skipAi() {
		await run(async () => {
			const agent = await api.saveSettings('agent', { enabled: false });
			if (session.settings) session.settings = { ...session.settings, agent };
			await finish('/search');
		});
	}
	async function savePlaylist() {
		if (!playlist.trim() || !brief.trim()) return;
		await run(async () => {
			await api.createCategory({ name: playlist.trim(), brief: brief.trim() });
			await session.loadCategories();
			await finish();
		});
	}
</script>

<svelte:head><title>Welcome to WatchCraft</title></svelte:head>

<div class="page welcome">
	<header>
		<img src="/logo.png" alt="" width="56" height="56" />
		<div><h1>Make yourself at home.</h1><p class="muted">A few choices, then you're ready to watch. You can change them later in Settings.</p></div>
	</header>
	<ol aria-label="Setup progress">
		{#each steps as label, i}
			<li class:current={step === i} class:done={step > i} aria-current={step === i ? 'step' : undefined}><span>{step > i ? '✓' : i + 1}</span>{label}</li>
		{/each}
	</ol>
	{#if error}<p role="alert" class="error">{error} Try again, or check that WatchCraft is still running.</p>{/if}
	{#if step === 0}
		<section>
			<h2>Who is watching?</h2>
			<p class="muted">Name this profile for yourself or the whole household. You can add more people later, each with their own playlists and history.</p>
			<TextField label="Profile name" bind:value={name} autofocus group="welcome-name" onsubmit={saveProfile} />
			<button class="btn primary big" disabled={busy || !name.trim()} use:focusable={{ group: 'welcome-profile', onSelect: saveProfile }}>Save profile and continue</button>
		</section>
	{:else if step === 1}
		<section>
			<h2>Let AI find things you'll enjoy.</h2>
			<p class="muted">Connect a model you already run or a service you use. WatchCraft doesn't install an AI model. Hosted services may charge for use. Prefer to browse yourself? Skip this step and connect AI later.</p>
			<div class="actions">
				<button class="btn" disabled={busy || connecting} use:focusable={{ group: 'welcome-ai', onSelect: skipAi }}>Skip AI and start browsing</button>
				<button class="btn" disabled={connecting} use:focusable={{ group: 'welcome-ai', onSelect: () => (step = 0) }}>Back</button>
			</div>
			<AiTab connectionOnly onConnecting={(value) => (connecting = value)} onConnected={() => (step = 2)} />
		</section>
	{:else}
		<section>
			<h2>What would you like to watch?</h2>
			<p class="muted">A playlist tells the curator what to look for. Be specific about what you enjoy and what to avoid. New picks appear gradually as the curator searches.</p>
			<TextField label="Playlist name" bind:value={playlist} placeholder="e.g. Weekend woodworking" autofocus group="welcome-playlist" />
			<TextField label="What should the curator look for?" bind:value={brief} multiline rows={4} placeholder="Calm furniture builds with clear explanations. Hand tools welcome; skip sponsored tool reviews." group="welcome-playlist" />
			<div class="actions">
				<button class="btn primary big" disabled={busy || !playlist.trim() || !brief.trim()} use:focusable={{ group: 'welcome-finish', onSelect: savePlaylist }}>Create playlist and start watching</button>
				<button class="btn" disabled={busy} use:focusable={{ group: 'welcome-finish', onSelect: () => run(() => finish()) }}>I'll make a playlist later</button>
			</div>
		</section>
	{/if}
	<footer><p class="small muted">Already set up? <a href="/settings" use:focusable={{ group: 'welcome-exit' }}>Open Settings</a>. This guide is always available from Settings.</p></footer>
</div>

<style>
	.welcome { max-width: 58rem; margin-inline: auto; }
	header { display: flex; align-items: center; gap: 1.1rem; margin-block: 1.5rem 2rem; }
	header img { flex: none; }
	h1 { font-size: clamp(1.8rem, 4vw, 2.8rem); line-height: 1.15; margin-bottom: 0.6rem; }
	h2 { font-size: 1.5rem; }
	p { max-width: 65ch; line-height: 1.65; }
	ol { list-style: none; padding: 0; display: flex; flex-wrap: wrap; gap: 1rem 2rem; padding-block: 1rem; border-block: 1px solid var(--line); margin-bottom: 2rem; }
	li { display: flex; align-items: center; gap: 0.6rem; color: var(--muted); }
	li span { width: 1.9rem; height: 1.9rem; display: grid; place-items: center; border: 1px solid var(--line-strong); border-radius: 50%; }
	li.current { color: var(--text); font-weight: 650; }
	li.current span { background: var(--accent); color: var(--accent-ink); border-color: var(--accent); }
	li.done span { color: var(--accent); }
	section { display: grid; gap: 1.1rem; }
	.actions { display: flex; gap: 0.8rem; flex-wrap: wrap; }
	footer { margin-top: 2rem; border-top: 1px solid var(--line); padding-top: 1rem; }
	footer a { text-decoration: underline; }
	.error { color: var(--bad); }
	@media (max-width: 600px) { header { align-items: start; } header img { width: 40px; height: 40px; } ol { display: grid; gap: 0.6rem; } }
</style>
