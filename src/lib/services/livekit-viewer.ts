import {
	Room,
	RoomEvent,
	Track,
	type RemoteParticipant,
	type RemoteTrackPublication,
	type RemoteTrack
} from 'livekit-client';
import type { Camera } from '$lib/camera';
import type { Prediction as Result } from '$lib/inference';
import { wait } from './viewer';
export type MonitorState = {
	active: boolean;
	status: string;
	error: string;
	inferenceStatus: string;
	stale: boolean;
	result: Result | null;
	sessionId: string | null;
	frames: number;
	windows: number;
};
export const initialMonitorState = (): MonitorState => ({
	active: false,
	status: 'Cámara registrada. Pulsa Conectar.',
	error: '',
	inferenceStatus: 'Sin consultar',
	stale: false,
	result: null,
	sessionId: null,
	frames: 0,
	windows: 0
});
export function createLivekitViewer(
	camera: Camera,
	video: HTMLVideoElement,
	changed: (state: MonitorState) => void,
	playBlocked: (value: boolean) => void
) {
	let room: Room | undefined;
	let abort: AbortController | undefined;
	let generation = 0;
	let attached: RemoteTrack | undefined;
	let state = initialMonitorState();
	let errors = 0;
	let lastWindow = 0;
	const publish = (patch: Partial<MonitorState>) => {
		state = { ...state, ...patch };
		changed(state);
	};
	function clearVideo() {
		attached?.detach(video);
		attached = undefined;
		video.srcObject = null;
		playBlocked(false);
	}
	function stop() {
		++generation;
		errors = 0;
		lastWindow = 0;
		abort?.abort();
		clearVideo();
		const old = room;
		room = undefined;
		old?.removeAllListeners();
		void old?.disconnect();
		publish({ ...initialMonitorState(), status: 'Visualización detenida' });
	}
	async function poll(signal: AbortSignal, own: number) {
		while (!signal.aborted && own === generation) {
			try {
				const response = await fetch(`/api/cameras/${encodeURIComponent(camera.id)}/inference`, {
					signal: AbortSignal.any([signal, AbortSignal.timeout(25000)])
				});
				const data = await response.json();
				if (response.status === 401) {
					const renewal = await fetch(`/api/cameras/${encodeURIComponent(camera.id)}/connection`, {
						method: 'POST',
						signal: AbortSignal.any([signal, AbortSignal.timeout(15000)])
					});
					if (!renewal.ok) throw new Error('No se pudo renovar el acceso. Vuelve a conectar.');
					await wait(1000, signal);
					continue;
				}
				if (!response.ok) throw new Error(data.message || 'Error en la API de inferencia');
				if (signal.aborted || own !== generation) return;
				const newSession = data.sessionId !== state.sessionId;
				const newWindow = newSession || data.result?.window_id !== state.result?.window_id;
				const failed = !newSession && data.errors > errors;
				errors = data.errors;
				if (newWindow) lastWindow = Date.now();
				const stale =
					data.stale ||
					failed ||
					(!newWindow && state.stale && !!data.result) ||
					(!!data.result && Date.now() - lastWindow > 15000);
				publish({
					result:
						data.sessionId === state.sessionId && data.result?.window_id === state.result?.window_id
							? state.result
							: data.result,
					sessionId: data.sessionId,
					frames: data.frames,
					windows: data.windows,
					stale,
					inferenceStatus: stale ? 'Resultado anterior / datos sin actualizar' : data.status
				});
			} catch (e) {
				if (signal.aborted || own !== generation) return;
				publish({
					stale: true,
					inferenceStatus: e instanceof Error ? e.message : 'Datos sin actualizar'
				});
			}
			try {
				await wait(1000, signal);
			} catch {
				return;
			}
		}
	}
	async function start() {
		stop();
		const own = ++generation;
		abort = new AbortController();
		const signal = abort.signal;
		const current = () => own === generation && !signal.aborted;
		publish({ active: true, status: 'Conectando a LiveKit', error: '' });
		try {
			const response = await fetch(`/api/cameras/${encodeURIComponent(camera.id)}/connection`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({}),
				signal: AbortSignal.any([signal, AbortSignal.timeout(15000)])
			});
			const data = await response.json();
			if (!response.ok) throw new Error(data.message || 'No se pudo obtener acceso a LiveKit');
			if (!current()) return;
			if (typeof data.serverUrl !== 'string' || typeof data.token !== 'string')
				throw new Error('Respuesta de conexión inválida');
			const connection = new Room();
			room = connection;
			const subscribe = (publication: RemoteTrackPublication, participant: RemoteParticipant) => {
				if (
					current() &&
					participant.identity === camera.participantIdentity &&
					publication.kind === Track.Kind.Video
				)
					publication.setSubscribed(true);
			};
			connection.on(RoomEvent.TrackPublished, subscribe);
			connection.on(RoomEvent.ParticipantConnected, (participant) => {
				participant.trackPublications.forEach((pub) => subscribe(pub, participant));
			});
			connection.on(RoomEvent.TrackSubscribed, (track, _publication, participant) => {
				if (
					!current() ||
					participant.identity !== camera.participantIdentity ||
					track.kind !== Track.Kind.Video
				)
					return;
				clearVideo();
				attached = track;
				track.attach(video);
				publish({ status: 'Video conectado' });
				void video
					.play()
					.then(() => {
						if (current()) playBlocked(false);
					})
					.catch(() => {
						if (current()) playBlocked(true);
					});
			});
			connection.on(RoomEvent.TrackUnsubscribed, (track) => {
				if (current() && track === attached) {
					clearVideo();
					publish({ status: 'Esperando video de la cámara' });
				}
			});
			connection.on(RoomEvent.ParticipantDisconnected, (participant) => {
				if (current() && participant.identity === camera.participantIdentity) {
					clearVideo();
					publish({
						status: 'Cámara desconectada; esperando al emisor',
						result: null,
						stale: true
					});
				}
			});
			connection.on(RoomEvent.Reconnecting, () => {
				if (current()) publish({ status: 'Reconectando video' });
			});
			connection.on(RoomEvent.Reconnected, () => {
				if (current())
					publish({ status: attached ? 'Video conectado' : 'Esperando video de la cámara' });
			});
			connection.on(RoomEvent.Disconnected, () => {
				if (current()) {
					stop();
					publish({ status: 'Conexión cerrada. Puedes volver a conectar.' });
				}
			});
			await connection.connect(data.serverUrl, data.token, { autoSubscribe: false });
			if (!current()) {
				await connection.disconnect();
				return;
			}
			if (!attached) publish({ status: 'Conectado a LiveKit; esperando video de la cámara' });
			connection.remoteParticipants.forEach((participant) =>
				participant.trackPublications.forEach((pub) => subscribe(pub, participant))
			);
			void poll(signal, own);
		} catch (e) {
			if (!current()) return;
			stop();
			publish({
				status: 'No conectado',
				error: e instanceof Error ? e.message : 'No se pudo conectar'
			});
		}
	}
	return { start, stop };
}
