<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { deviceMode } from '$lib/stores/deviceMode.svelte';
	import Home from '../+page.svelte';

	onMount(() => { if (page.url.searchParams.get('mode') === 'tv') deviceMode.set('tv'); });
</script>

<Home />
