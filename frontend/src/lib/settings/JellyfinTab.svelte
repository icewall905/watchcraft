<script lang="ts">
	import { onMount } from 'svelte';
	import { focusable } from '$lib/nav';
	import { session } from '$lib/stores/session.svelte';
	import { toast } from '$lib/stores/toast.svelte';
	import LinkForm from '$lib/jellyfin/LinkForm.svelte';
	import { jellyfin, type Encoding, type Status } from '$lib/jellyfin/api';
	import Section from './Section.svelte';

	let status = $state<Status | null>(null);
	let url = $state('');
	let allowChildren = $state(false);
	let saving = $state(false);
	let error = $state('');
	let enc = $state<Encoding | null>(null);
	const admin = $derived(session.isAdmin);

	async function refresh() {
		status = await jellyfin.status();
		if (admin) {
			const c = await jellyfin.config();
			url = c.url;
			allowChildren = c.allow_children;
			if (status.configured) enc = await jellyfin.encoding().catch(() => null);
		}
	}

	async function save(e: Event) {
		e.preventDefault();
		if (saving) return;
		saving = true;
		error = '';
		try {
			const r = await jellyfin.saveConfig(url, allowChildren);
			url = r.url;
			toast.success(r.server_name ? `Connected to ${r.server_name} (Jellyfin ${r.version}).` : 'Jellyfin removed.');
			await refresh();
		} catch (err) {
			error = (err as Error).message;
		} finally {
			saving = false;
		}
	}

	async function unlink() {
		status = await jellyfin.unlink();
	}

	onMount(() => { void refresh().catch(() => {}); });
</script>

{#if admin}
	<Section title="Jellyfin server" intro="Browse and play your Jellyfin movies and shows in The Cinema. The Hub talks to Jellyfin from the server and passes video through, so the Jellyfin address only has to be reachable from this server. Needs Jellyfin 10.9 or newer.">
		<form class="cfg" onsubmit={save}>
			<label>Address
				<input placeholder="http://192.168.1.20:8096" bind:value={url} use:focusable={{ group: 'jf-cfg' }} />
			</label>
			<label class="check">
				<input type="checkbox" bind:checked={allowChildren} use:focusable={{ group: 'jf-cfg-kids' }} />
				Child profiles may use Jellyfin (link them to Jellyfin users with parental controls)
			</label>
			{#if error}<p class="err" role="alert">{error}</p>{/if}
			<button class="btn primary" type="submit" disabled={saving} use:focusable={{ group: 'jf-cfg-save' }}>{saving ? 'Testing…' : 'Test and save'}</button>
		</form>
	</Section>

	{#if status?.configured}
		<Section title="Hardware transcoding" intro="Jellyfin does the transcoding. Choose the hardware encoder in the Jellyfin dashboard under Playback → Transcoding, and give the Jellyfin container the GPU (/dev/dri for Intel and AMD, the NVIDIA runtime for NVIDIA). The Hub direct-plays whatever this device can decode and asks for H.264 otherwise, which every hardware encoder supports.">
			{#if !enc}
				<p class="muted">Checking…</p>
			{:else if !enc.known}
				<p class="muted">{enc.reason}</p>
			{:else if enc.hardware}
				<p><strong>{enc.hardware.toUpperCase()}</strong> is on.
					{#if enc.decoding_codecs?.length}Hardware decoding: {enc.decoding_codecs.join(', ')}.{/if}
					{enc.hevc_encoding ? 'HEVC encoding allowed.' : ''} {enc.av1_encoding ? 'AV1 encoding allowed.' : ''}
					{enc.tonemapping ? 'HDR tone mapping on.' : ''}</p>
			{:else}
				<p class="warn">Jellyfin is set to software transcoding. Transcodes will use the server's CPU; turn on hardware acceleration in Jellyfin for smooth 4K and several streams at once.</p>
			{/if}
			<p class="small muted">While a video plays, the player's ⓘ menu shows whether it is direct, remuxed or transcoded and which encoder is used.</p>
		</Section>
	{/if}
{/if}

<Section title="Your Jellyfin account" intro="Each profile signs in as its own Jellyfin user, so watched state, resume points and parental controls stay separate.">
	{#if !status}
		<p class="muted">Loading…</p>
	{:else if !status.configured}
		<p class="muted">{admin ? 'Add the server above first.' : 'An administrator has not added a Jellyfin server yet.'}</p>
	{:else if !status.allowed}
		<p class="muted">Jellyfin is not available for this profile.</p>
	{:else if status.linked}
		<p>Signed in to {status.server_name || 'Jellyfin'} as <strong>{status.user_name}</strong>{status.is_admin ? ' (Jellyfin administrator)' : ''}.</p>
		<div><button class="btn danger" use:focusable={{ group: 'jf-unlink', onSelect: unlink }}>Sign out of Jellyfin</button></div>
	{:else}
		<LinkForm onLinked={(s) => { status = s; void refresh(); }} />
	{/if}
</Section>

<style>
	.cfg { display: grid; gap: 0.8rem; max-width: 32rem; }
	label { display: grid; gap: 0.3rem; }
	.check { display: flex; gap: 0.5rem; align-items: center; }
	.err { color: var(--bad); margin: 0; }
	.warn { color: var(--warn); }
</style>
