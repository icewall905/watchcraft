<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import { session } from '$lib/stores/session.svelte';
	import { toast } from '$lib/stores/toast.svelte';
	import { focusable } from '$lib/nav';
	import type { AdminAccount, Profile } from '$lib/types';
	import Section from './Section.svelte';
	import Avatar from '$lib/components/Avatar.svelte';
	import TextField from '$lib/components/form/TextField.svelte';
	import Select from '$lib/components/form/Select.svelte';
	import ParentalControls from '$lib/components/ParentalControls.svelte';

	let accounts = $state<AdminAccount[]>([]);
	let unowned = $state<Profile[]>([]);
	let loaded = $state(false);
	let newName = $state('');
	let newRole = $state<'admin' | 'user'>('user');
	let link = $state<{ who: string; url: string } | null>(null);
	let confirmDelete = $state<number | null>(null);
	let assignTo = $state<Record<number, number>>({});
	let parentalFor = $state<{ id: number; name: string; controls?: Record<string, boolean>; safety_rails?: string } | null>(null);

	async function load() {
		try {
			[accounts, unowned] = await Promise.all([api.adminAccounts(), api.unownedProfiles()]);
		} finally {
			loaded = true;
		}
	}
	onMount(load);

	const full = (path: string) => location.origin + path;
	const roleOptions = [
		{ value: 'user' as const, label: 'User', hint: 'Own profiles and personal settings.' },
		{ value: 'admin' as const, label: 'Administrator', hint: 'Server settings and accounts.' }
	];

	async function copy(text: string) {
		try {
			await navigator.clipboard.writeText(text);
			toast.success('Link copied');
		} catch {
			toast.warn('Copy it from the field.');
		}
	}

	async function invite() {
		try {
			const r = await api.createAccount({ ...(newName.trim() ? { username: newName.trim() } : {}), role: newRole });
			link = { who: r.account.username || 'new account', url: full(r.link) };
			newName = '';
			await load();
		} catch { /* toast shown */ }
	}
	async function reset(a: AdminAccount) {
		try {
			const r = await api.resetAccount(a.id);
			link = { who: a.username ?? 'invite', url: full(r.link) };
			await load();
		} catch { /* toast shown */ }
	}
	async function run(fn: () => Promise<unknown>, msg?: string) {
		try {
			await fn();
			if (msg) toast.success(msg);
			await load();
		} catch { /* toast shown */ }
	}
	async function remove(a: AdminAccount) {
		if (confirmDelete !== a.id) return (confirmDelete = a.id);
		confirmDelete = null;
		await run(() => api.deleteAccount(a.id), `Deleted ${a.username ?? 'invite'}`);
	}
	async function assign(p: Profile) {
		const to = assignTo[p.id];
		if (!to) return toast.warn('Pick an account first.');
		await run(async () => { await api.assignProfile(p.id, to); await session.loadProfiles(); }, `Moved ${p.name}`);
	}
	const isMe = (a: AdminAccount) => a.id === session.account?.id || a.username === session.account?.username;
</script>

<Section title="Accounts" intro="Each person signs in with their own account and sees only their own profiles. Invite someone, then send them the link. It works once and lasts 14 days.">
	<div class="invite">
		<TextField label="Username (optional)" bind:value={newName} placeholder="Leave empty to let them choose" autocomplete="off" id="acct-new-name" />
		<Select label="Role" bind:value={newRole} options={roleOptions} />
		<button class="btn primary" use:focusable={{ id: 'acct-invite', onSelect: invite }}>Create invite</button>
	</div>

	{#if link}
		<div class="linkbox panel" data-testid="invite-link">
			<p class="small muted">Link for {link.who}</p>
			<input class="input" readonly value={link.url} aria-label="Invite link" onfocus={(e) => e.currentTarget.select()} use:focusable />
			<div class="hstack">
				<button class="btn mini" use:focusable={{ onSelect: () => copy(link!.url) }}>Copy link</button>
				<button class="btn mini ghost" use:focusable={{ onSelect: () => (link = null) }}>Hide</button>
			</div>
		</div>
	{/if}

	{#if !loaded}<div class="skeleton" style="height:8rem"></div>{/if}
	<div class="list">
		{#each accounts as a (a.id)}
			<div class="item" class:off={a.disabled}>
				<div class="head">
					<strong>{a.username ?? 'Invite (name not picked yet)'}{isMe(a) ? ' (you)' : ''}</strong>
					<span class="small muted">
						{a.role === 'admin' ? 'Administrator' : 'User'}
						· {a.disabled ? 'disabled' : a.registered ? `${a.sessions} signed in` : 'invite pending'}
						{#if a.invite} · {a.invite.purpose === 'reset' ? 'reset' : 'invite'} link until {new Date(a.invite.expires_at).toLocaleDateString()}{/if}
					</span>
				</div>
				<div class="profiles">
					{#each a.profiles as p (p.id)}<span class="chip" title={p.name}><Avatar emoji={p.emoji} color={p.color} size="1.6rem" /> {p.name}{p.role === 'child' ? ' · child' : ''}</span>{:else}<span class="small muted">No profiles yet</span>{/each}
				</div>
				{#each a.profiles.filter((p) => p.role === 'child') as p (p.id)}
					<div class="rails">
						<span class="small muted">{p.name}: {Object.values(p.controls ?? {}).filter(Boolean).length} of {Object.keys(p.controls ?? {}).length} switched on · safety rails {p.safety_rails ? 'set' : 'default'}</span>
						<button class="btn mini" use:focusable={{ group: `ac-${a.id}`, onSelect: () => (parentalFor = p) }}>Parental controls for {p.name}</button>
					</div>
				{/each}
				<div class="hstack acts">
					<button class="btn mini" use:focusable={{ group: `ac-${a.id}`, onSelect: () => reset(a) }}>{a.registered ? 'Reset password link' : 'New invite link'}</button>
					{#if !isMe(a)}
						<button class="btn mini" use:focusable={{ group: `ac-${a.id}`, onSelect: () => run(() => api.updateAccount(a.id, { role: a.role === 'admin' ? 'user' : 'admin' })) }}>Make {a.role === 'admin' ? 'user' : 'admin'}</button>
						<button class="btn mini" use:focusable={{ group: `ac-${a.id}`, onSelect: () => run(() => api.updateAccount(a.id, { disabled: !a.disabled })) }}>{a.disabled ? 'Enable' : 'Disable'}</button>
					{/if}
					{#if a.sessions > 0}<button class="btn mini" use:focusable={{ group: `ac-${a.id}`, onSelect: () => run(() => api.signOutAccount(a.id), 'Signed out everywhere') }}>Sign out everywhere</button>{/if}
					{#if !isMe(a)}<button class="btn mini danger" use:focusable={{ group: `ac-${a.id}`, onSelect: () => remove(a) }}>{confirmDelete === a.id ? 'Press again to delete everything' : 'Delete'}</button>{/if}
				</div>
			</div>
		{/each}
	</div>
</Section>

{#if unowned.length}
	<Section title="Unassigned profiles" intro="These profiles existed before accounts. Move each one, with all its history and playlists, to the account that should own it.">
		<div class="list">
			{#each unowned as p (p.id)}
				<div class="item row">
					<Avatar emoji={p.emoji} color={p.color} size="2.6rem" />
					<strong class="grow">{p.name}</strong>
					<Select label="Account" bind:value={assignTo[p.id]} options={accounts.map((a) => ({ value: a.id, label: a.username ?? `invite #${a.id}` }))} />
					<button class="btn mini" use:focusable={{ group: `un-${p.id}`, onSelect: () => assign(p) }}>Assign</button>
				</div>
			{/each}
		</div>
	</Section>
{/if}

{#if parentalFor}<ParentalControls child={parentalFor} admin onClose={() => (parentalFor = null)} onSaved={load} />{/if}

<style>
	.invite { display: grid; gap: 0.8rem; max-width: 30rem; }
	.linkbox { display: grid; gap: 0.5rem; }
	.list { display: grid; gap: 0.6rem; }
	.item {
		display: grid;
		gap: 0.6rem;
		padding: 0.8rem 0.9rem;
		border-radius: var(--r-md);
		background: rgba(255, 255, 255, 0.035);
		border: 1px solid var(--line);
	}
	.item.off { opacity: 0.6; }
	.item.row { display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; }
	.grow { flex: 1; }
	.head { display: grid; }
	.profiles { display: flex; flex-wrap: wrap; gap: 0.5rem; }
	.rails { display: flex; flex-direction: column; align-items: flex-start; gap: 0.4rem; white-space: pre-line; }
	.chip { display: inline-flex; align-items: center; gap: 0.35rem; font-size: 0.85rem; padding: 0.15rem 0.6rem 0.15rem 0.2rem; border-radius: 999px; border: 1px solid var(--line); }
	.acts { flex-wrap: wrap; gap: 0.4rem; }
	.btn.mini { min-height: 2.2rem; padding: 0.2rem 0.85rem; font-size: 0.85rem; }
</style>
