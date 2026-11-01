<script lang="ts">
	import Section from './Section.svelte';
	import SaveBar from './SaveBar.svelte';
	import { SettingsDraft } from './useSettings.svelte';
	import Select from '$lib/components/form/Select.svelte';
	import Toggle from '$lib/components/form/Toggle.svelte';
	import TextField from '$lib/components/form/TextField.svelte';
	import { api } from '$lib/api';
	import { focusable } from '$lib/nav';
	import { toast } from '$lib/stores/toast.svelte';
	import { browserNotify } from '$lib/stores/browserNotify.svelte';

	const d = new SettingsDraft('notify');
	const SEVERITIES = [
		{ value: 'info', label: 'Everything' },
		{ value: 'low', label: 'Low and above' },
		{ value: 'medium', label: 'Medium and above' },
		{ value: 'high', label: 'High and above' },
		{ value: 'critical', label: 'Critical only' }
	];
	let testing = $state(false);
	async function sendTest() {
		if (testing) return;
		testing = true;
		try {
			const r = await api.testNotification();
			const parts = Object.entries(r.results).map(([k, v]) => `${k}: ${v}`);
			toast.success(parts.length ? `Test sent — ${parts.join(', ')}` : 'No channel is configured yet.');
		} catch { /* the API shows the error */ }
		finally { testing = false; }
	}
</script>

<Section
	title="Notifications"
	intro="Alerts stay in WatchCraft. These channels also push them out: a JSON webhook, a Home Assistant webhook, or email. An empty field turns that channel off. Only alerts at or above the minimum severity are sent."
>
	{#snippet actions()}
		<button class="btn" disabled={testing} use:focusable onclick={sendTest}>{testing ? 'Sending…' : 'Send test'}</button>
	{/snippet}
	<Select label="Send alerts at or above" bind:value={d.value.min_severity} options={SEVERITIES} />
	<TextField label="Webhook URL" type="url" placeholder="https://example.org/hooks/watchcraft"
		hint="Receives a JSON POST: event, severity, title, reason, url, publisher, curator_id." bind:value={d.value.webhook_url} />
	<TextField label="Home Assistant webhook" type="url" placeholder="http://homeassistant.local:8123/api/webhook/…"
		hint="Create an automation with a webhook trigger, then paste its URL. The same JSON body arrives." bind:value={d.value.ha_webhook_url} />
	<TextField label="Email host" placeholder="smtp.example.org" hint="Leave empty to turn email off." bind:value={d.value.email_host} />
	{#if d.value.email_host}
		<label class="field"><span class="label">Email port</span>
			<input class="input" type="number" min="1" max="65535" aria-label="Email port" bind:value={d.value.email_port} use:focusable />
			<span class="small muted">587 for STARTTLS, 465 for implicit TLS.</span>
		</label>
		<Toggle label="Use STARTTLS" hint="Turn off only for a local relay that does not offer it." bind:checked={d.value.email_starttls} />
		<TextField label="Email user" hint="Optional. Used to log in and as the From address." bind:value={d.value.email_user} />
		<TextField label="Email password" type="password" hint="Shown as ******** once saved. Leave it as is to keep the saved password." bind:value={d.value.email_password} />
		<TextField label="Send to" placeholder="you@example.org" bind:value={d.value.email_to} />
	{/if}
	<h3>This browser</h3>
	<p class="muted small">Desktop notifications appear while WatchCraft is open in a tab. They need permission from this browser and are per device.</p>
	{#if browserNotify.supported}
		{#if browserNotify.permission === 'granted'}
			<Toggle label="Show desktop notifications" hint="New alerts pop up even when another tab is in front." bind:checked={browserNotify.enabled} />
		{:else if browserNotify.permission === 'denied'}
			<p class="small">This browser blocked notifications for WatchCraft. Allow them in the site settings, then reload.</p>
		{:else}
			<button class="btn" use:focusable onclick={() => browserNotify.request()}>Allow desktop notifications</button>
		{/if}
	{:else}
		<p class="small muted">This browser does not support desktop notifications.</p>
	{/if}
	<SaveBar dirty={d.dirty} busy={d.busy} onsave={() => d.save('Notification settings saved')} onreset={() => d.reset()} />
</Section>
