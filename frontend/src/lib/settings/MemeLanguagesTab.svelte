<script lang="ts">
	// The languages this profile wants in Meme Radar. Nothing chosen shows every language; once some are
	// chosen, only those are shown, including against languages that turn up later. Memes whose language
	// could not be detected are always shown. Nothing is deleted.
	import { onMount } from 'svelte';
	import Section from './Section.svelte';
	import { focusable } from '$lib/nav';
	import { hub } from '$lib/hub/api';
	import { errMsg } from '$lib/hub/util';
	import { toast } from '$lib/stores/toast.svelte';

	const COMMON = ['en', 'da', 'sv', 'no', 'de', 'nl', 'fr', 'es', 'it', 'pt', 'pl', 'fi', 'uk', 'ru', 'tr', 'ar', 'hi', 'ja', 'ko', 'zh'];
	let counts = $state<Record<string, number>>({});
	let chosen = $state<string[]>([]);
	let hidden = $state<string[]>([]);
	let loading = $state(true);
	let error = $state('');
	let busy = $state(false);

	const name = (code: string) => {
		try { return new Intl.DisplayNames(['en'], { type: 'language' }).of(code) ?? code.toUpperCase(); }
		catch { return code.toUpperCase(); }
	};
	// Languages already collected first (most memes first), then common ones, then anything chosen earlier.
	const options = $derived([...new Set([...Object.keys(counts), ...COMMON, ...chosen])]);

	async function load() {
		try {
			const f = await hub.memeFacets();
			counts = Object.fromEntries(f.languages.map((l) => [l.key, l.count]));
			chosen = f.allowed_languages ?? [];
			hidden = f.hidden_languages ?? [];
			error = '';
		} catch (e) { error = errMsg(e); }
		finally { loading = false; }
	}
	onMount(load);

	async function save(next: string[], message: string) {
		busy = true;
		try {
			const r = await hub.setMemeLanguages(next);
			chosen = r.languages;
			hidden = r.hidden_languages;
			toast.success(message);
		} catch (e) { toast.error(errMsg(e)); }
		finally { busy = false; }
	}
	const toggle = (code: string) => chosen.includes(code)
		? save(chosen.filter((c) => c !== code), chosen.length === 1 ? 'No language chosen: every language is shown.' : `${name(code)} memes are no longer shown.`)
		: save([...chosen, code], `${name(code)} memes are shown.`);
</script>

<Section title="Meme languages"
	intro="Choose the languages you want in Meme Radar. Only those are shown, also when new languages turn up later. Memes whose language could not be detected are always shown, and nothing is deleted.">
	{#if loading}
		<p class="muted">Loading…</p>
	{:else if error}
		<p class="err" role="alert">{error}</p>
	{:else}
		<p class="small" role="status">
			{chosen.length ? `Showing: ${chosen.map(name).join(', ')}.` : 'No language chosen: every language is shown.'}
			{#if chosen.length}<button class="link" disabled={busy} use:focusable onclick={() => save([], 'Every language is shown.')}>Show all languages</button>{/if}
		</p>
		<ul class="langs" aria-label="Languages to show">
			{#each options as code (code)}
				<li>
					<label>
						<input type="checkbox" checked={chosen.includes(code)} disabled={busy} use:focusable onchange={() => toggle(code)} />
						<span>{name(code)} <span class="faint small">{code}{counts[code] ? ` · ${counts[code]} meme${counts[code] === 1 ? '' : 's'}` : ''}</span></span>
					</label>
				</li>
			{/each}
		</ul>
		{#if hidden.length}
			<p class="faint small">Also hidden after a thumbs-down: {hidden.map(name).join(', ')}. Choosing a language above shows it again.</p>
		{/if}
	{/if}
</Section>

<style>
	.langs { display: grid; grid-template-columns: repeat(auto-fill, minmax(13rem, 1fr)); gap: .4rem; list-style: none; padding: 0; margin: .6rem 0 0; }
	.langs label { display: flex; align-items: center; gap: .6rem; min-height: 2.5rem; padding: .4rem .7rem; border: 1px solid var(--line); border-radius: var(--r-md); background: rgba(255, 255, 255, .03); cursor: pointer; }
	.langs input { width: 1.1rem; height: 1.1rem; accent-color: var(--accent); }
	.link { background: none; border: 0; padding: 0 0 0 .4rem; color: var(--accent); text-decoration: underline; cursor: pointer; font: inherit; }
	.err { color: var(--bad); }
</style>
