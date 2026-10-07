import { json, error } from '@sveltejs/kit';
import { cameras } from '$lib/server/cameras';
import { env } from '$env/dynamic/private';

import { AccessToken } from 'livekit-server-sdk';
import { randomUUID } from 'node:crypto';
import type { RequestHandler } from './$types';
import { cookieName, issueAccess } from '$lib/server/access';

// Global bounded limiter for this single-process, private-tailnet deployment.
let attempts = 0;
let resetAt = 0;
export const POST: RequestHandler = async ({ request, url, cookies, params }) => {
	if (request.headers.get('origin') !== url.origin) error(403, 'Origen no permitido');
	// This viewer endpoint is restricted to the configured private deployment origin.
	if (env.HOST !== '127.0.0.1' || !env.ORIGIN || url.origin !== env.ORIGIN)
		error(403, 'Usa la dirección configurada de SentriX mediante Tailscale.');
	if (Date.now() > resetAt) {
		attempts = 0;
		resetAt = Date.now() + 60000;
	}
	if (++attempts > 20) error(429, 'Demasiados intentos. Espera un minuto.');
	const camera = await cameras.get(params.id);
	if (!camera) error(404, 'Cámara no encontrada');
	if (!env.LIVEKIT_URL || !env.LIVEKIT_API_KEY || !env.LIVEKIT_API_SECRET)
		error(503, 'Falta configurar LiveKit en el servidor');
	let serverUrl: URL;
	try {
		serverUrl = new URL(env.LIVEKIT_URL);
	} catch {
		error(503, 'URL de LiveKit inválida');
	}
	if (!['ws:', 'wss:'].includes(serverUrl.protocol)) error(503, 'LiveKit requiere ws:// o wss://');
	const token = new AccessToken(env.LIVEKIT_API_KEY, env.LIVEKIT_API_SECRET, {
		identity: `viewer-${randomUUID()}`,
		ttl: '10m'
	});
	token.addGrant({
		room: camera.room,
		roomJoin: true,
		canPublish: false,
		canPublishSources: [],
		canSubscribe: true,
		canPublishData: false
	});
	const jwt = await token.toJwt();
	cookies.set(cookieName, issueAccess(), {
		path: '/api',
		httpOnly: true,
		sameSite: 'strict',
		secure: url.protocol === 'https:',
		maxAge: 7200
	});
	return json(
		{ serverUrl: serverUrl.toString(), token: jwt },
		{ headers: { 'cache-control': 'no-store' } }
	);
};
