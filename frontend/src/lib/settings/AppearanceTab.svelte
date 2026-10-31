<script lang="ts">
	import { deviceMode } from '$lib/stores/deviceMode.svelte';
	import { api } from '$lib/api';
	import { focusable } from '$lib/nav';
	import { session } from '$lib/stores/session.svelte';
	import { toast } from '$lib/stores/toast.svelte';
	import { THEMES } from '$lib/themes';
	import Section from './Section.svelte';

	let saving = $state(false);
	let savingShelf = $state(false);
	async function saveShelf(patch: Record<string, unknown>) {
		const profile = session.profile;
		if (!profile || savingShelf) return;
		savingShelf = true;
		try {
			const updated = await api.updateProfile(profile.id, { prefs: { ...profile.prefs, ...patch } });
			session.profiles = session.profiles.map(p => p.id === updated.id ? updated : p);
		} catch { /* The API client displays the error. */ }
		finally { savingShelf = false; }
	}
	const TONES = [
		{ id: 'professional', name: 'Professional', description: 'Plain, neutral progress notes.' },
		{ id: 'friendly', name: 'Friendly', description: 'Warm, everyday language. Default.' },
		{ id: 'playful', name: 'Playful', description: 'Light and fun — always honest about what really happened.' }
	] as const;
	const selected = $derived(session.settings?.appearance?.theme ?? 'projector');
	async function select(id: string) {
		if (saving || id === selected || !session.settings) return;
		saving = true;
		try {
			const appearance = await api.saveSettings('appearance', { theme: id });
			if (session.settings) session.settings = { ...session.settings, appearance };
			toast.success(`${THEMES.find(t => t.id === id)?.name ?? 'Theme'} selected`);
		} catch {
			// The API client displays the error and the current theme remains selected.
		} finally {
			saving = false;
		}
	}
</script>

<div class="device-section">
<Section title="Device mode" intro="Choose how navigation works on this device. This preference is saved in this browser only.">
	<div class="themes" aria-label="Device mode">
		{#each [{ id: 'desktop' as const, name: 'Desktop mode', description: 'Keep the sidebar visible for quick navigation.' }, { id: 'tv' as const, name: 'TV mode', description: 'Hide the sidebar. Press Left at the edge of the page to open it; Right or Back closes it.' }] as mode}
			<button class="theme-card" class:selected={deviceMode.mode === mode.id} aria-pressed={deviceMode.mode === mode.id}
				use:focusable={{ group: 'device-mode', id: `mode-${mode.id}`, onSelect: () => deviceMode.set(mode.id) }}>
				<span class="details"><span class="name">{mode.name}</span><span class="small muted">{mode.description}</span></span>
			</button>
		{/each}
	</div>
</Section>
</div>

{#if session.hubAllowed}
<div class="device-section">
<Section title="Home activity shelf" intro="Show research activity and recent discoveries on this profile's home page.">
 <div class="themes" aria-label="Scouts shelf settings">
  <button class="theme-card" class:selected={session.profile?.prefs?.scouts_home === true} aria-pressed={session.profile?.prefs?.scouts_home === true} disabled={savingShelf} use:focusable={{ group: 'scouts-settings', onSelect: () => saveShelf({ scouts_home: session.profile?.prefs?.scouts_home !== true }) }}><span class="details"><span class="name">Scouts shelf: {session.profile?.prefs?.scouts_home === true ? 'On' : 'Off'}</span><span class="small muted">Optional for each profile; off by default.</span></span></button>
  {#if session.profile?.prefs?.scouts_home === true}
   <button class="theme-card" disabled={savingShelf} use:focusable={{ group: 'scouts-settings', onSelect: () => saveShelf({ scouts_home_position: session.profile?.prefs?.scouts_home_position === 'bottom' ? 'top' : 'bottom' }) }}><span class="details"><span class="name">Position: {session.profile?.prefs?.scouts_home_position === 'bottom' ? 'After video rows' : 'Before video rows'}</span><span class="small muted">Select to change the shelf position.</span></span></button>
  {/if}
 </div>
</Section>
</div>
<div class="device-section">
<Section title="Research team voice" intro="How Source Scout and Briefing Writer describe their work. Each curator has its own voice in its settings.">
 <div class="themes" aria-label="Research team voice">
  {#each TONES as t}
   <button class="theme-card" class:selected={(session.profile?.prefs?.hub_tone ?? 'friendly') === t.id} aria-pressed={(session.profile?.prefs?.hub_tone ?? 'friendly') === t.id} disabled={savingShelf}
	use:focusable={{ group: 'hub-tone', id: `tone-${t.id}`, onSelect: () => saveShelf({ hub_tone: t.id }) }}>
	<span class="details"><span class="name">{t.name}</span><span class="small muted">{t.description}</span></span>
   </button>
  {/each}
 </div>
</Section>
</div>
{/if}

<Section title="Appearance" intro="Choose a look for WatchCraft. Your selection is shared across profiles and devices and applies immediately.">
	<div class="themes" aria-label="Themes">
		{#each THEMES as theme (theme.id)}
			<button class="theme-card" class:selected={selected === theme.id} aria-pressed={selected === theme.id}
				disabled={saving || !session.isAdmin} use:focusable={{ group: 'appearance-themes', id: `theme-${theme.id}`, onSelect: () => select(theme.id) }}>
				<span class="preview" aria-hidden="true" style:--preview-bg={theme.bg1} style:--preview-surface={theme.surface} style:--preview-accent={theme.accent} style:--preview-ai={theme.ai} style:--preview-text={theme.text}>
					<span class="preview-header"><span></span><span></span><span></span></span>
					<span class="preview-content"><span class="preview-lines"><b>Aa</b><i></i><i></i></span><span class="preview-art"></span></span>
					<span class="preview-button"></span>
				</span>
				<span class="details"><span class="name">{theme.name}{#if selected === theme.id}<span class="chosen">✓ Selected</span>{/if}</span><span class="small muted">{theme.description}</span></span>
			</button>
		{/each}
	</div>
	<p class="small muted note" aria-live="polite">{!session.isAdmin ? 'An administrator can change the shared theme.' : saving ? 'Saving theme…' : 'Changes appear on other devices when they reload. Select Projector to return to the original look.'}</p>
</Section>

<style>
	.device-section { margin-bottom: 2rem; }
	.themes { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 14rem), 1fr)); gap: 1rem; }
	.theme-card { display: grid; gap: 0; padding: 0; text-align: left; color: var(--text); border: 1px solid var(--line-strong); border-radius: var(--r-md); background: var(--surface); overflow: hidden; cursor: pointer; }
	.theme-card:hover { border-color: var(--accent); background: var(--surface-2); }
	.theme-card.selected { border: 2px solid var(--accent); }
	.preview { height: 8.5rem; display: grid; align-content: start; gap: 0.55rem; padding: 0.7rem; background: var(--preview-bg); color: var(--preview-text); }
	.preview-header { display: flex; gap: 0.25rem; align-items: center; height: 1rem; padding: 0 0.4rem; border-radius: 0.3rem; background: var(--preview-surface); }
	.preview-header span { width: 0.32rem; height: 0.32rem; border-radius: 50%; background: var(--preview-accent); opacity: 0.8; }
	.preview-content { display: flex; gap: 0.5rem; align-items: stretch; }
	.preview-lines { display: grid; align-content: center; gap: 0.3rem; flex: 1; padding: 0.45rem; border-radius: 0.35rem; background: var(--preview-surface); }
	.preview-lines b { font-size: 0.9rem; }
	.preview-lines i { width: 70%; height: 0.2rem; border-radius: 1rem; background: var(--preview-text); opacity: 0.3; }
	.preview-lines i:last-child { width: 45%; }
	.preview-art { width: 40%; min-height: 2.6rem; border-radius: 0.35rem; background: linear-gradient(135deg, var(--preview-ai), var(--preview-surface)); }
	.preview-button { width: 3.6rem; height: 0.55rem; border-radius: 1rem; background: var(--preview-accent); }
	.details { display: grid; gap: 0.25rem; padding: 0.85rem; }
	.name { display: flex; justify-content: space-between; gap: 0.5rem; align-items: center; font-weight: 750; }
	.chosen { color: var(--accent); font-size: 0.75rem; white-space: nowrap; }
	.note { margin-top: 1rem; }
</style>
