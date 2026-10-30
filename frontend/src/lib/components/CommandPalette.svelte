<script lang="ts">
	import { tick } from 'svelte';
	import { goto } from '$app/navigation';
	import { deviceMode } from '$lib/stores/deviceMode.svelte';
	import { palette } from '$lib/stores/palette.svelte';
	import { nav, navScope, focusable } from '$lib/nav';
	import { onMount } from 'svelte';
	import { ALL_ENTRIES, FIND, isHubPath } from '$lib/navigation';
	import { session } from '$lib/stores/session.svelte';
	import Icon from './Icon.svelte';
	import VoiceSearch from './VoiceSearch.svelte';

	let q = $state('');
	let idx = $state(-1);
	let voiceActive = $state(false);
	let input = $state<HTMLInputElement>();
	let restore: HTMLElement | null = null;

	const results = $derived(ALL_ENTRIES.filter((e) => (session.hubAllowed || !isHubPath(e.href)) && e.label.toLowerCase().includes(q.trim().toLowerCase())));

	$effect(() => { void q; idx = -1; });
	$effect(() => {
		if (!palette.open) return;
		restore = document.activeElement as HTMLElement | null;
		q = '';
		void tick().then(() => input?.focus());
	});
	function close() { palette.hide(); restore?.focus?.(); }
	function go(href: string) { palette.hide(); void goto(href); }
	function submit() {
		if (idx >= 0 && results[idx]) return go(results[idx].href);
		const t = q.trim();
		const href = session.hubAllowed ? FIND.href : '/search';
		go(t ? `${href}?q=${encodeURIComponent(t)}` : href);
	}
	function onkey(e: KeyboardEvent) {
		if (deviceMode.mode !== 'desktop') return;
		if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
			e.preventDefault();
			if (palette.open) close(); else palette.show();
		}
	}
	function ondialogkey(e: KeyboardEvent) {
		if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); }
	}
	// The remote-navigation engine owns arrows (capture phase); steer the list through it.
	onMount(() => nav.handleKeys((key) => {
		if (!palette.open || voiceActive) return;
		if (key === 'down') { idx = Math.min(idx + 1, results.length - 1); return true; }
		if (key === 'up') { idx = Math.max(idx - 1, -1); return true; }
	}));
</script>

<svelte:window onkeydown={onkey} />

{#if palette.open && deviceMode.mode === 'desktop'}
	<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
	<div class="wrap" role="dialog" aria-modal="true" aria-label="Search everything" tabindex="-1" onkeydown={ondialogkey} use:navScope={{ onBack: close }}>
		<button class="backdrop" aria-label="Close search" tabindex="-1" onclick={close}></button>
		<div class="box">
			<form class="field" onsubmit={(e) => { e.preventDefault(); submit(); }}>
				<Icon name="search" />
				<input bind:this={input} bind:value={q} use:focusable={{ group: 'palette', osk: false, scroll: 'none', onSelect: submit }} placeholder="Search everything, or jump to…" aria-label="Search everything" role="combobox"
					aria-expanded="true" aria-controls="palette-list" aria-activedescendant={idx >= 0 ? `pal-${idx}` : undefined} autocomplete="off" />
				<VoiceSearch group="palette" onactive={(active) => voiceActive = active} onresult={(text) => {
					q = text; idx = -1; submit();
				}} />
				<kbd>Esc</kbd>
			</form>
			<ul id="palette-list" role="listbox" aria-label="Destinations">
				{#each results as r, i}
					<li id="pal-{i}" role="option" aria-selected={i === idx} class:sel={i === idx}>
						<button tabindex="-1" onmouseenter={() => (idx = i)} onclick={() => go(r.href)}><Icon name={r.icon} /><span>{r.label}</span><small>{r.href}</small></button>
					</li>
				{/each}
				{#if !results.length}<li class="none" role="presentation">No destination matches. Press Enter to search.</li>{/if}
			</ul>
			<p class="hint">Enter searches everything for “{q.trim() || '…'}” · ↑↓ to jump to a page</p>
		</div>
	</div>
{/if}

<style>
	.wrap { position: fixed; inset: 0; z-index: 300; display: grid; justify-items: center; align-content: start; padding-top: 14vh; }
	.backdrop { position: absolute; inset: 0; width: 100%; border: 0; background: rgba(5,4,14,.65); }
	.box { position: relative; width: min(36rem, calc(100vw - 2rem)); background: var(--overlay-surface); border: 1px solid var(--line-strong); border-radius: var(--r-lg); box-shadow: var(--shadow-lg); overflow: hidden; }
	.field { display: flex; align-items: center; gap: .7rem; padding: .9rem 1.1rem; border-bottom: 1px solid var(--line); color: var(--muted); }
	input { flex: 1; background: none; border: 0; outline: none; color: var(--text); font: 500 1.05rem var(--font); }
	kbd { font: 600 .7rem var(--font); padding: .15rem .45rem; border: 1px solid var(--line-strong); border-radius: var(--r-sm); color: var(--faint); }
	ul { list-style: none; margin: 0; padding: .4rem; max-height: 50vh; overflow-y: auto; }
	li button { display: flex; align-items: center; gap: .75rem; width: 100%; padding: .65rem .75rem; border: 0; border-radius: var(--r-md); background: none; color: var(--muted); font: 600 .95rem var(--font); cursor: pointer; text-align: left; }
	li button span { flex: 1; }
	li small { color: var(--faint); font-weight: 500; }
	li.sel button { background: var(--surface-2); color: var(--text); box-shadow: inset 3px 0 var(--accent); }
	.none, .hint { padding: .6rem .9rem; color: var(--faint); font-size: .8rem; margin: 0; }
	.hint { border-top: 1px solid var(--line); }
</style>
