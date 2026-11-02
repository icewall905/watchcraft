<script lang="ts">
	// Languages this profile does not want in Meme Radar. Hiding one filters the feed;
	// nothing is deleted, and the same switch brings it back.
	import { onMount } from 'svelte';
	import Section from './Section.svelte';
	import { focusable } from '$lib/nav';
	import { hub } from '$lib/hub/api';
	import { errMsg } from '$lib/hub/util';
	import { toast } from '$lib/stores/toast.svelte';

	let languages = $state<{ key: string; count: number }[]>([]);
	let hidden = $state<string[]>([]);
	let loading = $state(true);
	let error = $state('');
	let busy = $state('');

	const name = (code: string) => {
		try { return new Intl.DisplayNames(['en'], { type: 'language' }).of(code) ?? code.toUpperCase(); }
		catch { return code.toUpperCase(); }
	};

	async function load() {
		try {
			const f = await hub.memeFacets();
			languages = f.languages;
			hidden = f.hidden_languages ?? [];
			error = '';
		} catch (e) { error = errMsg(e); }
		finally { loading = false; }
	}
	onMount(load);

	async function toggle(code: string) {
		busy = code;
		try {
			const r = await hub.hideMemeLanguage(code, !hidden.includes(code));
			hidden = r.hidden_languages;
			toast.success(hidden.includes(code) ? `${name(code)} memes are hidden.` : `${name(code)} memes are back.`);
		} catch (e) { toast.error(errMsg(e)); }
		finally { busy = ''; }
	}
</script>

<Section title="Meme languages"
	intro="Languages you do not want in Meme Radar. Hiding one removes its memes from the feed for this profile; nothing is deleted, and the same switch brings it back.">
	{#if loading}
		<p class="muted">Loading…</p>
	{:else if error}
		<p class="err" role="alert">{error}</p>
	{:else if !languages.length}
		<p class="muted">No memes collected yet, so there is nothing to choose from.</p>
	{:else}
		<ul class="langs">
			{#each languages as l (l.key)}
				<li>
					<span>{name(l.key)} <span class="faint small">{l.key} · {l.count} meme{l.count === 1 ? '' : 's'}</span></span>
					<button class="btn" class:on={hidden.includes(l.key)} aria-pressed={hidden.includes(l.key)}
						disabled={busy === l.key} use:focusable onclick={() => toggle(l.key)}>
						{hidden.includes(l.key) ? 'Hidden' : 'Shown'}
					</button>
				</li>
			{/each}
		</ul>
	{/if}
	{#if hidden.length}
		<p class="faint small">Hidden: {hidden.map(name).join(', ')}</p>
	{/if}
</Section>

<style>
	.langs { display: grid; gap: .4rem; list-style: none; padding: 0; margin: 0; }
	.langs li { display: flex; align-items: center; justify-content: space-between; gap: 1rem; padding: .5rem .7rem; border: 1px solid var(--line); border-radius: var(--r-md); background: rgba(255, 255, 255, .03); }
	.langs .btn { min-height: 2.5rem; }
	.langs .btn.on { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 18%, transparent); }
	.err { color: var(--bad); }
</style>
