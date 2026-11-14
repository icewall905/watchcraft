<script lang="ts">
	import Section from './Section.svelte';
	import SaveBar from './SaveBar.svelte';
	import { SettingsDraft } from './useSettings.svelte';
	import Select from '$lib/components/form/Select.svelte';
	import Toggle from '$lib/components/form/Toggle.svelte';
	import TextField from '$lib/components/form/TextField.svelte';
	import Stepper from '$lib/components/form/Stepper.svelte';

	const d = new SettingsDraft('search');
	const mb = new SettingsDraft('moodboard_sources');
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

{#if mb.value && 'reddit' in mb.value}
<Section title="Mood Board image sources" intro="Where Mood Board curators look for new images. Social sources use public read APIs; every image is still checked by the vision model before it is pinned.">
	<Toggle label="Web image search" hint="The search provider above, with Wikimedia Commons as fallback." bind:checked={mb.value.web} />
	<Toggle label="Reddit" hint="Public image and gallery posts. 18+, spoiler and video posts are skipped." bind:checked={mb.value.reddit} />
	<Toggle label="Bluesky" hint="Public posts with images. Adult or graphic labelled posts are skipped." bind:checked={mb.value.bluesky} />
	<Toggle label="Mastodon" hint="Hashtag timelines on one server. Sensitive posts are skipped." bind:checked={mb.value.mastodon} />
	{#if mb.value.mastodon}<TextField label="Mastodon server" hint="Host name only, for example mastodon.social." placeholder="mastodon.social" bind:value={mb.value.mastodon_instance} />{/if}
	<TextField label="Flickr API key" type="password" hint="Optional. Flickr is used only with a key. Shown as ******** once saved." bind:value={mb.value.flickr_api_key} />
	<TextField label="Tumblr API key" type="password" hint="Optional. Tumblr is used only with a key. Shown as ******** once saved." bind:value={mb.value.tumblr_api_key} />
	<Stepper label="Time limit per source" hint="A slow source is skipped for that run." min={5} max={60} step={5} format={(v) => `${v} s`} bind:value={mb.value.source_timeout_s} />
	<p class="muted small">Instagram, TikTok, Facebook, X and Pinterest search offer no public access, so they are not searched. Public Pinterest boards can still be imported on a board.</p>
	<SaveBar stack={1} label="Save image sources" dirty={mb.dirty} busy={mb.busy} onsave={() => mb.save('Mood Board sources saved')} onreset={() => mb.reset()} />
</Section>
{/if}
