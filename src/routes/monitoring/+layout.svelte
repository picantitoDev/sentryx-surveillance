<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { page } from '$app/state';
	import type { Camera } from '$lib/camera';
	import { createLivekitViewer, initialMonitorState } from '$lib/services/livekit-viewer';
	let { children } = $props();
	let detail = $derived(page.url.pathname.replace(/\/$/, '') === '/monitoring/camera-1');
	let video = $state<HTMLVideoElement>();
	let cameras = $state<Camera[]>([]);
	let loading = $state(true);
	let registryError = $state('');
	let selected = $derived(
		cameras.find((camera) => camera.id === page.url.searchParams.get('camera')) ??
			(page.url.searchParams.has('camera') ? undefined : cameras[0])
	);
	let sourceId = $derived(selected?.sourceId ?? 'Sin cámara seleccionada');
	let selectionQuery = $derived(selected ? '?camera=' + encodeURIComponent(selected.id) : '');
	let viewerState = $state(initialMonitorState());
	let needsPlay = $state(false);

	let viewer = $state<ReturnType<typeof createLivekitViewer>>();
	async function playVideo() {
		if (video?.srcObject)
			try {
				await video.play();
				needsPlay = false;
			} catch {
				needsPlay = true;
			}
	}
	$effect(() => {
		if (!selected || !video) return;
		const connection = createLivekitViewer(
			selected,
			video,
			(next) => (viewerState = next),
			(blocked) => (needsPlay = blocked)
		);
		viewer = connection;
		viewerState = initialMonitorState();
		untrack(() => {
			void connection.start();
		});
		return () => {
			connection.stop();
			viewer = undefined;
		};
	});
	onMount(() => {
		const abort = new AbortController();
		void fetch('/api/cameras', { signal: abort.signal })
			.then(async (response) => {
				if (!response.ok) throw new Error('No se pudo cargar el registro de cámaras');
				cameras = await response.json();
			})
			.catch((e) => {
				if (!abort.signal.aborted) registryError = e.message;
			})
			.finally(() => (loading = false));
		const leave = () => viewer?.stop();
		window.addEventListener('pagehide', leave);
		return () => {
			abort.abort();
			window.removeEventListener('pagehide', leave);
		};
	});
</script>

<svelte:head
	><title>{detail ? (selected?.name ?? 'Cámara') : 'Monitoreo de cámaras'} | SentriX</title
	></svelte:head
>
<div class="min-h-full bg-slate-100 p-4 sm:p-8">
	<div class="mx-auto max-w-7xl">
		<header class="mb-6">
			{#if detail}<a
					href={'/monitoring' + selectionQuery}
					class="mb-4 inline-flex text-sm font-semibold text-slate-700 hover:underline"
					>← Volver al monitoreo</a
				>{/if}
			<h1 class="text-3xl font-bold text-slate-800">
				{detail ? (selected?.name ?? 'Cámara') : 'Monitoreo de cámaras'}
			</h1>
			<p class="mt-2 text-sm text-slate-600">
				Video por LiveKit y resultados de inferencia consultados por separado a la API.
			</p>
			<div class="mt-4 flex flex-wrap gap-3">
				<a
					href="/cameras"
					class="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-800"
					>Registrar / seleccionar cámara</a
				>
				{#if viewerState.active}<button
						type="button"
						onclick={() => {
							void viewer?.stop();
						}}
						class="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-800"
						>Detener visualización</button
					>
				{:else}<button
						type="button"
						onclick={() => {
							void viewer?.start();
						}}
						class="rounded-lg bg-blue-700 px-4 py-2 text-sm font-semibold text-white"
						disabled={!selected || loading}>Conectar visualización</button
					>{/if}
			</div>
		</header>
		{#if registryError}<p role="alert" class="mb-4 text-red-700">
				{registryError}
			</p>{/if}{#if !selected}<p class="mb-4 rounded-lg bg-white p-4 text-slate-700">
				{loading ? 'Cargando cámaras…' : 'Registra y selecciona una cámara antes de conectarte.'}
			</p>{/if}
		{#if selected}<div
				class={detail
					? 'grid grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(280px,1fr)]'
					: 'grid grid-cols-1 gap-6 lg:grid-cols-2'}
			>
				<section
					class="min-w-0 rounded-xl border border-slate-200 bg-white p-4"
					aria-label="Cámara 01"
				>
					<div class="mb-4 flex flex-wrap items-center justify-between gap-3">
						<div>
							<h2 class="font-semibold text-slate-800">
								{selected?.name ?? 'Sin cámara seleccionada'}
							</h2>
							<p class="text-sm text-slate-500">{sourceId}</p>
						</div>
						<span class="rounded-md bg-slate-100 px-3 py-1 text-xs text-slate-700"
							>LiveKit · Solo recepción</span
						>
					</div>
					<div class="relative overflow-hidden rounded-lg bg-slate-950">
						<!-- Pista remota de vigilancia, solo video sin audio. -->
						<!-- svelte-ignore a11y_media_has_caption -->
						<video
							bind:this={video}
							autoplay
							muted
							playsinline
							aria-label="Video remoto de la cámara registrada"
							class="aspect-video w-full object-contain">Tu navegador no admite video HTML5.</video
						>
						<div class="absolute top-3 right-3 left-3 flex items-start justify-between gap-3">
							<span class="rounded bg-slate-950/80 px-2 py-1 text-xs text-white"
								>{selected?.name ?? 'Cámara'}</span
							>
							{#if !detail}<a
									href={'/monitoring/camera-1' + selectionQuery}
									aria-label="Ampliar Cámara 01"
									class="rounded-lg bg-white px-3 py-2 text-sm font-semibold text-slate-900 shadow hover:bg-slate-200"
									>Ampliar ↗</a
								>{/if}
						</div>
						{#if needsPlay}<button
								type="button"
								onclick={playVideo}
								class="absolute bottom-4 left-1/2 -translate-x-1/2 rounded-lg bg-white px-4 py-2 font-semibold text-slate-900"
								>Reproducir</button
							>{/if}
					</div>
					<p role="status" class="mt-3 text-sm text-slate-600">{viewerState.status}</p>
					{#if detail}<p class="mt-2 text-sm text-slate-600">
							Inferencia: {viewerState.inferenceStatus}
						</p>{/if}
					{#if viewerState.error}<p role="alert" class="mt-2 text-sm break-words text-amber-800">
							{viewerState.error}
						</p>{/if}
				</section>
				{#if detail && selected}
					<aside
						class="rounded-xl border border-slate-200 bg-white p-6"
						aria-label="Resultados de la fuente"
					>
						<p class="text-xs font-semibold tracking-wider text-blue-700">
							{viewerState.result?.simulated ? 'Predicción simulada' : 'Resultados del servidor'}
						</p>
						<h2 class="mt-2 text-lg font-semibold text-slate-800">Acción detectada</h2>
						<div aria-live="polite" aria-atomic="true">
							<p class="mt-4 text-3xl font-bold text-slate-900">
								{viewerState.result?.clase ?? 'Esperando primera ventana'}
							</p>
						</div>
						{#if viewerState.stale}<p class="mt-3 text-sm font-semibold text-amber-800">
								Datos sin actualizar
							</p>{/if}
						<h3 class="mt-5 font-semibold text-slate-800">Resultados de Valentino</h3>
						<p class="mt-2 text-sm">
							{viewerState.stale
								? 'Resultado anterior; no confirma una alerta vigente.'
								: viewerState.result?.alert === true
									? 'Alerta confirmada por el backend'
									: viewerState.result?.alert === false
										? 'Sin alerta confirmada'
										: 'Esperando estado de alerta'}
						</p>

						<dl class="mt-5 space-y-2 text-sm text-slate-600">
							<div class="flex justify-between gap-3">
								<dt>Frames recibidos</dt>
								<dd>{viewerState.frames}</dd>
							</div>
							<div class="flex justify-between gap-3">
								<dt>Ventanas procesadas</dt>
								<dd>{viewerState.windows}</dd>
							</div>
							{#if viewerState.result}<div>
									<dt>Última ventana</dt>
									<dd class="break-all">{viewerState.result.window_id}</dd>
								</div>
								<div>
									<dt>Inferencia</dt>
									<dd>{viewerState.result.simulated ? 'Simulada' : 'Real'}</dd>
								</div>{/if}
						</dl>
						<p class="mt-5 text-sm leading-relaxed text-slate-600">
							Se muestra el último resultado recibido. El video y las predicciones se actualizan de
							forma independiente.
						</p>
					</aside>
				{/if}{#if !detail}
					{#each cameras.filter((camera) => camera.id !== selected?.id) as camera (camera.id)}
						<section
							class="rounded-xl border border-slate-200 bg-white p-4"
							aria-label={camera.name}
						>
							<div class="mb-4 flex items-center justify-between gap-3">
								<div>
									<h2 class="font-semibold text-slate-800">{camera.name}</h2>
									<p class="text-sm text-slate-500">{camera.location}</p>
								</div>
								<span class="rounded-md bg-slate-100 px-3 py-1 text-xs text-slate-700"
									>Registrada</span
								>
							</div>
							<div class="flex aspect-video items-center justify-center rounded-lg bg-slate-900">
								<a
									href={'/monitoring?camera=' + encodeURIComponent(camera.id)}
									class="rounded-lg bg-white px-4 py-2 font-semibold text-blue-700"
									>Seleccionar cámara</a
								>
							</div>
						</section>
					{/each}
				{/if}
			</div>
		{/if}{@render children()}
	</div>
</div>
