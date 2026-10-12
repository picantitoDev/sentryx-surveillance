import { json } from '@sveltejs/kit';
import { callInference } from '#lib/server/inference-api.js';
import { getRegisteredCamera, getRegisteredCameras, mapCamera } from '#lib/server/camera-registry.js';

/** @param {unknown} value */
function normalizeUrl(value) {
  return String(value ?? '').trim().replace(/\/+$/, '');
}

/** @param {unknown} value */
function cameraIdFromUrl(value) {
  try {
    const parsed = new URL(String(value ?? ''));
    if (!['http:', 'https:'].includes(parsed.protocol)) return '';
    return parsed.pathname.split('/').filter(Boolean).at(-1)?.toLowerCase() ?? '';
  } catch {
    return '';
  }
}

export async function GET() {
  try {
    return json({ cameras: await getRegisteredCameras() });
  } catch (error) {
    console.error('Error al obtener las cámaras:', error);
    return json({ error: 'No se pudieron consultar las cámaras.' }, { status: 500 });
  }
}

export async function POST({ request }) {
  const body = await request.json().catch(() => null);
  const name = typeof body?.name === 'string' ? body.name.trim() : '';
  const normalizedUrl = normalizeUrl(body?.url);
  const streamPath = cameraIdFromUrl(normalizedUrl);

  if (!name || name.length > 100) {
    return json({ error: 'El nombre es obligatorio y debe tener como máximo 100 caracteres.' }, { status: 400 });
  }
  if (!normalizedUrl || !streamPath || !/^[a-z0-9_-]{1,64}$/.test(streamPath)) {
    return json({ error: 'Ingresa una URL HTTP(S) válida cuyo último segmento sea el identificador de la cámara.' }, { status: 400 });
  }

  try {
    const cameras = await getRegisteredCameras();
    const number = cameras.reduce((highest, item) => {
      const savedNumber = Number(item.number);
      const idNumber = Number(String(item.id).match(/^camara(\d+)$/)?.[1]);
      return Math.max(highest, Number.isInteger(savedNumber) ? savedNumber : 0, Number.isInteger(idNumber) ? idNumber : 0);
    }, 0) + 1;
    const id = `camara${number}`;

    const saved = await callInference('/cameras', {
      method: 'POST',
      body: { source_id: id, name, stream_path: streamPath }
    });
    const camera = {
      ...mapCamera(saved?.camera ?? saved),
      id,
      number,
      name,
      url: `${normalizedUrl}/`,
      streamPath,
      registeredAt: String(saved?.registered_at ?? saved?.created_at ?? new Date().toISOString())
    };

    try {
      const source = await callInference('/sources/start', {
        method: 'POST', body: { source_id: id, stream_path: streamPath }
      });
      return json({ camera, inference: { available: true, source } }, { status: 201 });
    } catch (error) {
      console.error(`[SentriX] No se pudo iniciar la inferencia para ${id}:`, error);
      return json({ camera, inference: { available: false, error: error instanceof Error ? error.message : String(error) } }, { status: 201 });
    }
  } catch (error) {
    console.error('[SentriX] Error al registrar cámara:', error);
    return json({ error: 'No se pudo registrar la cámara.' }, { status: 500 });
  }
}

export async function DELETE({ url }) {
  const id = url.searchParams.get('id')?.trim();
  if (!id) return json({ error: 'Falta el identificador de la cámara.' }, { status: 400 });

  try {
    const camera = await getRegisteredCamera(id);
    if (!camera) return json({ removed: true });

    let warning = null;
    try {
      await callInference(`/sources/${encodeURIComponent(id)}`, { method: 'DELETE' });
    } catch (error) {
      console.error(`[SentriX] No se pudo detener la inferencia para ${id}:`, error);
      // La inferencia es un servicio externo: una caída o una fuente inexistente
      // no debe impedir que el operador retire el registro local.
      warning = 'El registro se eliminó, pero no fue posible confirmar la detención de la inferencia.';
    }

    await callInference(`/cameras/${encodeURIComponent(id)}`, { method: 'DELETE' });
    return json({ removed: true, inferenceStopped: warning === null, warning });
  } catch (error) {
    console.error('[SentriX] Error al eliminar cámara:', error);
    return json({ error: 'No se pudo eliminar la cámara.' }, { status: 500 });
  }
}
