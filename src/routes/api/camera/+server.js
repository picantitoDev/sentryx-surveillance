import { json } from '@sveltejs/kit';
import { callInference } from '#lib/server/inference-api.js';
import { getRegisteredCameras, mapCamera } from '#lib/server/camera-registry.js';

/** @param {unknown} value */
function normalizeUrl(value) {
  return String(value ?? '').trim().replace(/\/+$/, '');
}

/** @param {unknown} value */
function cameraIdFromUrl(value) {
  try {
    const parsed = new URL(String(value ?? ''));
    if (parsed.protocol !== 'https:') return '';
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
    return json({ error: 'Ingresa una URL HTTPS válida cuyo último segmento sea el identificador de la cámara.' }, { status: 400 });
  }

  try {
    const cameras = await getRegisteredCameras({ includeDisabled: true });
    const activeNumbers = new Set(cameras.filter((item) => item.enabled).map((item) => {
      const savedNumber = Number(item.number);
      const idNumber = Number(String(item.id).match(/^camara(\d+)$/)?.[1]);
      return Number.isInteger(savedNumber) && savedNumber > 0 ? savedNumber : idNumber;
    }).filter((value) => Number.isInteger(value) && value > 0));
    let number = 1;
    while (activeNumbers.has(number)) number += 1;
    const id = `camara${number}`;
    const existingDisabledCamera = cameras.find((item) => item.id === id && !item.enabled);
    const registrationBody = { source_id: id, name, stream_path: streamPath, enabled: true };

    const saved = existingDisabledCamera
      ? await callInference(`/cameras/${encodeURIComponent(id)}`, { method: 'PUT', body: registrationBody })
      : await callInference('/cameras', { method: 'POST', body: registrationBody });
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
    const camera = await getRegisteredCameras({ includeDisabled: true }).then((items) => items.find((item) => item.id === id) ?? null);
    if (!camera) return json({ removed: true });

    let warning = null;
    try {
      await callInference(`/sources/${encodeURIComponent(id)}`, { method: 'DELETE' });
    } catch (error) {
      console.error(`[SentriX] No se pudo detener la inferencia para ${id}:`, error);
      // La detención de la fuente no debe impedir eliminar el registro,
      // incluso cuando se trata de la última cámara del inventario.
      warning = 'El registro se eliminó, pero no fue posible confirmar la detención de la inferencia.';
    }

    // FastAPI protege su cámara predeterminada con HTTP 409. En ese único caso se
    // aplica una baja lógica y el registro deja de formar parte del inventario.
    try {
      await callInference(`/cameras/${encodeURIComponent(id)}`, { method: 'DELETE' });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      if (!message.toLowerCase().includes('predeterminada')) throw error;
      await callInference(`/cameras/${encodeURIComponent(id)}`, {
        method: 'PUT',
        body: { source_id: id, name: camera.name, stream_path: camera.streamPath, enabled: false }
      });
    }
    return json({ removed: true, inferenceStopped: warning === null, warning });
  } catch (error) {
    console.error('[SentriX] Error al eliminar cámara:', error);
    return json({ error: 'No se pudo eliminar la cámara.' }, { status: 500 });
  }
}
