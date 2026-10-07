import { error, json } from '@sveltejs/kit';
import { cameras } from '$lib/server/cameras';
import { env } from '$env/dynamic/private';
import { cookieName, validAccess } from '$lib/server/access';
import { parseInference } from '$lib/inference';
import type { RequestHandler } from './$types';
export const GET: RequestHandler = async ({ cookies, request, params }) => {
	if (!validAccess(cookies.get(cookieName)))
		error(401, 'Acceso vencido. Vuelve a conectar la visualización.');
	const camera = await cameras.get(params.id);
	if (!camera) error(404, 'Cámara no encontrada');
	if (!env.INFERENCE_API_BASE_URL) error(503, 'Falta configurar la API de inferencia');
	let url: URL;
	try {
		url = new URL(env.INFERENCE_API_BASE_URL.replace(/\/$/, '') + '/webrtc/sessions');
		if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password)
			throw new Error();
	} catch {
		error(503, 'Configuración de API inválida');
	}
	try {
		const response = await fetch(url, {
			signal: AbortSignal.any([request.signal, AbortSignal.timeout(5000)]),
			redirect: 'error'
		});
		if (!response.ok) throw new Error();
		const data = parseInference(
			await response.json(),
			camera.room,
			camera.sourceId,
			camera.participantIdentity
		);
		return json(data, { headers: { 'cache-control': 'no-store' } });
	} catch {
		error(502, 'No se pudo actualizar la inferencia: backend inaccesible o respuesta inválida.');
	}
};
