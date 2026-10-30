// App-wide session: auth, profiles, active profile, cached categories + settings.
import { api, PROFILE_KEY, onUnauthorized, onProfileLocked } from '../api';
import { thumbnailPreference } from './thumbnailPreference.svelte';
import type { Category, Profile, Settings } from '../types';

class Session {
	ready = $state(false);
	authRequired = $state(false);
	authenticated = $state(true);
	profiles = $state<Profile[]>([]);
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
	isAdmin = $derived(this.profile?.role === 'admin' && !this.profile?.pin_locked);
	hubAllowed = $derived(!!this.profile && this.profile.role !== 'child' && !this.profile.pin_locked);
	needsLogin = $derived(this.authRequired && !this.authenticated);

	constructor() {
		onProfileLocked(() => { void this.loadProfiles().then(() => {
			if (this.profile?.pin_locked) { this.categories = []; this.choosing = true; }
		}); });
		onUnauthorized(() => {
			this.authRequired = true;
			this.authenticated = false;
		});
	}

	async init() {
		try {
			const st = await api.authStatus();
			this.authRequired = st.required;
			this.authenticated = st.authenticated;
		} catch {
			/* server unreachable: keep going, pages will show errors */
		}
		if (!this.needsLogin) await this.loadAll(true);
		this.ready = true;
	}

	async loadAll(firstLaunch = false) {
		await this.loadProfiles();
		let stored: number | null = null;
		try {
			const raw = localStorage.getItem(PROFILE_KEY);
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
			if (this.profileId != null) localStorage.setItem(PROFILE_KEY, String(this.profileId));
		} catch {
			/* ignore */
		}
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

	async afterLogin() {
		this.authenticated = true;
		await this.loadAll(true);
		this.version++;
	}

	categoryById(id: number | null | undefined): Category | undefined {
		return id == null ? undefined : this.categories.find((c) => c.id === id);
	}
}

export const session = new Session();
