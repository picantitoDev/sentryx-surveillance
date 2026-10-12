import { json } from '@sveltejs/kit';
import { callInference } from '#lib/server/inference-api.js';
import { getRegisteredCamera } from '#lib/server/camera-registry.js';

export async function POST({ request }) {
  const body = await request.json().catch(() => ({}));
  const camera = await getRegisteredCamera(body.id);
  if (!camera) {
    return json({ error: 'Primero registra una camara.' }, { status: 404 });
  }
  try {
    const source = await callInference('/sources/start', {
      method: 'POST', body: { source_id: camera.id, stream_path: camera.streamPath ?? camera.id }
    });
    return json({ source });
  } catch (error) {
    return json({ error: error instanceof Error ? error.message : String(error) }, { status: 502 });
  }
}
