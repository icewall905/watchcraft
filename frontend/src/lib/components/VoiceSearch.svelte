<script lang="ts">
	import { onDestroy, tick } from 'svelte';
	import { api } from '$lib/api';
	import { focusable } from '$lib/nav';
	import { session } from '$lib/stores/session.svelte';
	import Icon from './Icon.svelte';
	import Modal from './Modal.svelte';

	let { onresult, group = 'voice-search', onactive }: {
		onresult: (text: string) => void; group?: string; onactive?: (active: boolean) => void;
	} = $props();
	let phase = $state<'idle' | 'permission' | 'listening' | 'processing' | 'error'>('idle');
	let error = $state('');
	let seconds = $state(0);
	let button: HTMLButtonElement;
	let done = $state<HTMLButtonElement>();
	let retry = $state<HTMLButtonElement>();
	let generation = 0;
	let profile: number | null = null;
	let stream: MediaStream | null = null;
	let recorder: MediaRecorder | null = null;
	let context: AudioContext | null = null;
	let interval: ReturnType<typeof setInterval> | null = null;
	let deadline: ReturnType<typeof setTimeout> | null = null;
	let request: AbortController | null = null;

	function release() {
		if (interval) clearInterval(interval);
		if (deadline) clearTimeout(deadline);
		interval = null; deadline = null;
		if (recorder) {
			recorder.onstop = null; recorder.onerror = null; recorder.ondataavailable = null;
			if (recorder.state !== 'inactive') recorder.stop();
		}
		recorder = null;
		stream?.getTracks().forEach(track => track.stop()); stream = null;
		context?.close().catch(() => {}); context = null;
	}
	function close(restore = true) {
		generation++; release(); request?.abort(); request = null;
		profile = null; phase = 'idle'; onactive?.(false);
		if (restore) button?.focus();
	}
	function fail(message: string) {
		release(); error = message; phase = 'error';
		void tick().then(() => { if (phase === 'error') retry?.focus(); });
	}
	function finish() {
		if (phase !== 'listening' || !recorder || recorder.state === 'inactive') return;
		phase = 'processing';
		if (interval) clearInterval(interval);
		if (deadline) clearTimeout(deadline);
		recorder.stop();
	}
	$effect(() => {
		const current = session.profileId;
		if (profile !== null && profile !== current) close(false);
	});
	onDestroy(() => close(false));

	async function start() {
		release(); request?.abort();
		const token = ++generation;
		profile = session.profileId;
		phase = 'permission'; error = ''; seconds = 0; onactive?.(true);
		if (!window.isSecureContext) return fail('Microphone access needs HTTPS. Open WatchCraft at its HTTPS address.');
		if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined')
			return fail('This browser cannot record a microphone. Use a browser with microphone recording support.');
		try {
			const input = await navigator.mediaDevices.getUserMedia({ audio: {
				channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true
			}, video: false });
			if (token !== generation) { input.getTracks().forEach(track => track.stop()); return; }
			stream = input;
			const mimeType = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4']
				.find(type => MediaRecorder.isTypeSupported(type));
			const capture = new MediaRecorder(input, mimeType ? { mimeType } : undefined);
			recorder = capture;
			const chunks: Blob[] = [];
			capture.ondataavailable = event => { if (token === generation && event.data.size) chunks.push(event.data); };
			capture.onerror = () => { if (token === generation) fail('The microphone recording failed. Please try again.'); };
			capture.onstop = async () => {
				if (token !== generation) return;
				const audio = new Blob(chunks, { type: capture.mimeType || 'audio/webm' });
				release(); phase = 'processing';
				request = new AbortController();
				try {
					const result = await api.transcribeAudio(audio, request.signal);
					if (token !== generation) return;
					close(); onresult(result.text);
				} catch (e) {
					if (token !== generation || (e as Error).name === 'AbortError') return;
					fail(e instanceof Error ? e.message : 'Voice search failed. Please try again.');
				}
			};
			capture.start(250); phase = 'listening';
			void tick().then(() => { if (token === generation && phase === 'listening') done?.focus(); });
			const started = performance.now();
			let lastSpeech = started, voicedFrames = 0;
			let analyser: AnalyserNode | null = null;
			let samples: Float32Array<ArrayBuffer> | null = null;
			try {
				context = new AudioContext();
				void context.resume().catch(() => {});
				if (token !== generation || phase !== 'listening') return;
				analyser = context.createAnalyser(); analyser.fftSize = 1024;
				context.createMediaStreamSource(input).connect(analyser);
				samples = new Float32Array(analyser.fftSize);
			} catch { /* Recording still works with the Done button when analysis is unavailable. */ }
			if (token !== generation || phase !== 'listening') return;
			interval = setInterval(() => {
				const now = performance.now(); seconds = Math.floor((now - started) / 1000);
				if (!analyser || !samples || context?.state !== 'running') return;
				analyser.getFloatTimeDomainData(samples);
				const rms = Math.sqrt(samples.reduce((sum, value) => sum + value * value, 0) / samples.length);
				if (rms > 0.008) { lastSpeech = now; voicedFrames++; }
				if (voicedFrames >= 3 && now - lastSpeech > 1400) finish();
				else if (!voicedFrames && now - started > 7000) fail('No speech was heard. Try again or check your microphone.');
			}, 100);
			deadline = setTimeout(finish, 20000);
		} catch (e) {
			if (token !== generation) return;
			const name = (e as Error).name;
			fail(name === 'NotAllowedError' ? 'Microphone access was denied. Allow it in your browser and try again.'
				: name === 'NotFoundError' ? 'No microphone was found. Connect or select your remote microphone and try again.'
				: 'Could not open the microphone. Check that it is connected and available.');
		}
	}
</script>

<button bind:this={button} type="button" class="btn voice" aria-label="Voice search" title="Voice search"
	use:focusable={{ group, onSelect: start }}><Icon name="mic" /></button>

{#if phase !== 'idle'}
	<Modal title="Voice search" size="sm" portal onClose={() => close()}>
		<div class="speech" class:listening={phase === 'listening'}>
			<Icon name="mic" size="2.6rem" />
			<p role="status" aria-live="polite">{phase === 'permission' ? 'Waiting for microphone permission…' : phase === 'listening' ? 'Listening…' : phase === 'processing' ? 'Turning speech into text…' : 'Please try again'}</p>
			{#if phase === 'listening'}<p class="small muted">Say your search, then pause. Or choose Done.</p><span class="small faint">{seconds}s / 20s</span>{/if}
			{#if error}<p class="err" role="alert">{error}</p>{/if}
		</div>
		{#snippet footer()}
			{#if phase === 'listening'}<button bind:this={done} type="button" class="btn primary" use:focusable={{ group: 'voice-dialog', autofocus: true, onSelect: finish }}>Done</button>{/if}
			{#if phase === 'error'}<button bind:this={retry} type="button" class="btn primary" use:focusable={{ group: 'voice-dialog', autofocus: true, onSelect: start }}>Try again</button>{/if}
			<button type="button" class="btn" use:focusable={{ group: 'voice-dialog', autofocus: phase === 'permission' || phase === 'processing', onSelect: () => close() }}>Cancel</button>
		{/snippet}
	</Modal>
{/if}

<style>
	.voice { flex: 0 0 auto; min-width: 2.8rem; padding: .55rem; }
	.speech { display: grid; justify-items: center; gap: .7rem; padding: 1rem 0; text-align: center; }
	.speech > :global(svg) { color: var(--accent); }
	.speech p { margin: 0; }
	.listening > :global(svg) { animation: breathe 1s ease-in-out infinite alternate; }
	@keyframes breathe { to { opacity: .45; transform: scale(.9); } }
	@media (prefers-reduced-motion: reduce) { .listening > :global(svg) { animation: none; } }
</style>
