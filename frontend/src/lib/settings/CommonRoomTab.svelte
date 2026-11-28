<script lang="ts">
	import Section from './Section.svelte';
	import SaveBar from './SaveBar.svelte';
	import { SettingsDraft } from './useSettings.svelte';
	import Toggle from '$lib/components/form/Toggle.svelte';
	import TextField from '$lib/components/form/TextField.svelte';
	import LanguageChecklist from './LanguageChecklist.svelte';
	import { crApi } from '$lib/hub/commonroom';
	import { errMsg } from '$lib/hub/util';

	const cr = new SettingsDraft('commonroom');
	// A tab opened before an update may hold settings without this section: start from the defaults.
	if (!cr.value || !('show_nsfw' in cr.value)) {
		cr.value = { reddit_client_id: '', reddit_secret: '', stackexchange_key: '', show_nsfw: false };
		cr.saved = '';
	}
	let langCounts = $state<Record<string, number>>({});
	let langChosen = $state<string[]>([]);
	let langState = $state<'loading' | 'ready' | string>('loading');
	$effect(() => {
		crApi.languages().then((r) => {
			langCounts = Object.fromEntries(r.languages.map((l) => [l.key, l.count]));
			langChosen = r.allowed;
			langState = 'ready';
		}, (e) => { langState = errMsg(e); });
	});
	const saveLanguages = async (next: string[]) => (await crApi.setLanguages(next)).allowed;
	const keyed = $derived.by(() => { const s = JSON.parse(cr.saved || '{}'); return !!s.reddit_client_id && !!s.reddit_secret; });
</script>

<Section title="Common Room" intro="Keys for the Common Room social wall. Without Reddit keys, Reddit is read slowly through public RSS: the newest, hot and top-of-week/month/year/all lists, without scores, comments or search. Every other platform works without keys.">
	<p class="small" class:warn={!keyed} role="status">{keyed ? 'Reddit keys are set: full Reddit access with scores, comments and older posts.' : 'Reddit is in limited mode until you add keys.'}</p>
	<p class="muted small">Create a Reddit "script" app at <a href="https://www.reddit.com/prefs/apps" target="_blank" rel="noopener noreferrer">reddit.com/prefs/apps</a>, then paste its client id and secret.</p>
	<TextField label="Reddit client id" bind:value={cr.value.reddit_client_id} />
	<TextField label="Reddit secret" type="password" hint="Shown as ******** once saved. Leave it as is to keep the saved secret." bind:value={cr.value.reddit_secret} />
	<TextField label="Stack Exchange key" type="password" hint="Optional. Raises the request quota." bind:value={cr.value.stackexchange_key} />
	<Toggle label="Show NSFW posts" hint="Blurred until clicked. Always hidden for child profiles." bind:checked={cr.value.show_nsfw} />
	<SaveBar label="Save Common Room" dirty={cr.dirty} busy={cr.busy} onsave={() => cr.save('Common Room settings saved')} onreset={() => cr.reset()} />
</Section>

<Section title="Post languages" intro="Choose the languages you want on your Common Room wall. Posts in other languages are never judged or shown, also when new ones turn up later. Posts whose language could not be told are always shown. This applies to the profile you are using now.">
	{#if langState === 'loading'}<p class="muted">Loading…</p>
	{:else if langState !== 'ready'}<p class="warn" role="alert">{langState}</p>
	{:else}<LanguageChecklist counts={langCounts} bind:chosen={langChosen} save={saveLanguages} noun="posts" />{/if}
</Section>
