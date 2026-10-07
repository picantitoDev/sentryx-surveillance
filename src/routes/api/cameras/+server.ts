import { json, error } from '@sveltejs/kit';
import { cameras } from '$lib/server/cameras';
import { validateCamera } from '$lib/camera';
import type { RequestHandler } from './$types';
export const GET: RequestHandler = async () =>
	json(await cameras.list(), { headers: { 'cache-control': 'no-store' } });
export const POST: RequestHandler = async ({ request, url }) => {
	if (request.headers.get('origin') !== url.origin) error(403, 'Origen no permitido');
	const body = await request.text();
	if (body.length > 4096) error(413, 'Registro demasiado grande');
	let input;
	try {
		input = validateCamera(JSON.parse(body));
	} catch (e) {
		error(400, e instanceof Error ? e.message : 'Registro inválido');
	}
	try {
		return json(await cameras.add(input), { status: 201 });
	} catch (e) {
		if (e instanceof Error && e.message.includes('ya está registrado')) error(409, e.message);
		console.error('No se pudo guardar la cámara', e);
		error(500, 'No se pudo guardar la cámara');
	}
};
