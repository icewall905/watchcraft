<script lang="ts">
	import Section from './Section.svelte';
	import SaveBar from './SaveBar.svelte';
	import { SettingsDraft } from './useSettings.svelte';
	import Select from '$lib/components/form/Select.svelte';
	import Toggle from '$lib/components/form/Toggle.svelte';
	import TextField from '$lib/components/form/TextField.svelte';

	const d = new SettingsDraft('search');
	const PROVIDERS = [
		{ value: 'none', label: 'Off', hint: 'Curators can still use feeds, GitHub, Hacker News, arXiv, Wikipedia and advisories.' },
		{ value: 'searxng', label: 'SearXNG (self-hosted)', hint: 'Recommended. Private, no API key, you control the instance.' },
		{ value: 'brave', label: 'Brave Search API', hint: 'Hosted. Needs an API key.' }
	];
</script>

<Section title="Search and sources" intro="Web search lets curators and research briefs look beyond feeds. Queries are sent to the provider you choose here, nowhere else.">
	<Select label="Web search provider" bind:value={d.value.provider} options={PROVIDERS} />
	{#if d.value.provider === 'searxng'}
		<TextField label="SearXNG URL" type="url" hint="Your instance, with JSON output enabled (search.formats includes json). Example: https://searx.example.org" placeholder="https://searx.example.org" bind:value={d.value.searxng_url} />
	{:else if d.value.provider === 'brave'}
		<TextField label="Brave API key" type="password" hint="Shown as ******** once saved. Leave it as is to keep the saved key." bind:value={d.value.brave_api_key} />
	{/if}
	<Toggle label="Use the configured AI model" hint="For curator relevance, drafts, reports and briefings. Off = simple keyword rules; nothing is sent to the AI model." bind:checked={d.value.hub_ai} />
	<p class="muted small">Manage what each curator may visit under <a href="/sources">Sources</a>.</p>
	<SaveBar dirty={d.dirty} busy={d.busy} onsave={() => d.save('Search settings saved')} onreset={() => d.reset()} />
</Section>
