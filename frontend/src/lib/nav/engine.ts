/**
 * Spatial navigation engine for remote-control (10-foot) use.
 *
 * - Elements opt in with the `focusable` action (see ./actions.ts). They get real DOM focus.
 * - Arrow keys move to the geometrically nearest candidate in that direction, preferring the
 *   current `group` (row) for left/right and the remembered element of a row when entering it.
 * - Enter activates (`click()`), back keys pop the top scope (modal) or the route.
 * - Scopes (`navScope` action) trap focus inside modals/overlays and restore focus on close.
 * - Focus is remembered per route and per group and restored when coming back.
 */

export type Dir = 'up' | 'down' | 'left' | 'right';
export type NavKey = Dir | 'select' | 'back' | 'playpause' | 'next' | 'prev' | 'ff' | 'rw';

export interface FocusOpts {
	/** Row / section id. Left/right stays inside the group; entering a group restores its last focus. */
	group?: string;
	/** Stable id used to restore focus when returning to a route. */
	id?: string;
	/** Called on Enter / click. */
	onSelect?: (e?: Event) => void;
	/** Per-element key handler; return true to consume the key. */
	onKey?: (key: NavKey, e: KeyboardEvent) => boolean | void;
	onFocus?: () => void;
	onBlur?: () => void;
	/** Preferred initial focus of the view / scope. */
	autofocus?: boolean;
	disabled?: boolean;
	/** How to scroll into view on focus (default 'center'). */
	scroll?: 'center' | 'nearest' | 'none';
	/** Text inputs: open the on-screen keyboard on Enter (default true). */
	osk?: boolean;
	/** Label for the on-screen keyboard. */
	label?: string;
	/** Restore the row's last focus when entering the group vertically (default true). Off for grids. */
	memory?: boolean;
}

interface Entry {
	el: HTMLElement;
	opts: FocusOpts;
}

interface Scope {
	root: HTMLElement;
	onBack?: () => void;
	restore: HTMLElement | null;
}

type KeyHandler = (key: NavKey, e: KeyboardEvent) => boolean | void;

const KEYMAP: Record<string, NavKey> = {
	ArrowUp: 'up',
	ArrowDown: 'down',
	ArrowLeft: 'left',
	ArrowRight: 'right',
	Up: 'up',
	Down: 'down',
	Left: 'left',
	Right: 'right',
	Enter: 'select',
	NumpadEnter: 'select',
	Select: 'select',
	Accept: 'select',
	Backspace: 'back',
	Escape: 'back',
	BrowserBack: 'back',
	GoBack: 'back',
	' ': 'playpause',
	Spacebar: 'playpause',
	MediaPlayPause: 'playpause',
	MediaPlay: 'playpause',
	MediaPause: 'playpause',
	MediaTrackNext: 'next',
	MediaTrackPrevious: 'prev',
	MediaFastForward: 'ff',
	MediaRewind: 'rw'
};

function prefersReducedMotion() {
	return typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches;
}

export function isTextInput(el: Element | null): el is HTMLInputElement | HTMLTextAreaElement {
	if (!el) return false;
	if (el instanceof HTMLTextAreaElement) return true;
	if (el instanceof HTMLInputElement) {
		return ['text', 'search', 'password', 'url', 'email', 'number', 'tel', ''].includes(el.type);
	}
	return (el as HTMLElement).isContentEditable;
}

class NavEngine {
	private entries = new Map<HTMLElement, Entry>();
	private scopes: Scope[] = [];
	private handlers: KeyHandler[] = [];
	private rawListeners = new Set<(e: KeyboardEvent) => void>();
	private routeKey = '/';
	private routeMemory = new Map<string, string>();
	private groupMemory = new Map<string, string>();
	private pendingRestore: { id: string; until: number } | null = null;
	private userMoved = false;
	private routeAt = 0;
	private lastFocused: HTMLElement | null = null;
	/** Element the engine focused on its own (not the user); may be replaced when better content loads. */
	private autoFocused: HTMLElement | null = null;
	private ensureQueued = false;
	private ensureForce = false;
	/** Time of the last navigation key; after that, the engine never moves focus on its own. */
	private lastKeyAt = 0;
	/** Between beforeNavigate and afterNavigate: the router owns focus, don't fight it. */
	private navigating = false;
	/** Bumped on every navigation, so delayed focus fixes from the previous page are dropped. */
	private routeGen = 0;
	private physicallyTyped = new WeakSet<HTMLElement>();
	private lastMove = 0;
	private installed = false;

	/** Called when back is pressed and no scope handles it (router back). */
	backFallback: () => void = () => history.back();
	/** Opens the on-screen keyboard for a text input. */
	oskOpener: ((el: HTMLInputElement | HTMLTextAreaElement, label?: string) => void) | null = null;

	install() {
		if (this.installed || typeof window === 'undefined') return;
		this.installed = true;
		const root = document.documentElement;
		root.dataset.modality = matchMedia('(hover: none)').matches ? 'pointer' : 'keys';
		window.addEventListener('keydown', (e) => this.onKeyDown(e), true);
		window.addEventListener(
			'keyup',
			(e) => {
				if ((e.key === ' ' || e.key === 'Enter') && !isTextInput(document.activeElement)) e.preventDefault();
			},
			true
		);
		window.addEventListener('pointerdown', () => (root.dataset.modality = 'pointer'), true);
		window.addEventListener('wheel', () => (root.dataset.modality = 'pointer'), { capture: true, passive: true });
		document.addEventListener('focusin', (e) => {
			const el = e.target as HTMLElement;
			const entry = this.entries.get(el);
			if (!entry) {
				// Stray focus (browser focus fix-ups, non-navigable elements): keep the remote's cursor where it was.
				const last = this.lastFocused;
				if (this.modality === 'keys' && !isTextInput(el) && el !== document.body && last && last !== el && last.isConnected && this.inTopScope(last)) {
					queueMicrotask(() => last.focus({ preventScroll: true }));
				}
				return;
			}
			this.lastFocused = el;
			this.remember(el, entry);
			entry.opts.onFocus?.();
		});
		document.addEventListener('focusout', (e) => {
			const el = e.target as HTMLElement;
			const entry = this.entries.get(el);
			entry?.opts.onBlur?.();
			// Focus dropped to <body> (element hidden/inert, framework focus reset) while the window still has
			// focus: put the remote's cursor back instead of stranding it.
			if (entry && !e.relatedTarget && !this.navigating) {
				const gen = this.routeGen;
				setTimeout(() => {
					if (this.navigating || gen !== this.routeGen || this.modality !== 'keys' || !document.hasFocus()) return;
					const a = document.activeElement;
					if (a && a !== document.body) return;
					if (el.isConnected && this.usable(entry) && this.inTopScope(el)) el.focus({ preventScroll: true });
					else this.queueEnsure(true);
				}, 0);
			}
		});
	}

	get modality(): 'keys' | 'pointer' {
		return (document.documentElement.dataset.modality as 'keys' | 'pointer') ?? 'keys';
	}

	/** Current modal/overlay root; shell navigation must not intercept its keys. */
	get scopeRoot(): HTMLElement | null { return this.scopes.at(-1)?.root ?? null; }

	// ---------- registration

	register(el: HTMLElement, opts: FocusOpts) {
		this.entries.set(el, { el, opts });
		if (!el.hasAttribute('tabindex') && !(el instanceof HTMLButtonElement || el instanceof HTMLAnchorElement || el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement)) {
			el.tabIndex = 0;
		}
		el.classList.add('focusable');
		this.queueEnsure();
	}

	update(el: HTMLElement, opts: FocusOpts) {
		const e = this.entries.get(el);
		if (e) e.opts = opts;
	}

	unregister(el: HTMLElement) {
		const wasActive = document.activeElement === el;
		this.entries.delete(el);
		if (wasActive) this.queueEnsure();
	}

	pushScope(root: HTMLElement, onBack?: () => void): Scope {
		const active = document.activeElement as HTMLElement | null;
		const scope: Scope = { root, onBack, restore: active && active !== document.body ? active : null };
		this.scopes.push(scope);
		this.queueEnsure(true);
		return scope;
	}

	updateScope(scope: Scope, onBack?: () => void) {
		scope.onBack = onBack;
	}

	popScope(scope: Scope) {
		const i = this.scopes.indexOf(scope);
		if (i < 0) return;
		this.scopes.splice(i, 1);
		const active = document.activeElement;
		const focusWasInside = !active || active === document.body || scope.root.contains(active) || !active.isConnected;
		if (focusWasInside) {
			if (scope.restore && scope.restore.isConnected && this.inTopScope(scope.restore)) this.focus(scope.restore, { scroll: false });
			else this.queueEnsure(true);
		}
	}

	/** Global key handler (e.g. the player). Last registered runs first. */
	handleKeys(fn: KeyHandler): () => void {
		this.handlers.push(fn);
		return () => {
			this.handlers = this.handlers.filter((h) => h !== fn);
		};
	}

	onRawKey(fn: (e: KeyboardEvent) => void): () => void {
		this.rawListeners.add(fn);
		return () => this.rawListeners.delete(fn);
	}

	// ---------- route memory

	beginNavigation() {
		this.navigating = true;
		this.routeGen++;
	}

	/** Same page, different query (tabs, search): keep focus, just re-key the memory. */
	setRouteKey(key: string) {
		this.navigating = false;
		this.routeGen++;
		this.routeKey = key;
		this.pendingRestore = null;
		this.queueEnsure();
	}

	setRoute(key: string) {
		this.navigating = false;
		this.routeGen++;
		this.routeKey = key;
		const id = this.routeMemory.get(key);
		this.pendingRestore = id ? { id, until: Date.now() + 2500 } : null;
		this.userMoved = false;
		this.routeAt = Date.now();
		// Drop focus held by an element of the previous route.
		const a = document.activeElement as HTMLElement | null;
		if (a && a !== document.body && !a.isConnected) a.blur();
		this.queueEnsure(true);
	}

	private navId(el: HTMLElement, entry: Entry): string {
		if (entry.opts.id) return entry.opts.id;
		const group = entry.opts.group ?? '';
		const peers = this.list().filter((e) => (e.opts.group ?? '') === group);
		const idx = peers.findIndex((e) => e.el === el);
		return `${group}#${idx}`;
	}

	private remember(el: HTMLElement, entry: Entry) {
		if (this.scopeOf(el)) return; // don't remember focus inside modals
		const id = this.navId(el, entry);
		this.routeMemory.set(this.routeKey, id);
		if (entry.opts.group) this.groupMemory.set(`${this.routeKey}|${entry.opts.group}`, id);
	}

	private findById(id: string, list = this.candidates()): HTMLElement | null {
		for (const e of list) if (e.opts.id === id) return e.el;
		const m = /^(.*)#(\d+)$/.exec(id);
		if (!m) return null;
		const peers = list.filter((e) => (e.opts.group ?? '') === m[1] && !e.opts.id);
		return peers[Number(m[2])]?.el ?? null;
	}

	// ---------- candidates

	private list(): Entry[] {
		const arr = [...this.entries.values()].filter((e) => e.el.isConnected);
		arr.sort((a, b) => (a.el.compareDocumentPosition(b.el) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1));
		return arr;
	}

	private scopeOf(el: Element): Scope | undefined {
		for (let i = this.scopes.length - 1; i >= 0; i--) if (this.scopes[i].root.contains(el)) return this.scopes[i];
		return undefined;
	}

	private inTopScope(el: Element): boolean {
		const top = this.scopes[this.scopes.length - 1];
		if (top) return top.root.contains(el);
		return true;
	}

	private usable(e: Entry): boolean {
		const el = e.el;
		if (e.opts.disabled || (el as HTMLButtonElement).disabled || el.getAttribute('aria-disabled') === 'true') return false;
		if (el.closest('[inert],[hidden],[data-nav-off]')) return false;
		const r = el.getBoundingClientRect();
		if (r.width < 1 || r.height < 1) return false;
		const cv = (el as HTMLElement & { checkVisibility?: (o?: object) => boolean }).checkVisibility;
		if (cv && !cv.call(el, { visibilityProperty: true })) return false;
		return true;
	}

	private candidates(): Entry[] {
		return this.list().filter((e) => this.inTopScope(e.el) && this.usable(e));
	}

	activeEntry(): Entry | undefined {
		const a = document.activeElement as HTMLElement | null;
		if (!a) return undefined;
		const e = this.entries.get(a);
		return e && this.inTopScope(a) ? e : undefined;
	}

	// ---------- focusing

	focus(el: HTMLElement, o: { scroll?: boolean } = {}) {
		const entry = this.entries.get(el);
		el.focus({ preventScroll: true });
		if (o.scroll === false) return;
		// Rows and grids of cards scroll to the centre (10-foot browsing); everything else just into view.
		const mode = entry?.opts.scroll ?? (!this.scopeOf(el) && el.closest('[data-nav-row],[data-nav-center]') ? 'center' : 'nearest');
		if (mode === 'none' || el.closest('[data-nav-noscroll]')) return;
		el.scrollIntoView({ block: mode, inline: 'center', behavior: prefersReducedMotion() ? 'auto' : 'smooth' });
	}

	private queueEnsure(force = false) {
		this.ensureForce ||= force;
		if (this.ensureQueued) return;
		this.ensureQueued = true;
		requestAnimationFrame(() => {
			const f = this.ensureForce;
			this.ensureQueued = false;
			this.ensureForce = false;
			this.ensure(f);
		});
	}

	/** True once the user pressed a navigation key since the current route/scope appeared. */
	private get userActed() {
		return this.userMoved || this.lastKeyAt > this.routeAt;
	}

	/** Make sure something sensible has focus (restore memory, autofocus, or first). */
	private ensure(force = false) {
		if (typeof document === 'undefined') return;
		const cands = this.candidates();
		if (!cands.length) return;
		const current = this.activeEntry();
		// Late restore: data loaded after route change and the remembered element showed up.
		if (this.pendingRestore && this.userActed) this.pendingRestore = null;
		if (this.pendingRestore && this.scopes.length === 0) {
			if (Date.now() > this.pendingRestore.until) this.pendingRestore = null;
			else {
				const el = this.findById(this.pendingRestore.id, cands);
				if (el) {
					this.pendingRestore = null;
					if (current?.el !== el) this.focus(el);
					return;
				}
			}
		}
		const pointer = this.modality === 'pointer';
		const content = (e: Entry) => !e.el.closest('[data-nav-skip-initial]') && !(pointer && isTextInput(e.el));
		// The last autofocus wins: later/deeper content (a form) is more specific than navigation (a side menu).
		const preferred = cands.findLast((e) => e.opts.autofocus && !(pointer && isTextInput(e.el))) ?? cands.find(content);
		if (current && current.el.isConnected && this.usable(current)) {
			// Upgrade an automatic focus once the page's preferred element shows up (data arrived late),
			// but never after the user pressed a key.
			const better = preferred && preferred.el !== current.el && (preferred.opts.autofocus || current.el.closest('[data-nav-skip-initial]'));
			if (!this.userActed && current.el === this.autoFocused && better && !this.scopes.length) {
				this.autoFocused = preferred.el;
				this.focus(preferred.el);
			}
			return;
		}
		// Touch/mouse users get no automatic focus ring; the first arrow key press picks it up.
		if (pointer) return;
		let pick = preferred;
		if (!pick) {
			// Only chrome (top bar) so far: give the page a moment to load its content.
			if (Date.now() - this.routeAt < 1500) {
				setTimeout(() => this.queueEnsure(true), 300);
				return;
			}
			pick = cands[0];
		}
		if (pointer && isTextInput(pick.el)) return; // never pop a phone keyboard on its own
		this.autoFocused = pick.el;
		this.focus(pick.el, { scroll: !pick.el.closest('[data-nav-skip-initial]') });
	}

	/** Explicitly focus the view's preferred element. */
	focusInitial() {
		this.queueEnsure(true);
	}

	// ---------- movement

	move(dir: Dir): boolean {
		const cur = this.activeEntry();
		const cands = this.candidates();
		if (!cur) {
			const first = cands.findLast((e) => e.opts.autofocus) ?? cands.find((e) => !e.el.closest('[data-nav-skip-initial]')) ?? cands[0];
			this.userMoved = true;
			this.pendingRestore = null;
			if (first) this.focus(first.el);
			return !!first;
		}
		const r = cur.el.getBoundingClientRect();
		const horizontal = dir === 'left' || dir === 'right';
		const inDir = (c: DOMRect) => {
			const tolX = Math.min(r.width, c.width) * 0.3;
			const tolY = Math.min(r.height, c.height) * 0.3;
			switch (dir) {
				case 'right':
					return c.left >= r.right - tolX && c.left + c.width / 2 > r.left + r.width / 2;
				case 'left':
					return c.right <= r.left + tolX && c.left + c.width / 2 < r.left + r.width / 2;
				case 'down':
					return c.top >= r.bottom - tolY && c.top + c.height / 2 > r.top + r.height / 2;
				case 'up':
					return c.bottom <= r.top + tolY && c.top + c.height / 2 < r.top + r.height / 2;
			}
		};
		let pool = cands.filter((e) => e !== cur).map((e) => ({ e, rect: e.el.getBoundingClientRect() })).filter((x) => inDir(x.rect));
		const group = cur.opts.group;
		if (horizontal) {
			const same = group ? pool.filter((x) => x.e.opts.group === group) : [];
			if (same.length) pool = same;
			else {
				// Prefer the same visual line (vertical overlap). Carousel rows never jump to another line;
				// elsewhere (side menus → content) fall back to the nearest thing in that direction.
				let line = pool.filter((x) => x.rect.bottom > r.top + 4 && x.rect.top < r.bottom - 4);
				if (group) line = line.filter((x) => x.e.opts.group !== group);
				if (!line.length && !cur.el.closest('[data-nav-row]')) {
					const inBar = !!cur.el.closest('[data-nav-skip-initial]');
					line = pool.filter((x) => (!group || x.e.opts.group !== group) && !!x.e.el.closest('[data-nav-skip-initial]') === inBar);
				}
				pool = line;
			}
		}
		if (!horizontal) {
			// Vertical moves stay inside a zone (e.g. a side menu vs. its content) but may leave to zone-less
			// chrome like the top bar.
			// A side column (the desktop sidebar) is entered only horizontally.
			const zone = cur.el.closest('[data-nav-zone]');
			pool = pool.filter((x) => {
				const z = x.e.el.closest('[data-nav-zone]');
				if (zone) return !z || z === zone;
				return !z || !z.hasAttribute('data-nav-side');
			});
		}
		if (!pool.length) return false;
		const cx = r.left + r.width / 2;
		const cy = r.top + r.height / 2;
		let best = pool[0];
		let bestScore = Infinity;
		for (const x of pool) {
			const c = x.rect;
			let primary: number;
			let orth: number;
			let overlap: boolean;
			if (horizontal) {
				primary = dir === 'right' ? Math.max(0, c.left - r.right) : Math.max(0, r.left - c.right);
				orth = Math.abs(c.top + c.height / 2 - cy);
				overlap = c.bottom > r.top && c.top < r.bottom;
			} else {
				primary = dir === 'down' ? Math.max(0, c.top - r.bottom) : Math.max(0, r.top - c.bottom);
				// Distance to nearest point horizontally (so wide elements are reachable from anywhere above).
				orth = c.left > cx ? c.left - cx : c.right < cx ? cx - c.right : 0;
				overlap = c.right > r.left && c.left < r.right;
			}
			const score = primary + orth * (horizontal ? 2.5 : overlap ? 0.6 : 1.4);
			if (score < bestScore) {
				bestScore = score;
				best = x;
			}
		}
		let target = best.e.el;
		// Entering another row vertically: restore that row's last focused element.
		const tg = best.e.opts.group;
		if (!horizontal && tg && tg !== group && best.e.opts.memory !== false) {
			const memId = this.groupMemory.get(`${this.routeKey}|${tg}`);
			const mem = memId ? this.findById(memId, cands) : null;
			if (mem) target = mem;
		}
		this.userMoved = true;
		this.pendingRestore = null;
		this.focus(target);
		return true;
	}

	private inBack = false;

	back() {
		// Re-entrant call (an onBack handler that calls nav.back()): go to the router instead of looping.
		if (this.inBack) return this.backFallback();
		this.inBack = true;
		try {
			const top = this.scopes[this.scopes.length - 1];
			if (top) top.onBack?.();
			else this.backFallback();
		} finally {
			this.inBack = false;
		}
	}

	activate(el: HTMLElement) {
		const entry = this.entries.get(el);
		if (isTextInput(el) && entry?.opts.osk !== false && !this.physicallyTyped.has(el) && this.oskOpener) {
			this.oskOpener(el as HTMLInputElement, entry?.opts.label);
			return;
		}
		if (isTextInput(el)) {
			if (entry?.opts.onSelect) entry.opts.onSelect();
			else if (el instanceof HTMLInputElement) el.form?.requestSubmit();
			return;
		}
		el.click();
	}

	// ---------- keyboard

	private onKeyDown(e: KeyboardEvent) {
		for (const l of this.rawListeners) l(e);
		if (e.altKey || e.ctrlKey || e.metaKey) return;
		const key = KEYMAP[e.key] ?? (e.code === 'Space' ? 'playpause' : undefined);
		const active = document.activeElement as HTMLElement | null;
		const typing = isTextInput(active);
		if (typing && active && e.key.length === 1 && e.key !== ' ') this.physicallyTyped.add(active);
		if (!key) return;
		document.documentElement.dataset.modality = 'keys';
		this.lastKeyAt = Date.now();

		if (typing && active) {
			if (!this.textInputKey(key, e, active as HTMLInputElement | HTMLTextAreaElement)) return;
		}

		const entry = this.activeEntry();
		const consume = () => {
			e.preventDefault();
			e.stopPropagation();
		};
		if (entry?.opts.onKey?.(key, e)) return consume();
		for (let i = this.handlers.length - 1; i >= 0; i--) {
			if (this.handlers[i](key, e)) return consume();
		}
		switch (key) {
			case 'up':
			case 'down':
			case 'left':
			case 'right': {
				consume();
				const t = performance.now();
				if (e.repeat && t - this.lastMove < 70) return;
				this.lastMove = t;
				this.move(key);
				return;
			}
			case 'select':
			case 'playpause':
				if (entry) {
					consume();
					this.activate(entry.el);
				} else if (key === 'select') {
					consume();
					this.move('down');
				}
				return;
			case 'back':
				consume();
				this.back();
				return;
		}
	}

	/** Returns true if the engine should handle the key (instead of the native text input). */
	private textInputKey(key: NavKey, e: KeyboardEvent, el: HTMLInputElement | HTMLTextAreaElement): boolean {
		const value = el.value ?? '';
		const start = el.selectionStart;
		const end = el.selectionEnd;
		const noCaret = start === null || start === undefined;
		switch (key) {
			case 'playpause':
				return false; // type a space
			case 'back':
				if (e.key === 'Backspace') return value.length === 0;
				return true;
			case 'left':
				return noCaret || (start === 0 && end === 0);
			case 'right':
				return noCaret || (start === value.length && end === value.length);
			case 'up':
				if (el instanceof HTMLTextAreaElement && !noCaret) return !value.slice(0, start!).includes('\n');
				return true;
			case 'down':
				if (el instanceof HTMLTextAreaElement && !noCaret) return !value.slice(end!).includes('\n');
				return true;
			case 'select':
				if (el instanceof HTMLTextAreaElement && this.physicallyTyped.has(el)) return false; // newline
				return true;
			default:
				return true;
		}
	}
}

export const nav = new NavEngine();
