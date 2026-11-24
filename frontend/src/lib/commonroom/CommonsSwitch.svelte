<script lang="ts">
	import { page } from '$app/state';
	import { focusable } from '$lib/nav';

	const SECTIONS = [
		{ href: '/common-room', label: 'Subs' },
		{ href: '/memes', label: 'Meme Radar' }
	];
</script>

<!-- The Commons holds both community threads and the Meme Radar; one switch on both pages. -->
<nav class="commons-switch" aria-label="The Commons">
	{#each SECTIONS as s (s.href)}
		{@const on = page.url.pathname === s.href || page.url.pathname.startsWith(s.href + '/')}
		<a href={s.href} class:on aria-current={on ? 'page' : undefined} use:focusable>{s.label}</a>
	{/each}
</nav>

<style>
	.commons-switch { display: inline-flex; gap: .25rem; padding: .25rem; border-radius: 999px; background: var(--surface-2); border: 1px solid var(--line); width: fit-content; }
	a { padding: .4rem .95rem; border-radius: 999px; font-weight: 600; font-size: .9rem; color: var(--muted); text-decoration: none; }
	a:hover { color: var(--text); }
	a.on { background: var(--surface-hi); color: var(--text); box-shadow: 0 1px 3px rgba(0, 0, 0, .15); }
</style>
