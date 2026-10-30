<script lang="ts">
	let { name, size = '1.25em' }: { name: string; size?: string } = $props();
	const P: Record<string, string> = {
		home: 'M3 11.5 12 4l9 7.5V20a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z',
		mic: 'M9 5a3 3 0 0 1 6 0v7a3 3 0 0 1-6 0zM6 10v2a6 6 0 0 0 12 0v-2M12 18v4M8 22h8',
		search: 'M10.5 4a6.5 6.5 0 1 1 0 13 6.5 6.5 0 0 1 0-13zM15.5 15.5 20 20',
		subs: 'M4 6h16v11H4zM8 21h8M10 9.5v4l3.5-2z',
		team: 'M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM2.5 20c0-3.6 2.9-6 6.5-6s6.5 2.4 6.5 6M16 4.3a3.5 3.5 0 0 1 0 6.4M18 14.4c2.1.7 3.5 2.8 3.5 5.6',
		agent: 'M12 3l1.9 5.6L19.5 10l-5.6 1.9L12 17.5l-1.9-5.6L4.5 10l5.6-1.4zM18.5 16l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z',
		settings: 'M12 8.5a3.5 3.5 0 1 1 0 7 3.5 3.5 0 0 1 0-7zM19.4 13.5l1.6 1.2-2 3.4-1.9-.7a7 7 0 0 1-1.8 1l-.3 2h-4l-.3-2a7 7 0 0 1-1.8-1l-1.9.7-2-3.4 1.6-1.2a7 7 0 0 1 0-3L3 9.3l2-3.4 1.9.7a7 7 0 0 1 1.8-1l.3-2h4l.3 2a7 7 0 0 1 1.8 1l1.9-.7 2 3.4-1.6 1.2a7 7 0 0 1 0 3z',
		pause: 'M7 5h3.5v14H7zM13.5 5H17v14h-3.5z',
		play: 'M7 4.5v15l12.5-7.5z',
		back: 'M15 5l-7 7 7 7',
		next: 'M5 5l9 7-9 7zM16 5h3v14h-3z',
		prev: 'M19 5l-9 7 9 7zM5 5h3v14H5z',
		cc: 'M3 6h18v12H3zM10 10.5a2 2 0 1 0 0 3M17 10.5a2 2 0 1 0 0 3',
		speed: 'M12 20a8 8 0 1 1 8-8M12 12l4-4',
		quality: 'M4 7h10M18 7h2M4 17h2M10 17h10M14 5v4M6 15v4',
		info: 'M12 10.5V17M12 7v.01M12 3a9 9 0 1 1 0 18 9 9 0 0 1 0-18z',
		plus: 'M12 5v14M5 12h14',
		refresh: 'M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7',
		check: 'M5 12.5l4.5 4.5L19 7.5',
		x: 'M6 6l12 12M18 6 6 18',
		edit: 'M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4',
		trash: 'M5 7h14M10 7V4h4v3M7 7l1 13h8l1-13',
		up: 'M6 15l6-6 6 6',
		down: 'M6 9l6 6 6-6',
		bell: 'M6 16V11a6 6 0 1 1 12 0v5l1.5 2h-15zM10 20.5h4',
		block: 'M12 3a9 9 0 1 1 0 18 9 9 0 0 1 0-18zM5.6 5.6l12.8 12.8',
		rewind: 'M11 6 4 12l7 6zM20 6l-7 6 7 6z',
		forward: 'M13 6l7 6-7 6zM4 6l7 6-7 6z',
		chart: 'M3 20h18M6.5 20v-6M11.5 20V8M16.5 20v-10M21 20V4',
		tv: 'M3 7h18v11H3zM8 3l4 4 4-4M8 21h8',
		compass: 'M12 3a9 9 0 1 1 0 18 9 9 0 0 1 0-18zM15.5 8.5l-2 5-5 2 2-5z',
		news: 'M5 4h12v16H7a2 2 0 0 1-2-2zM17 8h3v10a2 2 0 0 1-2 2M8 8h6M8 12h6M8 16h4',
		book: 'M5 4h13v16H7a2 2 0 0 1-2-2zM5 18a2 2 0 0 1 2-2h11M9 8h6',
		library: 'M6 4h12v17l-6-4-6 4z',
		briefing: 'M6 3h9l4 4v14H6zM14 3v5h5M9 12h7M9 16h7',
		alert: 'M7 20h10M8 20v-7a4 4 0 0 1 8 0v7M12 4V2M4.5 7 6 8.5M19.5 7 18 8.5',
		research: 'M10 4a6 6 0 1 1 0 12 6 6 0 0 1 0-12zM14.5 14.5 20 20M10 7.5a1.6 1.6 0 1 1 0 3.2 1.6 1.6 0 0 1 0-3.2zM7.5 13.5c.6-1.6 4.4-1.6 5 0',
		curator: 'M5 9h14v10H5zM12 9V5M12 3.5v.01M9 13.5v.01M15 13.5v.01M9.5 16.5h5',
		source: 'M12 12v9M8 21h8M12 12a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM8.5 5.5a5 5 0 0 0 0 7M15.5 5.5a5 5 0 0 1 0 7',
		more: 'M5 12v.01M12 12v.01M19 12v.01',
		pin: 'M9 4h6l-1 6 3 3H7l3-3zM12 13v8',
		collection: 'M3 6a1 1 0 0 1 1-1h5l2 2h9a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z',
		external: 'M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5',
		grid: 'M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z',
		list: 'M8 6h12M8 12h12M8 18h12M4 6v.01M4 12v.01M4 18v.01',
		menu: 'M4 6h16M4 12h16M4 18h16',
		sidebar: 'M4 5h16v14H4zM9 5v14'
	};
	const filled = new Set(['play', 'pause', 'next', 'prev', 'agent', 'rewind', 'forward']);
</script>

<svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" class="icon">
	<path
		d={P[name] ?? ''}
		fill={filled.has(name) ? 'currentColor' : 'none'}
		stroke={filled.has(name) ? 'none' : 'currentColor'}
		stroke-width="2"
		stroke-linecap="round"
		stroke-linejoin="round"
	/>
</svg>

<style>
	.icon {
		flex: none;
	}
</style>
