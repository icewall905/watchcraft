<script lang="ts">
	import { focusable } from '$lib/nav';
	import { direction, safeHref, shortDate, surfaceIcon, verb, type Teaching } from './api';
	// `showText` off: only the signals (a teaching card already shows its own text).
	let { teachings, showText = true }: { teachings: Teaching[]; showText?: boolean } = $props();
</script>

<ul class="why">
	{#each teachings as t (t.id)}
		<li>
			{#if showText}<p class="small"><strong>{direction(t.polarity)}:</strong> {t.text}{#if t.user_authored} <span class="badge">You told me</span>{/if}</p>{/if}
			{#if t.signals.length}
				<ul class="signals">
					{#each t.signals as s, i (i)}
						<li class="signal small">
							<span class="icon" aria-hidden="true">{surfaceIcon(s.surface)}</span>
							<span class="verb">{verb(s.kind)}</span>
							{#if safeHref(s.url)}<a href={safeHref(s.url)} target="_blank" rel="noopener noreferrer" use:focusable>{s.title || s.url}</a>{:else if s.title}<span>{s.title}</span>{/if}
							{#if s.board}<span class="muted">· {s.board}</span>{/if}
							{#if s.note}<q class="muted">{s.note}</q>{/if}
							<time class="muted" datetime={new Date(s.at * 1000).toISOString()}>{shortDate(s.at)}</time>
						</li>
					{/each}
				</ul>
			{:else if t.reason}<p class="small muted">{t.reason}</p>{/if}
		</li>
	{:else}<li class="small muted">No recorded signals behind this.</li>{/each}
</ul>

<style>
	.why, .signals { list-style: none; margin: 0; padding: 0; display: grid; gap: 0.5rem; }
	.signals { gap: 0.3rem; padding-left: 0.25rem; }
	.signal { display: flex; flex-wrap: wrap; gap: 0.35rem; align-items: baseline; overflow-wrap: anywhere; }
	.verb { font-weight: 600; }
	a { color: var(--info); }
	.badge { font-size: 0.75rem; padding: 0.05rem 0.45rem; border-radius: var(--r-pill); background: var(--surface-2); border: 1px solid var(--line); }
</style>
