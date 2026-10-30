<script lang="ts">
	import { page } from '$app/state';
	import { deviceMode } from '$lib/stores/deviceMode.svelte';
	import { ALL_ENTRIES, MOBILE_PRIMARY, MOBILE_MORE, FIND, isHubPath } from '$lib/navigation';
	import { session } from '$lib/stores/session.svelte';
	import Icon from './Icon.svelte';

	let more = $state(false);
	let narrow = $state(false);
	const path = $derived(page.url.pathname);
	const primary = $derived(MOBILE_PRIMARY.filter(e => session.hubAllowed || !isHubPath(e.href)));
	const rest = $derived(ALL_ENTRIES.filter((e) => !MOBILE_PRIMARY.includes(e) && (session.hubAllowed || !isHubPath(e.href))));
	const moreActive = $derived(rest.some((e) => e.active(path)));

	$effect(() => {
		const mq = matchMedia('(max-width: 760px)');
		const sync = () => { narrow = mq.matches; if (!narrow) more = false; };
		sync();
		mq.addEventListener('change', sync);
		return () => mq.removeEventListener('change', sync);
	});
	$effect(() => { void path; more = false; });
	function onkey(e: KeyboardEvent) { if (more && e.key === 'Escape') more = false; }
</script>

<svelte:window onkeydown={onkey} />

{#if deviceMode.mode === 'desktop' && narrow}
	{#if more}
		<button class="backdrop" aria-label="Close menu" tabindex="-1" onclick={() => (more = false)}></button>
		<div class="sheet" role="dialog" aria-label="More navigation">
			<a href={session.hubAllowed ? FIND.href : '/search'} class="find"><Icon name={FIND.icon} /><span>{session.hubAllowed ? FIND.label : 'Video search'}</span></a>
			<div class="grid">
				{#each rest as it}
					{@const on = it.active(path)}
					<a href={it.href} aria-current={on ? 'page' : undefined} class:active={on}><Icon name={it.icon} /><span>{it.label}</span></a>
				{/each}
			</div>
		</div>
	{/if}
	<nav class="bar glass" aria-label="Primary" style:grid-template-columns={`repeat(${primary.length + 1}, 1fr)`}>
		{#each primary as it}
			{@const on = it.active(path)}
			<a href={it.href} aria-current={on ? 'page' : undefined} class:active={on}><Icon name={it.icon} size="1.45em" /><span>{it.label}</span></a>
		{/each}
		<button class:active={more || moreActive} aria-expanded={more} aria-haspopup="dialog" onclick={() => (more = !more)}>
			<Icon name={MOBILE_MORE.icon} size="1.45em" /><span>{MOBILE_MORE.label}</span>
		</button>
	</nav>
{/if}

<style>
	.bar { position: fixed; inset: auto 0 0 0; z-index: 120; display: grid; grid-template-columns: repeat(5, 1fr); padding: .25rem .3rem calc(.25rem + env(safe-area-inset-bottom)); background: var(--bg-1); border-width: 1px 0 0; }
	a, button { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: .15rem; min-height: 3rem; padding: .3rem; border: 0; border-radius: var(--r-md); background: none; color: var(--muted); font: 600 .68rem var(--font); cursor: pointer; }
	.active { color: var(--accent); }
	.bar .active { background: var(--surface-2); }
	a:focus-visible, button:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
	.backdrop { position: fixed; inset: 0; z-index: 125; border: 0; width: 100%; background: rgba(0,0,0,.55); }
	.sheet { position: fixed; inset: auto 0 calc(3.75rem + env(safe-area-inset-bottom)) 0; z-index: 130; max-height: 70vh; overflow-y: auto; padding: .9rem; background: var(--overlay-surface); border: 1px solid var(--line-strong); border-radius: var(--r-lg) var(--r-lg) 0 0; display: grid; gap: .7rem; }
	.grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: .5rem; }
	.grid a { background: var(--surface); min-height: 4.5rem; }
	.find { flex-direction: row; justify-content: flex-start; gap: .6rem; padding: .8rem 1rem; background: var(--surface-2); font-size: .95rem; color: var(--text); }
</style>
