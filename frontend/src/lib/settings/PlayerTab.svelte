<script lang="ts">
	import Section from './Section.svelte';
	import SaveBar from './SaveBar.svelte';
	import { SettingsDraft } from './useSettings.svelte';
	import Select from '$lib/components/form/Select.svelte';
	import { api } from '$lib/api';
	import { focusable } from '$lib/nav';
	import TextField from '$lib/components/form/TextField.svelte';
	import Toggle from '$lib/components/form/Toggle.svelte';

	const d = new SettingsDraft('player');
	const stt = new SettingsDraft('stt');
	let testing = $state(false);
	let testResult = $state('');
	async function testConnection() {
		if (testing) return;
		testing = true;
		testResult = '';
		try {
			const result = await api.speechTest($state.snapshot(stt.value));
			testResult = result.ok ? 'Speech recognition connection works.' : result.error || 'Connection failed.';
		} catch (e: any) {
			testResult = e?.message || 'Connection failed.';
		} finally { testing = false; }
	}
	const HEIGHTS = [480, 720, 1080, 1440, 2160].map((h) => ({ value: h, label: `${h}p`, hint: h === 1080 ? 'Best for the projector' : h > 1080 ? 'Needs a fast CPU or GPU decoding' : undefined }));
	const CODECS = [
		{ value: 'avc1', label: 'H.264', hint: 'Plays everywhere, lightest on the CPU. Recommended.' },
		{ value: 'vp9', label: 'VP9', hint: 'Better picture per bit, heavier to decode.' },
		{ value: 'av01', label: 'AV1', hint: 'Newest and most efficient, needs hardware support.' }
	];
	const SPEEDS = [0.75, 1, 1.25, 1.5, 1.75, 2].map((s) => ({ value: s, label: s === 1 ? 'Normal' : `${s}×` }));
	const LANGS = [
		{ value: '', label: 'Off' },
		{ value: 'en', label: 'English' },
		{ value: 'da', label: 'Danish' },
		{ value: 'de', label: 'German' },
		{ value: 'sv', label: 'Swedish' },
		{ value: 'no', label: 'Norwegian' },
		{ value: 'fr', label: 'French' },
		{ value: 'es', label: 'Spanish' }
	];
</script>

<Section title="Player" intro="How videos play on this server, for everyone.">
	<Select label="Maximum quality" hint="Higher quality uses more bandwidth through the server." bind:value={d.value.max_height} options={HEIGHTS} />
	<Select label="Preferred video format" bind:value={d.value.prefer_codec} options={CODECS} />
	<Select label="Default speed" bind:value={d.value.default_speed} options={SPEEDS} />
	<Select label="Captions" hint="Turn captions on automatically in this language when available." bind:value={d.value.captions_lang} options={LANGS} />
	<Toggle label="Autoplay next" hint="When a video ends, play the next pick from the same playlist after a 10 second countdown." bind:checked={d.value.autoplay_next} />
	<SaveBar stack={stt.dirty ? 1 : 0} dirty={d.dirty} busy={d.busy} onsave={() => d.save('Player settings saved')} onreset={() => d.reset()} />
</Section>

<Section title="Generated subtitles" intro="Use your speech recognition server as an alternative to YouTube captions. Generation only starts when you choose it in the player.">
	<Toggle label="Enable generated subtitles" bind:checked={stt.value.subtitles_enabled} />
	<TextField label="Speech recognition URL" type="url" hint="OpenAI-compatible server base URL, including /v1 when required. The server must be reachable from WatchCraft." placeholder="http://10.0.10.73:8000/v1" bind:value={stt.value.base_url} oninput={() => { if (stt.value.api_key === '********') stt.value.api_key = ''; testResult = ''; }} />
	<TextField label="Speech recognition model" placeholder="Qwen3-ASR-1.7B" bind:value={stt.value.model} />
	<TextField label="Speech recognition API key" type="password" hint="Leave empty for local servers. Shown as ******** once saved; leave that value unchanged to keep it." bind:value={stt.value.api_key} />
	<TextField label="Subtitle language" hint="Optional language code, such as en or da. Leave empty to detect automatically." bind:value={stt.value.language} />
	<button class="btn" disabled={testing} use:focusable={{ disabled: testing, onSelect: testConnection }}>{testing ? 'Testing…' : 'Test speech connection'}</button>
	{#if testResult}<p class="small" role="status">{testResult}</p>{/if}
	<SaveBar dirty={stt.dirty} busy={stt.busy} onsave={() => stt.save('Speech recognition settings saved')} onreset={() => stt.reset()} />
</Section>
