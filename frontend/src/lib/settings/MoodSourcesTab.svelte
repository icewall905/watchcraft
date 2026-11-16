<script lang="ts">
	import Section from './Section.svelte';
	import SaveBar from './SaveBar.svelte';
	import { SettingsDraft } from './useSettings.svelte';
	import Toggle from '$lib/components/form/Toggle.svelte';
	import TextField from '$lib/components/form/TextField.svelte';
	import Stepper from '$lib/components/form/Stepper.svelte';
	import { boardsApi, type PinterestStatus } from '$lib/hub/boards';

	const mb = new SettingsDraft('moodboard_sources');
	let pinterest = $state<PinterestStatus | null>(null);
	$effect(() => { boardsApi.pinterestLiveStatus().then((s) => { pinterest = s; }, () => { pinterest = null; }); });
	const when = (t: number) => new Date(t * 1000).toLocaleString();
</script>

{#if mb.value && 'reddit' in mb.value}
<Section title="Mood Board image sources" intro="Where Mood Board curators look for new images. Social sources use public read APIs; every image is still checked by the vision model before it is pinned.">
	<Toggle label="Web image search" hint="The provider under Web search, with Wikimedia Commons as fallback." bind:checked={mb.value.web} />
	<Toggle label="Reddit" hint="Public image and gallery posts. 18+, spoiler and video posts are skipped." bind:checked={mb.value.reddit} />
	<Toggle label="Bluesky" hint="Public posts with images. Adult or graphic labelled posts are skipped." bind:checked={mb.value.bluesky} />
	<Toggle label="Mastodon" hint="Hashtag timelines on one server. Sensitive posts are skipped." bind:checked={mb.value.mastodon} />
	{#if mb.value.mastodon}<TextField label="Mastodon server" hint="Host name only, for example mastodon.social." placeholder="mastodon.social" bind:value={mb.value.mastodon_instance} />{/if}
	<TextField label="Flickr API key" type="password" hint="Optional. Flickr is used only with a key. Shown as ******** once saved." bind:value={mb.value.flickr_api_key} />
	<TextField label="Tumblr API key" type="password" hint="Optional. Tumblr is used only with a key. Shown as ******** once saved." bind:value={mb.value.tumblr_api_key} />
	<Stepper label="Time limit per source" hint="A slow source is skipped for that run." min={5} max={60} step={5} format={(v) => `${v} s`} bind:value={mb.value.source_timeout_s} />
	<Toggle label="Pinterest (scraping, off by default)" hint="Searches Pinterest for curators and lets boards search it live. Pinterest's robots.txt and terms of service forbid automated access, so use at your own risk. Kept slow on purpose: a few requests per run, 12–30 s apart, at most 15 an hour and 80 a day. It pauses itself and switches off if Pinterest blocks it or shows a captcha." bind:checked={mb.value.pinterest} />
	{#if pinterest?.blocked}<p class="small warn" role="status">Pinterest paused itself on {when(pinterest.last_block_at)}: {pinterest.reason}. It stays off until {when(pinterest.blocked_until)}; switch it on again after that to retry.</p>
	{:else if pinterest?.last_reason && !mb.value.pinterest}<p class="small muted" role="status">Pinterest last paused itself on {when(pinterest.last_block_at)}: {pinterest.last_reason}. You can switch it on again.</p>
	{:else if pinterest && mb.value.pinterest}<p class="small muted">Left today: {pinterest.day_left} searches ({pinterest.hour_left} this hour), {pinterest.images_left} images.</p>{/if}
	<p class="muted small">Instagram, TikTok, Facebook and X offer no public access, so they are not searched. Boards can always open Pinterest in your browser so you can paste images, or import a public Pinterest board. Everything you add is stored on this server.</p>
	<SaveBar label="Save image sources" dirty={mb.dirty} busy={mb.busy} onsave={() => mb.save('Mood Board sources saved')} onreset={() => mb.reset()} />
</Section>
{/if}
