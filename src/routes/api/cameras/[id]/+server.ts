import { error } from '@sveltejs/kit';
import { cameras } from '$lib/server/cameras';
import type { RequestHandler } from './$types';
export const DELETE: RequestHandler = async ({ request, url, params }) => {
	if (request.headers.get('origin') !== url.origin) error(403, 'Origen no permitido');
	let removed: boolean;
	try {
		removed = await cameras.remove(params.id);
	} catch {
		error(500, 'No se pudo eliminar la cámara. Inténtalo de nuevo.');
	}
	if (!removed) error(404, 'La cámara ya no está registrada.');
	return new Response(null, { status: 204 });
};
