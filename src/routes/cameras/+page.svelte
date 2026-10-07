<script lang="ts">
	import { onMount } from 'svelte';
	import type { Camera } from '$lib/camera';
	let cameras = $state<Camera[]>([]);
	let loading = $state(true);
	let saving = $state(false);
	let showForm = $state(false);
	let error = $state('');
	let notice = $state('');
	let name = $state('');
	let location = $state('');
	const defaults = {
		sourceId: 'webcam-prueba',
		room: 'prueba-webcam-aislada',
		participantIdentity: 'webcam-prueba'
	};
	let dialog: HTMLDialogElement;
	let sourceId = $state(defaults.sourceId);
	let room = $state(defaults.room);
	let participantIdentity = $state(defaults.participantIdentity);
	onMount(() => {
		const controller = new AbortController();
		void fetch('/api/cameras', { signal: controller.signal })
			.then(async (response) => {
				if (!response.ok) throw new Error('No se pudo cargar el registro de cámaras');
				cameras = await response.json();
			})
			.catch((e) => {
				if (!controller.signal.aborted) error = e.message;
			})
			.finally(() => (loading = false));
		return () => controller.abort();
	});
	async function register(event: SubmitEvent) {
		event.preventDefault();
		if (saving) return;
		saving = true;
		error = '';
		notice = '';
		try {
			const response = await fetch('/api/cameras', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ name, location, sourceId, room, participantIdentity })
			});
			const data = await response.json();
			if (!response.ok) throw new Error(data.message || 'No se pudo registrar la cámara');
			cameras = [...cameras, data];
			showForm = false;
			name = location = '';
			sourceId = defaults.sourceId;
			room = defaults.room;
			participantIdentity = defaults.participantIdentity;
			dialog.close();
			notice = 'Cámara registrada. Ya puedes abrir el monitor y conectarte.';
		} catch (e) {
			error = e instanceof Error ? e.message : 'Error al guardar';
		} finally {
			saving = false;
		}
	}
	let deleting = $state<string | null>(null);
	async function removeCamera(camera: Camera) {
		if (
			deleting ||
			!window.confirm(
				`¿Eliminar «${camera.name}» del registro? Podrás volver a registrarla. Esto no detiene el emisor ni borra resultados de Valentino.`
			)
		)
			return;
		deleting = camera.id;
		error = '';
		notice = '';
		try {
			const response = await fetch('/api/cameras/' + encodeURIComponent(camera.id), {
				method: 'DELETE'
			});
			if (!response.ok && response.status !== 404) {
				const data = await response.json();
				throw new Error(data.message || 'No se pudo eliminar la cámara');
			}
			cameras = cameras.filter((item) => item.id !== camera.id);
			notice = 'Cámara eliminada del registro.';
		} catch (e) {
			error = e instanceof Error ? e.message : 'No se pudo eliminar la cámara';
		} finally {
			deleting = null;
		}
	}
</script>

<svelte:head><title>Cámaras | SentriX</title></svelte:head>
<div class="camera-page mx-auto max-w-7xl p-4 sm:p-8">
	<div class="flex flex-wrap items-center justify-between gap-4">
		<div>
			<h1 class="text-3xl font-bold text-slate-800">Cámaras</h1>
			<p class="mt-2 text-sm text-slate-600">Gestión de las fuentes de video</p>
		</div>
		<button
			type="button"
			onclick={() => {
				error = '';
				showForm = true;
				dialog.showModal();
			}}
			aria-expanded={showForm}
			class="add-camera rounded-lg px-5 py-3 font-semibold text-white"
			>{showForm ? 'Cerrar formulario' : '+ Agregar cámara'}</button
		>
	</div>
	{#if error}<p role="alert" class="mt-4 rounded-lg bg-red-50 p-4 text-red-800">{error}</p>{/if}
	{#if notice}<p role="status" class="mt-4 rounded-lg bg-blue-50 p-4 text-blue-800">
			{notice}
		</p>{/if}

	<dialog
		bind:this={dialog}
		class="register-dialog"
		aria-labelledby="register-title"
		onclose={() => (showForm = false)}
		oncancel={(event) => {
			if (saving) event.preventDefault();
		}}
	>
		{#if showForm}
			<form onsubmit={register}>
				<header class="dialog-header">
					<div class="camera-symbol" aria-hidden="true">▣</div>
					<div>
						<h2 id="register-title">Registrar cámara</h2>
						<p>Registra una nueva cámara para visualizar y analizar el video en el sistema.</p>
					</div>
					<button
						class="close-dialog"
						type="button"
						aria-label="Cerrar registro"
						disabled={saving}
						onclick={() => dialog.close()}>×</button
					>
				</header>
				{#if error}<p role="alert" class="form-error">{error}</p>{/if}
				<div class="basic-fields">
					<label
						>Nombre de la cámara <span class="required">*</span><input
							required
							maxlength="100"
							bind:value={name}
							placeholder="Cámara de entrada"
							aria-describedby="name-help"
						/><small id="name-help"
							>Usa un nombre que te permita identificar fácilmente la cámara.</small
						></label
					>
					<label
						>Ubicación <span class="required">*</span><input
							required
							maxlength="100"
							bind:value={location}
							placeholder="Entrada principal"
							aria-describedby="location-help"
						/><small id="location-help">Indica dónde se encuentra la cámara.</small></label
					>
				</div>
				<aside class="connection-note">
					<span class="info-symbol" aria-hidden="true">i</span>
					<div>
						<strong
							>{room === defaults.room &&
							participantIdentity === defaults.participantIdentity &&
							sourceId === defaults.sourceId
								? 'Se vinculará con la transmisión configurada de Gabriel.'
								: 'Se vinculará con la transmisión indicada en la configuración avanzada.'}</strong
						>
						<p>
							Esta cámara utilizará la sala, identidad del emisor y fuente de inferencia indicadas
							abajo.
						</p>
					</div>
				</aside>
				<details class="advanced">
					<summary
						><span
							><strong>Configuración avanzada</strong><small
								>Conexión preconfigurada. Estos datos son de solo lectura para evitar cambios
								accidentales.</small
							></span
						><span class="chevron" aria-hidden="true">⌄</span></summary
					>
					<div class="advanced-body">
						<div class="technical-fields">
							<label
								>Sala de LiveKit<input
									maxlength="100"
									readonly
									value={room}
									aria-describedby="room-help"
								/><small id="room-help">Sala donde se recibirá el video.</small></label
							>
							<label
								>Identidad del emisor<input
									maxlength="100"
									readonly
									value={participantIdentity}
									aria-describedby="identity-help"
								/><small id="identity-help">Identidad del participante que enviará el video.</small
								></label
							>
							<label
								>Fuente de inferencia<input
									maxlength="100"
									readonly
									value={sourceId}
									aria-describedby="source-help"
								/><small id="source-help">Identificador usado por la API de inferencia.</small
								></label
							>
						</div>
						<p class="advanced-note">
							Estos valores mantienen la conexión con Gabriel y la API de inferencia. Para
							configurar otra transmisión, contacta al administrador.
						</p>
					</div>
				</details>
				<footer class="dialog-actions">
					<button
						type="button"
						class="cancel-button"
						disabled={saving}
						onclick={() => dialog.close()}>Cancelar</button
					><button
						class="add-camera rounded-lg px-5 py-3 font-semibold text-white disabled:opacity-50"
						disabled={saving}>{saving ? 'Registrando…' : 'Registrar cámara'}</button
					>
				</footer>
			</form>
		{/if}
	</dialog>
	<div class="camera-table" aria-busy={loading}>
		<table>
			<thead>
				<tr
					><th scope="col">Nombre / ubicación</th><th scope="col">Estado</th><th scope="col"
						>Fuente</th
					><th scope="col">FPS</th><th scope="col">Acciones</th></tr
				>
			</thead>
			<tbody>
				{#if loading}
					<tr><td colspan="5" class="empty-state">Cargando cámaras…</td></tr>
				{:else if !cameras.length}
					<tr
						><td colspan="5" class="empty-state"
							>Todavía no hay cámaras registradas. Usa «Agregar cámara» para agregar la primera.</td
						></tr
					>
				{:else}
					{#each cameras as camera (camera.id)}
						<tr>
							<td
								><span class="camera-name">{camera.name}</span><span class="camera-location"
									>{camera.location}</span
								></td
							>
							<td>Registrada</td>
							<td>LiveKit</td>
							<td><span title="FPS no disponible en el registro de cámaras">—</span></td>
							<td
								><a
									class="table-action"
									href={'/monitoring?camera=' + encodeURIComponent(camera.id)}
									>Abrir monitor<span class="sr-only"> de {camera.name}</span></a
								><button
									type="button"
									class="delete-camera"
									disabled={deleting !== null}
									onclick={() => removeCamera(camera)}
									aria-label={'Eliminar ' + camera.name}
									>{deleting === camera.id ? 'Eliminando…' : 'Eliminar'}</button
								></td
							>
						</tr>
					{/each}
				{/if}
			</tbody>
		</table>
	</div>
</div>

<style>
	.delete-camera {
		margin-left: 8px;
		border: 1px solid #fecaca;
		border-radius: 8px;
		padding: 7px 11px;
		color: #b91c1c;
		font-size: 0.8125rem;
		background: white;
	}
	.delete-camera:hover {
		background: #fff1f2;
	}
	.delete-camera:disabled {
		opacity: 0.5;
		cursor: wait;
	}
	.delete-camera:focus-visible {
		outline: 2px solid #b91c1c;
		outline-offset: 3px;
	}

	.register-dialog {
		width: min(1100px, calc(100vw - 32px));
		max-height: calc(100dvh - 40px);
		margin: auto;
		padding: 30px;
		border: 1px solid #e1e8f3;
		border-radius: 16px;
		color: #0c2349;
		background: white;
		box-shadow: 0 24px 80px #0b214233;
	}
	.register-dialog::backdrop {
		background: #12284780;
		backdrop-filter: blur(3px);
	}
	.dialog-header {
		display: flex;
		align-items: center;
		gap: 20px;
		margin-bottom: 30px;
	}
	.dialog-header h2 {
		font-size: 1.75rem;
		font-weight: 700;
	}
	.dialog-header p {
		color: #59729a;
		margin-top: 4px;
	}
	.camera-symbol {
		display: grid;
		place-items: center;
		width: 64px;
		height: 64px;
		flex-shrink: 0;
		background: #eaf2ff;
		border: 1px solid #dceaff;
		border-radius: 12px;
		color: #0048cc;
		font-size: 30px;
	}
	.close-dialog {
		align-self: flex-start;
		margin-left: auto;
		padding: 0 8px;
		font-size: 30px;
		color: #526887;
	}
	.basic-fields {
		display: grid;
		gap: 24px;
	}
	.register-dialog label {
		display: block;
		font-size: 0.95rem;
		font-weight: 600;
	}
	.required {
		color: #dc2626;
	}
	.register-dialog input {
		display: block;
		width: 100%;
		margin-top: 9px;
		padding: 12px 15px;
		background: white;
		border: 1px solid #c7d4e6;
		border-radius: 9px;
		font-weight: 400;
		color: #0c2349;
	}
	.register-dialog input:focus {
		outline: 2px solid #005bff;
		outline-offset: 2px;
	}
	.register-dialog small {
		display: block;
		margin-top: 7px;
		font-size: 0.85rem;
		font-weight: 400;
		color: #5c7090;
	}
	.connection-note {
		display: flex;
		align-items: flex-start;
		gap: 14px;
		margin: 28px 0;
		padding: 18px;
		border: 1px solid #cfe0ff;
		border-radius: 10px;
		background: #edf4ff;
	}
	.connection-note p {
		margin-top: 5px;
		color: #526c94;
		font-size: 0.9rem;
	}
	.info-symbol {
		border-radius: 50%;
		background: #005bff;
		color: white;
		width: 24px;
		height: 24px;
		text-align: center;
		flex-shrink: 0;
		font-weight: 700;
	}
	.advanced {
		border: 1px solid #e2e8f3;
		border-radius: 12px;
		background: #f8faff;
	}
	.advanced summary {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 16px;
		padding: 18px 20px;
		cursor: pointer;
		list-style: none;
	}
	.advanced summary::-webkit-details-marker {
		display: none;
	}
	.advanced[open] .chevron {
		transform: rotate(180deg);
	}
	.advanced-body {
		padding: 20px;
		border-top: 1px solid #e2e8f3;
	}
	.technical-fields {
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		gap: 24px;
	}
	.technical-fields input {
		background: #f1f5fa;
	}
	.advanced-note {
		margin-top: 20px;
		padding: 12px 16px;
		background: #edf4ff;
		border: 1px solid #d4e4ff;
		border-radius: 8px;
		color: #0048cc;
		font-size: 0.85rem;
	}
	.dialog-actions {
		display: flex;
		justify-content: flex-end;
		gap: 12px;
		margin-top: 24px;
	}
	.cancel-button {
		padding: 12px 20px;
		border: 1px solid #c7d4e6;
		border-radius: 9px;
		color: #40577a;
	}
	.form-error {
		padding: 12px;
		margin-bottom: 20px;
		border-radius: 8px;
		background: #fff1f2;
		color: #b91c1c;
	}
	@media (max-width: 700px) {
		.register-dialog {
			padding: 20px;
		}
		.technical-fields {
			grid-template-columns: 1fr;
			gap: 16px;
		}
		.camera-symbol {
			display: none;
		}
		.dialog-header h2 {
			font-size: 1.4rem;
		}
		.dialog-actions {
			flex-wrap: wrap;
		}
	}

	.camera-page {
		color: #103653;
		padding-top: 3rem;
		padding-bottom: 3rem;
	}
	.add-camera {
		background: var(--color-blue-700, #0048cc);
		font-size: 0.875rem;
		transition: background 150ms;
	}
	.add-camera:hover {
		background: var(--color-blue-600, #005bff);
	}
	.camera-table {
		margin-top: 1.75rem;
		overflow-x: auto;
		border: 1px solid #dce5f0;
		border-radius: 8px;
		background: white;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		text-align: left;
		font-size: 0.875rem;
	}
	thead {
		background: #f5f7fa;
	}
	th {
		padding: 14px 18px;
		color: #68809c;
		font-size: 0.8125rem;
		font-weight: 600;
		white-space: nowrap;
	}
	td {
		padding: 20px 18px;
		border-top: 1px solid #e5edf5;
		vertical-align: middle;
	}
	tbody tr:first-child td {
		border-top: 0;
	}
	th:first-child {
		width: 28%;
	}
	th:nth-child(2) {
		width: 16%;
	}
	th:nth-child(3) {
		width: 14%;
	}
	th:nth-child(4) {
		width: 10%;
	}
	.camera-name {
		display: block;
		font-weight: 500;
		overflow-wrap: anywhere;
	}
	.camera-location {
		display: block;
		margin-top: 4px;
		color: #7186a1;
		font-size: 0.8125rem;
		overflow-wrap: anywhere;
	}
	.table-action {
		display: inline-flex;
		border: 1px solid #d1dfec;
		border-radius: 8px;
		padding: 7px 11px;
		font-size: 0.8125rem;
		white-space: nowrap;
		text-decoration: none;
	}
	.table-action:hover {
		background: #f0f6fa;
		border-color: #9bb6cd;
	}
	.table-action:focus-visible,
	.add-camera:focus-visible {
		outline: 2px solid var(--color-blue-700, #0048cc);
		outline-offset: 3px;
	}
	.empty-state {
		padding: 32px 18px;
		color: #68809c;
	}
	@media (max-width: 640px) {
		table {
			min-width: 620px;
		}
		.camera-page {
			padding-top: 1.5rem;
		}
	}
</style>
