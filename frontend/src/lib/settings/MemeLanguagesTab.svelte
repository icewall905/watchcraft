<script lang="ts">
	// The languages this profile wants in Meme Radar. Nothing chosen shows every language; once some are
	// chosen, only those are shown, including against languages that turn up later. Memes whose language
	// could not be detected are always shown. Nothing is deleted.
	import { onMount } from 'svelte';
	import Section from './Section.svelte';
	import LanguageChecklist from './LanguageChecklist.svelte';
	import { hub } from '$lib/hub/api';
	import { errMsg } from '$lib/hub/util';

	let counts = $state<Record<string, number>>({});
	let chosen = $state<string[]>([]);
	let hidden = $state<string[]>([]);
	let loading = $state(true);
	let error = $state('');
	const name = (code: string) => {
		try { return new Intl.DisplayNames(['en'], { type: 'language' }).of(code) ?? code.toUpperCase(); }
		catch { return code.toUpperCase(); }
	};

	onMount(async () => {
		try {
			const f = await hub.memeFacets();
			counts = Object.fromEntries(f.languages.map((l) => [l.key, l.count]));
			chosen = f.allowed_languages ?? [];
			hidden = f.hidden_languages ?? [];
		} catch (e) { error = errMsg(e); }
		finally { loading = false; }
	});
	async function save(next: string[]) {
		const r = await hub.setMemeLanguages(next);
		hidden = r.hidden_languages;
		return r.languages;
	}
</script>

<Section title="Meme languages"
	intro="Choose the languages you want in Meme Radar. Only those are shown, also when new languages turn up later. Memes whose language could not be detected are always shown, and nothing is deleted.">
	{#if loading}
		<p class="muted">Loading…</p>
	{:else if error}
		<p class="err" role="alert">{error}</p>
	{:else}
		<LanguageChecklist {counts} bind:chosen {save} noun="memes" />
		{#if hidden.length}
			<p class="faint small">Also hidden after a thumbs-down: {hidden.map(name).join(', ')}. Choosing a language above shows it again.</p>
		{/if}
	{/if}
</Section>

<style>
	.err { color: var(--bad); }
</style>
