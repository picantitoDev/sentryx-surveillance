import { ApiError, type RequestOptions } from './api.ts';

type Json = Record<string, unknown>;
export type Result = {
	clase: string;
	confianza: number;
	simulated: boolean;
	window_id: string;
	timestamp: number;
};
export type ViewerState = {
	status: string;
	active: boolean;
	stale: boolean;
	result: Result | null;
	sessionId: string | null;
	viewerId: string | null;
	frames: number;
	windows: number;
	error: string;
};
export const initialViewerState = (): ViewerState => ({
	status: 'Preparando visualización',
	active: false,
	stale: false,
	result: null,
	sessionId: null,
	viewerId: null,
	frames: 0,
	windows: 0,
	error: ''
});
type Api = { request(path: string, options?: RequestOptions): Promise<unknown> };
export function object(value: unknown): Json {
	if (!value || typeof value !== 'object' || Array.isArray(value))
		throw new Error('Respuesta inválida del backend');
	return value as Json;
}
function text(value: unknown): string {
	if (typeof value !== 'string' || !value.trim())
		throw new Error('Campo de texto inválido en la respuesta');
	return value;
}
function count(value: unknown): number {
	if (typeof value !== 'number' || !Number.isInteger(value) || value < 0)
		throw new Error('Contador inválido');
	return value;
}
export function parseSource(value: unknown, sourceId: string, sessionId?: string) {
	const data = object(value);
	const id = text(data.session_id);
	if (id.length > 64 || data.source_id !== sourceId || (sessionId && id !== sessionId))
		throw new Error('La fuente o sesión no coincide');
	return { data, id, state: text(data.connection_state) };
}
export function parseResult(value: unknown, sourceId: string, sessionId: string): Result | null {
	if (value === null) return null;
	const data = object(value);
	if (data.source_id !== sourceId || data.session_id !== sessionId)
		throw new Error('La predicción pertenece a otra fuente o sesión');
	if (
		typeof data.confianza !== 'number' ||
		!Number.isFinite(data.confianza) ||
		data.confianza < 0 ||
		data.confianza > 1
	)
		throw new Error('Confianza inválida');
	if (
		typeof data.timestamp !== 'number' ||
		!Number.isFinite(data.timestamp) ||
		typeof data.simulated !== 'boolean'
	)
		throw new Error('Metadatos de predicción inválidos');
	return {
		clase: text(data.clase),
		confianza: data.confianza,
		simulated: data.simulated,
		window_id: text(data.window_id),
		timestamp: data.timestamp
	};
}
export function wait(ms: number, signal: AbortSignal): Promise<void> {
	return new Promise((resolve, reject) => {
		if (signal.aborted) {
			reject(signal.reason);
			return;
		}
		const done = () => {
			clearTimeout(timer);
			signal.removeEventListener('abort', abort);
			resolve();
		};
		const abort = () => {
			clearTimeout(timer);
			signal.removeEventListener('abort', abort);
			reject(signal.reason);
		};
		const timer = setTimeout(done, ms);
		signal.addEventListener('abort', abort, { once: true });
	});
}
export function gatherIce(
	pc: RTCPeerConnection,
	signal: AbortSignal,
	timeout = 15000
): Promise<void> {
	return new Promise((resolve, reject) => {
		const clean = () => {
			clearTimeout(timer);
			pc.removeEventListener('icegatheringstatechange', changed);
			signal.removeEventListener('abort', abort);
		};
		const changed = () => {
			if (pc.iceGatheringState === 'complete') {
				clean();
				resolve();
			}
		};
		const abort = () => {
			clean();
			reject(signal.reason);
		};
		const timer = setTimeout(() => {
			clean();
			reject(new Error('No se completó ICE en 15 segundos'));
		}, timeout);
		pc.addEventListener('icegatheringstatechange', changed);
		signal.addEventListener('abort', abort, { once: true });
		if (signal.aborted) abort();
		else changed();
	});
}
function message(error: unknown): string {
	if (error instanceof ApiError) {
		const body = error.body;
		const detail =
			body && typeof body === 'object' && 'detail' in body ? JSON.stringify(body.detail) : '';
		return `HTTP ${error.status}${detail ? ': ' + detail : ''}`;
	}
	return error instanceof Error ? error.message : 'No se pudo conectar con el backend';
}
class SourceGone extends Error {}

export function createViewer(options: {
	api: Api;
	video: HTMLVideoElement;
	sourceId: string;
	onState: (state: ViewerState) => void;
	onPlayBlocked: (blocked: boolean) => void;
	createPeer?: () => RTCPeerConnection;
}) {
	const { api, video, sourceId } = options;
	let state = initialViewerState();
	let generation = 0;
	let controller: AbortController | undefined;
	let task: Promise<void> = Promise.resolve();
	const publish = (patch: Partial<ViewerState>) => {
		state = { ...state, ...patch };
		options.onState(state);
	};
	async function closeViewer(id: string) {
		try {
			await api.request(`webrtc/viewers/${encodeURIComponent(id)}`, {
				method: 'DELETE',
				timeoutMs: 5000,
				keepalive: true
			});
		} catch (error) {
			if (!(error instanceof ApiError && error.status === 404))
				console.warn('No se pudo confirmar el cierre del espectador:', message(error));
		}
	}
	async function run(signal: AbortSignal, own: number) {
		const current = () => generation === own && !signal.aborted;
		while (current()) {
			let pc: RTCPeerConnection | undefined;
			let viewerId: string | undefined;
			let connectionTimer: ReturnType<typeof setTimeout> | undefined;
			const attempt = new AbortController();
			const cancel = () => attempt.abort(signal.reason);
			signal.addEventListener('abort', cancel, { once: true });
			let lost = false;
			let retry = false;
			const cleanupLocal = () => {
				if (connectionTimer) clearTimeout(connectionTimer);
				if (pc) {
					pc.ontrack = null;
					pc.onconnectionstatechange = null;
					pc.close();
				}
				if (generation === own) {
					video.srcObject = null;
					options.onPlayBlocked(false);
				}
			};
			attempt.signal.addEventListener('abort', cleanupLocal, { once: true });
			try {
				publish({
					active: true,
					status: 'Buscando cámara de Gabriel',
					error: '',
					result: null,
					stale: false,
					sessionId: null,
					viewerId: null,
					frames: 0,
					windows: 0
				});
				let sessionId = '';
				while (current() && !sessionId) {
					const list = await api.request('webrtc/sessions', {
						signal: attempt.signal,
						timeoutMs: 20000
					});
					if (!current()) return;
					if (!Array.isArray(list))
						throw new Error('La lista de fuentes no tiene el formato esperado');
					const found = list.find((item) => object(item).source_id === sourceId);
					if (found) {
						const source = parseSource(found, sourceId);
						if (source.state === 'connected') sessionId = source.id;
						else publish({ status: 'La cámara se está conectando' });
					} else publish({ status: 'Esperando cámara de Gabriel' });
					if (!sessionId) await wait(2000, attempt.signal);
				}
				if (!current()) return;
				publish({ status: 'Conectando video de Gabriel', sessionId });
				pc = options.createPeer?.() ?? new RTCPeerConnection({ iceServers: [] });
				const peer = pc;
				peer.addTransceiver('video', { direction: 'recvonly' });
				peer.ontrack = (event) => {
					if (!current() || attempt.signal.aborted) return;
					video.srcObject = event.streams[0] ?? new MediaStream([event.track]);
					void video
						.play()
						.then(() => {
							if (current()) options.onPlayBlocked(false);
						})
						.catch(() => {
							if (current()) options.onPlayBlocked(true);
						});
				};
				peer.onconnectionstatechange = () => {
					if (!current()) return;
					if (peer.connectionState === 'connected') {
						if (connectionTimer) clearTimeout(connectionTimer);
						publish({ status: 'Video conectado' });
					} else if (['failed', 'disconnected', 'closed'].includes(peer.connectionState)) {
						lost = true;
						attempt.abort(new SourceGone('Cámara desconectada'));
					}
				};
				await peer.setLocalDescription(await peer.createOffer());
				await gatherIce(peer, attempt.signal);
				if (!current()) return;
				const sdp = peer.localDescription?.sdp;
				if (!sdp || sdp.length > 100000) throw new Error('Oferta SDP inválida');
				// No abortar este POST: recuperar y cerrar un viewer_id que llegue después de detener.
				const raw = object(
					await api.request('webrtc/viewers/offer', {
						method: 'POST',
						json: { type: 'offer', sdp, session_id: sessionId },
						timeoutMs: 20000
					})
				);
				viewerId = text(raw.viewer_id);
				if (!current()) return;
				if (attempt.signal.aborted) throw attempt.signal.reason;
				if (raw.type !== 'answer' || raw.session_id !== sessionId || raw.source_id !== sourceId)
					throw new Error('La respuesta WebRTC no coincide con la visualización');
				publish({ viewerId });
				await peer.setRemoteDescription({ type: 'answer', sdp: text(raw.sdp) });
				if (!current()) return;
				if (peer.connectionState !== 'connected')
					connectionTimer = setTimeout(() => {
						attempt.abort(
							new Error(
								'No llegó el video en 30 segundos. Revisa Tailscale y el tráfico UDP del firewall.'
							)
						);
					}, 30000);
				while (current() && !attempt.signal.aborted) {
					try {
						const source = parseSource(
							await api.request(`webrtc/sessions/${encodeURIComponent(sessionId)}`, {
								signal: attempt.signal,
								timeoutMs: 20000
							}),
							sourceId,
							sessionId
						);
						if (!current() || attempt.signal.aborted) break;
						if (source.state !== 'connected') throw new SourceGone('Cámara desconectada');
						const viewer = object(
							await api.request(`webrtc/viewers/${encodeURIComponent(viewerId)}`, {
								signal: attempt.signal,
								timeoutMs: 20000
							})
						);
						if (!current() || attempt.signal.aborted) break;
						if (viewer.viewer_id !== viewerId || viewer.session_id !== sessionId)
							throw new Error('El estado recibido pertenece a otro espectador');
						const viewerConnection = text(viewer.connection_state);
						text(viewer.ice_state);
						if (['failed', 'closed', 'disconnected'].includes(viewerConnection))
							throw new SourceGone('Cámara desconectada');
						const result = parseResult(source.data.latest_result, sourceId, sessionId);
						const frames = count(source.data.frames_received);
						const windows = count(source.data.windows_processed);
						publish({
							frames,
							windows,
							stale: false,
							error: '',
							result: result?.window_id === state.result?.window_id ? state.result : result
						});
					} catch (error) {
						if (attempt.signal.aborted || !current()) throw error;
						if (error instanceof SourceGone || (error instanceof ApiError && error.status === 404))
							throw new SourceGone('Cámara desconectada');
						publish({ stale: true, error: 'Datos sin actualizar: ' + message(error) });
					}
					await wait(1000, attempt.signal);
				}
				if (attempt.signal.aborted && current()) throw attempt.signal.reason;
			} catch (error) {
				if (!current()) return;
				retry =
					lost ||
					error instanceof SourceGone ||
					(error instanceof ApiError && error.status === 404);
				publish({
					status: retry
						? 'Cámara desconectada; buscando la fuente de nuevo'
						: 'No se pudo iniciar la visualización',
					error: retry ? '' : message(attempt.signal.aborted ? attempt.signal.reason : error),
					result: null,
					sessionId: null,
					viewerId: null,
					frames: 0,
					windows: 0,
					stale: false,
					active: retry
				});
			} finally {
				signal.removeEventListener('abort', cancel);
				attempt.signal.removeEventListener('abort', cleanupLocal);
				cleanupLocal();
				if (viewerId) await closeViewer(viewerId);
			}
			if (!retry || !current()) return;
			try {
				await wait(2000, signal);
			} catch {
				return;
			}
		}
	}
	return {
		start() {
			controller?.abort();
			const own = ++generation;
			controller = new AbortController();
			const signal = controller.signal;
			publish({ ...initialViewerState(), active: true, status: 'Preparando visualización' });
			task = task
				.catch(() => {})
				.then(async () => {
					if (!signal.aborted && own === generation) await run(signal, own);
				});
			return task;
		},
		stop() {
			controller?.abort();
			++generation;
			video.srcObject = null;
			options.onPlayBlocked(false);
			publish({ ...initialViewerState(), status: 'Visualización detenida' });
			return task;
		}
	};
}
