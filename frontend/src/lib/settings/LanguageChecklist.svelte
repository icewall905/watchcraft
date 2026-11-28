<script lang="ts">
	// Pick the languages to show. Nothing chosen shows every language; text whose language is unknown always shows.
	import { focusable } from '$lib/nav';
	import { errMsg } from '$lib/hub/util';
	import { toast } from '$lib/stores/toast.svelte';

	let { counts, chosen = $bindable(), save, noun = 'posts' }:
		{ counts: Record<string, number>; chosen: string[]; save: (next: string[]) => Promise<string[]>; noun?: string } = $props();

	const COMMON = ['en', 'da', 'sv', 'no', 'de', 'nl', 'fr', 'es', 'it', 'pt', 'pl', 'fi', 'uk', 'ru', 'tr', 'ar', 'hi', 'ja', 'ko', 'zh'];
	let busy = $state(false);
	const name = (code: string) => {
		try { return new Intl.DisplayNames(['en'], { type: 'language' }).of(code) ?? code.toUpperCase(); }
		catch { return code.toUpperCase(); }
	};
	// Languages already collected first (most first), then common ones, then anything chosen earlier.
	const options = $derived([...new Set([...Object.keys(counts), ...COMMON, ...chosen])]);

	async function apply(next: string[], message: string) {
		busy = true;
		try { chosen = await save(next); toast.success(message); }
		catch (e) { toast.error(errMsg(e)); }
		finally { busy = false; }
	}
	const toggle = (code: string) => chosen.includes(code)
		? apply(chosen.filter((c) => c !== code), chosen.length === 1 ? 'No language chosen: every language is shown.' : `${name(code)} ${noun} are no longer shown.`)
		: apply([...chosen, code], `${name(code)} ${noun} are shown.`);
</script>

<p class="small" role="status">
	{chosen.length ? `Showing: ${chosen.map(name).join(', ')}.` : 'No language chosen: every language is shown.'}
	{#if chosen.length}<button class="link" disabled={busy} use:focusable onclick={() => apply([], 'Every language is shown.')}>Show all languages</button>{/if}
</p>
<ul class="langs" aria-label="Languages to show">
	{#each options as code (code)}
		<li>
			<label>
				<input type="checkbox" checked={chosen.includes(code)} disabled={busy} use:focusable onchange={() => toggle(code)} />
				<span>{name(code)} <span class="faint small">{code}{counts[code] ? ` · ${counts[code]} ${counts[code] === 1 ? noun.replace(/s$/, '') : noun}` : ''}</span></span>
			</label>
		</li>
	{/each}
</ul>

<style>
	.langs { display: grid; grid-template-columns: repeat(auto-fill, minmax(13rem, 1fr)); gap: .4rem; list-style: none; padding: 0; margin: .6rem 0 0; }
	.langs label { display: flex; align-items: center; gap: .6rem; min-height: 2.5rem; padding: .4rem .7rem; border: 1px solid var(--line); border-radius: var(--r-md); background: rgba(255, 255, 255, .03); cursor: pointer; }
	.langs input { width: 1.1rem; height: 1.1rem; accent-color: var(--accent); }
	.link { background: none; border: 0; padding: 0 0 0 .4rem; color: var(--accent); text-decoration: underline; cursor: pointer; font: inherit; }
</style>
