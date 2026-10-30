/** The first palette is the original WatchCraft appearance. The other themes
 * only override visual tokens: playback, settings and profile colours are unchanged. */
export interface Theme {
	id: string; name: string; description: string;
	bg: string; bg1: string; bg2: string; surface: string; accent: string; accentInk: string;
	ai: string; text: string; muted: string; faint: string;
}

export const THEMES: Theme[] = [
	{ id: 'projector', name: 'Projector', description: 'The original violet room and warm amber.', bg: '#0a0817', bg1: '#110e26', bg2: '#1a1638', surface: '#1b1740', accent: '#ffb547', accentInk: '#1c1205', ai: '#9d86ff', text: '#f0edff', muted: '#aaa4d2', faint: '#716b9c' },
	{ id: 'ocean', name: 'Studio', description: 'Near-black with a calm indigo accent.', bg: '#08090a', bg1: '#0f1011', bg2: '#161719', surface: '#1c1d20', accent: '#5e6ad2', accentInk: '#ffffff', ai: '#8fa6ff', text: '#f7f8f8', muted: '#8a8f98', faint: '#6b7079' },
	{ id: 'forest', name: 'Reel', description: 'Cinema slate with green and sky blue.', bg: '#0e1114', bg1: '#14181c', bg2: '#1b2127', surface: '#202830', accent: '#00e054', accentInk: '#002610', ai: '#40bcf4', text: '#e6eef6', muted: '#99aabb', faint: '#6a7a8b' },
	{ id: 'ember', name: 'Clay', description: 'Warm black with soft terracotta.', bg: '#0d0c0b', bg1: '#141311', bg2: '#1c1a17', surface: '#24211e', accent: '#e07a5f', accentInk: '#2a0f06', ai: '#9cc3b4', text: '#f2eee9', muted: '#aba49c', faint: '#78716a' },
	{ id: 'midnight', name: 'Twilight', description: 'Dark grey with electric purple.', bg: '#0b0b0d', bg1: '#0e0e10', bg2: '#18181b', surface: '#1f1f23', accent: '#9147ff', accentInk: '#ffffff', ai: '#5cdcff', text: '#efeff1', muted: '#adadb8', faint: '#6e6e7a' },
	{ id: 'slate', name: 'Graphite', description: 'Quiet monochrome with a crisp white edge.', bg: '#0a0a0b', bg1: '#111113', bg2: '#18181b', surface: '#202024', accent: '#ededef', accentInk: '#111113', ai: '#8b9bb4', text: '#ededef', muted: '#a1a1aa', faint: '#6e6e78' },
	{ id: 'rose', name: 'Blossom', description: 'Soft black with bright pink.', bg: '#0d0b0c', bg1: '#141113', bg2: '#1b171a', surface: '#231e21', accent: '#f472b6', accentInk: '#2d0a1c', ai: '#a5b4fc', text: '#f4eef1', muted: '#ada3a8', faint: '#776d73' },
	{ id: 'aurora', name: 'Cobalt', description: 'Blue-black with clear blue and violet.', bg: '#010409', bg1: '#0d1117', bg2: '#151b23', surface: '#1b222c', accent: '#4493f8', accentInk: '#04111f', ai: '#a371f7', text: '#e6edf3', muted: '#9198a1', faint: '#6e7681' },
	{ id: 'copper', name: 'Lime', description: 'Pure black with acid lime.', bg: '#090909', bg1: '#0f0f0f', bg2: '#161616', surface: '#1d1d1d', accent: '#c6f432', accentInk: '#1a2200', ai: '#a78bfa', text: '#f0f0f0', muted: '#a1a1a1', faint: '#6e6e6e' },
	{ id: 'noir', name: 'Noir', description: 'True black, silver type and a single red.', bg: '#050505', bg1: '#0b0b0c', bg2: '#121214', surface: '#1a1a1d', accent: '#ff4d5e', accentInk: '#2a0006', ai: '#9aa0b5', text: '#f0f0f0', muted: '#a3a3a8', faint: '#6c6c72' },
	{ id: 'lagoon', name: 'Tide', description: 'Cool charcoal with cyan.', bg: '#0a0d10', bg1: '#0f1317', bg2: '#151a20', surface: '#1b2128', accent: '#22d3ee', accentInk: '#032229', ai: '#c4a5ff', text: '#e8eef2', muted: '#97a3ad', faint: '#66727c' }
];

const TOKENS = ['--bg-0', '--bg-1', '--bg-2', '--surface', '--surface-solid', '--surface-2', '--surface-hi', '--line', '--line-strong', '--text', '--muted', '--faint', '--accent', '--accent-2', '--accent-ink', '--accent-glow', '--ai', '--ambient-1', '--ambient-2', '--ambient-3', '--modal-top', '--modal-bottom', '--overlay-surface', '--overlay-surface-deep', '--keyboard-surface', '--toast-surface', '--status-pop-surface', '--savebar-surface'];

export function applyTheme(id: string | null | undefined) {
	const theme = THEMES.find((t) => t.id === id) ?? THEMES[0];
	const root = document.documentElement;
	root.dataset.theme = theme.id;
	for (const token of TOKENS) root.style.removeProperty(token);
	if (theme.id !== 'projector') {
		const mix = (color: string, amount: number, into = 'transparent') => `color-mix(in srgb, ${color} ${amount}%, ${into})`;
		const tokens: Record<string, string> = {
			'--bg-0': theme.bg, '--bg-1': theme.bg1, '--bg-2': theme.bg2,
			'--surface': mix(theme.surface, 70), '--surface-solid': theme.surface,
			'--surface-2': mix(theme.surface, 85, theme.ai), '--surface-hi': mix(theme.surface, 65, theme.ai),
			'--line': mix(theme.text, 11), '--line-strong': mix(theme.text, 24),
			'--text': theme.text, '--muted': theme.muted, '--faint': theme.faint,
			'--accent': theme.accent, '--accent-2': mix(theme.accent, 75, 'white'), '--accent-ink': theme.accentInk,
			'--accent-glow': mix(theme.accent, 48), '--ai': theme.ai,
			'--ambient-1': mix(theme.ai, 14), '--ambient-2': mix(theme.accent, 6), '--ambient-3': mix(theme.ai, 10),
			'--modal-top': mix(theme.surface, 95), '--modal-bottom': mix(theme.bg1, 97),
			'--overlay-surface': mix(theme.surface, 96), '--overlay-surface-deep': mix(theme.bg1, 97),
			'--keyboard-surface': mix(theme.surface, 96), '--toast-surface': mix(theme.surface, 96),
			'--status-pop-surface': mix(theme.surface, 97), '--savebar-surface': mix(theme.bg1, 96)
		};
		for (const [key, value] of Object.entries(tokens)) root.style.setProperty(key, value);
	}
	document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme.bg);
}
