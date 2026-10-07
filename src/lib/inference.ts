export const classes = ['Normal', 'Hurto', 'Robo', 'Agresión física', 'Vandalismo'] as const;
const probabilityKeys: Record<(typeof classes)[number], string> = {
	Normal: 'normal',
	Hurto: 'hurto',
	Robo: 'robo',
	'Agresión física': 'agresion_fisica',
	Vandalismo: 'vandalismo'
};
export type Prediction = {
	clase: string;
	confianza: number;
	simulated: boolean;
	window_id: string;
	probabilities: Record<string, number> | null;
	alert: boolean | null;
};
export type Inference = {
	sessionId: string | null;
	status: string;
	result: Prediction | null;
	frames: number;
	windows: number;
	errors: number;
	frameAge: number | null;
	stale: boolean;
};
function object(value: unknown): Record<string, unknown> {
	if (!value || typeof value !== 'object' || Array.isArray(value))
		throw new Error('Objeto inválido');
	return value as Record<string, unknown>;
}
function number(value: unknown, max = Infinity) {
	if (typeof value !== 'number' || !Number.isFinite(value) || value < 0 || value > max)
		throw new Error('Número inválido');
	return value;
}
function count(value: unknown) {
	const n = number(value);
	if (!Number.isInteger(n)) throw new Error('Contador inválido');
	return n;
}
export function parseInference(
	value: unknown,
	room: string,
	source: string,
	participant = 'webcam-prueba'
): Inference {
	if (!Array.isArray(value)) throw new Error('Lista inválida');
	const matches = value
		.map(object)
		.filter(
			(s) =>
				s.source_id === source &&
				s.transport === 'livekit' &&
				s.room === room &&
				s.participant_identity === participant
		);
	const empty: Inference = {
		sessionId: null,
		status: 'Esperando al backend o al emisor',
		result: null,
		frames: 0,
		windows: 0,
		errors: 0,
		frameAge: null,
		stale: false
	};
	if (!matches.length) return empty;
	if (matches.length !== 1) throw new Error('Sesión ambigua');
	const s = matches[0];
	if (typeof s.session_id !== 'string' || !s.session_id || typeof s.connection_state !== 'string')
		throw new Error('Sesión inválida');
	const frameAge = s.last_frame_age_seconds === null ? null : number(s.last_frame_age_seconds);
	const info = {
		...empty,
		sessionId: s.session_id,
		frames: count(s.frames_received),
		windows: count(s.windows_processed),
		errors: count(s.processing_errors),
		frameAge,
		stale: frameAge === null || frameAge > 5
	};
	if (s.connection_state !== 'connected') return { ...info, status: 'Fuente no conectada' };
	if (s.latest_result === null) return { ...info, status: 'Esperando primera ventana' };
	const r = object(s.latest_result);
	if (
		r.source_id !== source ||
		r.session_id !== s.session_id ||
		typeof r.window_id !== 'string' ||
		!r.window_id ||
		typeof r.simulated !== 'boolean' ||
		!classes.includes(r.clase as (typeof classes)[number])
	)
		throw new Error('Predicción inválida');
	let probabilities: Record<string, number> | null = null;
	if (r.probabilities != null) {
		const p = object(r.probabilities);
		probabilities = Object.fromEntries(
			classes.map((c) => [c, number(p[probabilityKeys[c]] ?? p[c], 1)])
		);
	}
	let alert: boolean | null = null;
	if (r.alert != null) {
		const a = object(r.alert);
		if (typeof a.active !== 'boolean') throw new Error('Alerta inválida');
		alert = a.active;
	}
	return {
		...info,
		status: 'Resultado recibido',
		result: {
			clase: r.clase as string,
			confianza: number(r.confianza, 1),
			simulated: r.simulated,
			window_id: r.window_id,
			probabilities,
			alert
		}
	};
}
