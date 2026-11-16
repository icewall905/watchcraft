<script lang="ts">
	import type { Snippet } from 'svelte';
	import { navScope } from '$lib/nav';

	interface Props {
		title?: string;
		subtitle?: string;
		onClose: () => void;
		size?: 'sm' | 'md' | 'lg' | 'full' | 'drawer';
		children: Snippet;
		footer?: Snippet;
		portal?: boolean;
	}
	let { title, subtitle, onClose, size = 'md', children, footer, portal = false }: Props = $props();

	// A transformed or blurred ancestor traps fixed-position overlays. Move
	// selected dialogs to the document root so their backdrop covers the screen.
	function moveToRoot(node: HTMLElement, enabled: boolean) {
		if (enabled) document.body.appendChild(node);
		return { destroy: () => { if (enabled) node.remove(); } };
	}
</script>

<div class="backdrop {size}" role="presentation" use:moveToRoot={portal} onclick={(e) => e.target === e.currentTarget && onClose()}>
	<div class="modal {size}" role="dialog" aria-modal="true" aria-label={title} use:navScope={{ onBack: onClose }}>
		{#if title}
			<header>
				<h2>{title}</h2>
				{#if subtitle}<p class="muted small">{subtitle}</p>{/if}
			</header>
		{/if}
		<div class="body">{@render children()}</div>
		{#if footer}<footer>{@render footer()}</footer>{/if}
	</div>
</div>

<style>
	.backdrop {
		position: fixed;
		inset: 0;
		z-index: 500;
		display: grid;
		place-items: center;
		padding: clamp(0.75rem, 3vw, 3rem);
		background: rgba(5, 4, 14, 0.62);
		backdrop-filter: blur(6px);
		-webkit-backdrop-filter: blur(6px);
		animation: fade 0.2s var(--ease);
	}
	.backdrop.drawer {
		place-items: stretch end;
		padding: 0;
	}
	header h2 {
		overflow-wrap: anywhere;
	}
	.modal {
		min-width: 0;
		position: relative;
		width: min(100%, 38rem);
		max-height: min(92dvh, 64rem);
		display: flex;
		flex-direction: column;
		border-radius: var(--r-lg);
		background: linear-gradient(180deg, var(--modal-top), var(--modal-bottom));
		border: 1px solid var(--line-strong);
		box-shadow: var(--shadow-lg), 0 0 0 1px rgba(0, 0, 0, 0.3);
		animation: pop 0.28s var(--ease-spring);
	}
	.modal.sm {
		width: min(100%, 28rem);
	}
	.modal.lg {
		width: min(100%, 64rem);
	}
	.modal.full {
		width: min(100%, 90rem);
		height: min(92dvh, 64rem);
	}
	.modal.drawer {
		width: min(100%, 44rem);
		height: 100dvh;
		max-height: none;
		border-radius: var(--r-lg) 0 0 var(--r-lg);
		animation: slide 0.32s var(--ease);
	}
	header {
		padding: clamp(1rem, 1.6vw, 1.75rem) clamp(1rem, 1.8vw, 2rem) 0.5rem;
		display: grid;
		gap: 0.3rem;
	}
	.body {
		padding: 0.75rem clamp(1rem, 1.8vw, 2rem) clamp(1rem, 1.6vw, 1.75rem);
		overflow: auto;
		flex: 1;
	}
	footer {
		display: flex;
		gap: 0.75rem;
		justify-content: flex-end;
		flex-wrap: wrap;
		padding: 0.9rem clamp(1rem, 1.8vw, 2rem) clamp(1rem, 1.6vw, 1.6rem);
		border-top: 1px solid var(--line);
	}
	@keyframes fade {
		from {
			opacity: 0;
		}
	}
	@keyframes pop {
		from {
			opacity: 0;
			transform: scale(0.96) translateY(8px);
		}
	}
	@keyframes slide {
		from {
			transform: translateX(40px);
			opacity: 0;
		}
	}
</style>
