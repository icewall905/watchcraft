// App-wide session: auth, profiles, active profile, cached categories + settings.
import { api, profileKey, currentAccountId, setCurrentAccountId, setProfileCookie, onUnauthorized, onProfileLocked } from '../api';
import { thumbnailPreference } from './thumbnailPreference.svelte';
import type { Account, AuthStatus, Category, Profile, Settings } from '../types';
import { featureFor, isHubPath } from '../navigation';

/** Hub areas a child profile cannot open. */
const KID_LOCKED_PATHS = ['/music', '/sources'];

class Session {
	ready = $state(false);
	authRequired = $state(false);
	authenticated = $state(true);
	/** 'open' = legacy household mode (no sign-in); 'accounts' = real accounts. */
	mode = $state<'open' | 'accounts'>('open');
	/** Accounts signed in on this client. */
	accounts = $state<AuthStatus['accounts']>([]);
	/** Active account of this tab (accounts mode). */
	account = $state<Account | null>(null);
	accountId = $state<string | null>(null);
	/** Fresh install: no accounts yet, show "Create the first admin". */
	bootstrap = $state(false);
	/** "Add account": show the sign-in screen while the other accounts stay signed in. */
	adding = $state(false);
	profiles = $state<Profile[]>([]);
	/** Profiles last seen per account (picker lists other signed-in accounts from this). */
	profileCache = $state<Record<string, Profile[]>>({});
	profileId = $state<number | null>(null);
	/** Show the full-screen "Who's watching?" picker. */
	choosing = $state(false);
	unlocking = $state<Profile | null>(null);
	private pinAnswer: ((ok: boolean) => void) | null = null;
	categories = $state<Category[]>([]);
	settings = $state<Settings | null>(null);
	/** Bumped whenever the active profile changes: pages re-fetch on change. */
	version = $state(0);

	profile = $derived(this.profiles.find((p) => p.id === this.profileId) ?? null);
	/** Player settings for the active profile: profile prefs override the global defaults. */
	playerPrefs = $derived({
		...(this.settings?.player ?? {}),
		...((this.profile?.prefs?.player ?? {}) as Partial<Settings['player']>)
	});
	isAdmin = $derived(
		this.mode === 'accounts'
			? this.account?.role === 'admin' && !!this.profile && this.profile.role !== 'child' && !this.profile.pin_locked
			: this.profile?.role === 'admin' && !this.profile?.pin_locked
	);
	/** Full Hub: grown-up profiles. Children use the Hub under the server's kid-safe lock. */
	grownUp = $derived(!!this.profile && this.profile.role !== 'child' && !this.profile.pin_locked);
	isChild = $derived(this.profile?.role === 'child');
	/** Hub pages: grown-ups, plus child profiles (kid-safe content only, adult-only tools hidden). */
	hubAllowed = $derived(!!this.profile && !this.profile.pin_locked);
	/** Mood Boards: grown-ups, plus child profiles (whose boards are always child-friendly). */
	boardsAllowed = $derived(this.hubAllowed);
	/** Whether this profile may open a Hub page. Music and source management stay grown-up only. */
	canOpen(path: string): boolean {
		const p = path.split(/[?#]/)[0];
		if (this.grownUp) return true;
		if (this.isChild) {
			const key = featureFor(p);
			if (key && !this.allows(key)) return false;
		}
		if (!isHubPath(p)) return true;
		if (!this.hubAllowed) return false;
		return !KID_LOCKED_PATHS.some((x) => p === x || p.startsWith(x + '/'));
	}
	/** A parental-control switch for the current profile (grown-ups: always on). */
	allows(key: string): boolean {
		if (!this.isChild) return true;
		return this.profile?.controls?.[key] ?? key === 'screen';
	}
	needsLogin = $derived(this.mode === 'accounts' ? !this.account || this.adding : this.authRequired && !this.authenticated);

	constructor() {
		onProfileLocked(() => { void this.loadProfiles().then(() => {
			if (this.profile?.pin_locked) { this.categories = []; this.choosing = true; }
		}); });
		onUnauthorized(() => {
			if (this.mode === 'accounts') { void this.refreshStatus(); return; }
			this.authRequired = true;
			this.authenticated = false;
		});
	}

	async init() {
		try {
			const st = await api.authStatus();
			this.applyStatus(st);
		} catch {
			/* server unreachable: keep going, pages will show errors */
		}
		if (!this.needsLogin) await this.loadAll(true);
		this.ready = true;
	}

	/** Take the server's view of signed-in accounts; keep this tab's own active account when still valid. */
	private applyStatus(st: AuthStatus) {
		this.mode = st.mode === 'accounts' ? 'accounts' : 'open';
		if (this.mode === 'open') {
			const legacy = st as unknown as { required?: boolean; authenticated?: boolean };
			this.authRequired = !!legacy.required;
			this.authenticated = legacy.authenticated ?? true;
			return;
		}
		this.bootstrap = !!st.bootstrap;
		this.accounts = st.accounts ?? [];
		const mine = currentAccountId();
		const id = this.accounts.some((a) => a.public_id === mine) ? mine : (st.active ?? this.accounts[0]?.public_id ?? null);
		this.accountId = id;
		setCurrentAccountId(id);
		const a = this.accounts.find((x) => x.public_id === id);
		const full = st.account && st.account.username === a?.username ? st.account : null;
		this.account = a ? { ...(full ?? {}), public_id: a.public_id, username: a.username, role: a.role } : null;
		this.authenticated = !!this.account;
		this.authRequired = true;
	}

	/** Re-read sign-in state (after a 401). If the active account is gone, fall back to another or the login screen. */
	async refreshStatus() {
		const before = this.accountId;
		try { this.applyStatus(await api.authStatus()); } catch { return; }
		if (this.accountId && this.accountId !== before) {
			try { await api.switchAccount(this.accountId); } catch { /* ignore */ }
			await this.loadAll(true);
			this.version++;
		}
	}

	async loadAll(firstLaunch = false) {
		await this.loadProfiles();
		let stored: number | null = null;
		try {
			const raw = localStorage.getItem(profileKey());
			stored = raw ? Number(raw) : null;
		} catch {
			/* ignore */
		}
		const valid = this.profiles.find((p) => p.id === stored);
		if (valid) this.profileId = valid.id;
		else {
			this.profileId = this.profiles[0]?.id ?? null;
			this.persist();
			if (firstLaunch && this.profiles.length > 1) this.choosing = true;
		}
		if (this.profile?.pin_locked) { this.categories = []; this.choosing = true; }
		await Promise.all([this.profile?.pin_locked ? Promise.resolve() : this.loadCategories(), this.loadSettings()]);
	}

	async loadProfiles() {
		try {
			this.profiles = await api.profiles();
			if (this.accountId) this.profileCache[this.accountId] = this.profiles;
		} catch {
			this.profiles = [];
		}
	}

	async loadCategories() {
		try {
			this.categories = await api.categories();
		} catch {
			this.categories = [];
		}
	}

	async loadSettings() {
		try {
			this.settings = await api.settings();
			thumbnailPreference.dearrow = !!this.settings.extras.dearrow_thumbnails;
		} catch {
			/* keep previous */
		}
	}

	private persist() {
		try {
			if (this.profileId != null) localStorage.setItem(profileKey(), String(this.profileId));
		} catch {
			/* ignore */
		}
		if (this.profileId != null) setProfileCookie(this.accountId, this.profileId);
	}

	async switchProfile(id: number) {
		if (!(await this.ensureUnlocked(id))) return false;
		if (id !== this.profileId && this.profileId != null) await api.lockProfile(this.profileId);
		this.profileId = id;
		this.persist();
		await Promise.all([this.loadProfiles(), this.loadCategories()]);
		this.choosing = false;
		this.version++;
		return true;
	}

	async ensureUnlocked(id: number): Promise<boolean> {
		await this.loadProfiles();
		const profile = this.profiles.find(p => p.id === id);
		if (!profile) return false;
		if (!profile.pin_locked) return true;
		if (this.unlocking) return false;
		this.unlocking = profile;
		return new Promise(resolve => { this.pinAnswer = resolve; });
	}

	answerPin(ok: boolean) {
		const answer = this.pinAnswer;
		this.pinAnswer = null;
		this.unlocking = null;
		answer?.(ok);
	}

	async lockProfile() {
		if (this.profileId == null) return;
		await api.lockProfile(this.profileId);
		await this.loadProfiles();
		this.categories = [];
		this.choosing = true;
	}

	/** After any successful sign-in (password, invite, device code). `publicId` = the account just signed in. */
	async afterLogin(publicId?: string) {
		if (this.mode === 'accounts') {
			if (publicId) setCurrentAccountId(publicId);
			this.profiles = [];
			this.profileId = null;
			this.categories = [];
			this.adding = false;
			await this.refreshStatus();
			if (!this.account) return;
		}
		this.authenticated = true;
		await this.loadAll(true);
		if (this.mode === 'accounts' && this.profiles.length > 1) this.choosing = true;
		this.version++;
	}

	/** Make another signed-in account active in this tab. */
	async switchAccount(publicId: string) {
		if (publicId === this.accountId) return true;
		try { await api.switchAccount(publicId); } catch { return false; }
		setCurrentAccountId(publicId);
		this.profiles = [];
		this.profileId = null;
		this.categories = [];
		this.settings = null;
		this.applyStatus({ ...this.statusSnapshot(), active: publicId });
		this.adding = false;
		await this.loadAll(false);
		if (this.profiles.length > 1 || this.profile?.pin_locked) this.choosing = true;
		this.version++;
		return true;
	}

	private statusSnapshot(): AuthStatus {
		return { mode: this.mode, bootstrap: this.bootstrap, active: this.accountId, account: this.account, accounts: this.accounts };
	}

	/** Show the sign-in screen to add another account. */
	addAccount() {
		this.adding = true;
		this.choosing = false;
	}

	async signOut(publicId?: string, all = false) {
		const target = publicId ?? this.accountId ?? undefined;
		let st: AuthStatus;
		try { st = await api.logout(all ? { all: true } : { public_id: target }); } catch { return; }
		const wasActive = all || target === this.accountId;
		const rest = (st.accounts ?? []).filter((a) => a.public_id !== target);
		this.accounts = all ? [] : rest;
		if (!wasActive) return;
		setCurrentAccountId(null);
		this.profiles = [];
		this.profileId = null;
		this.categories = [];
		this.settings = null;
		this.choosing = false;
		this.account = null;
		this.accountId = null;
		const next = this.accounts.find((a) => a.active) ?? this.accounts[0];
		if (next) await this.switchAccount(next.public_id);
		else this.authenticated = false;
	}

	categoryById(id: number | null | undefined): Category | undefined {
		return id == null ? undefined : this.categories.find((c) => c.id === id);
	}
}

export const session = new Session();
