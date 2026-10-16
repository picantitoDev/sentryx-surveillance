import { nextAvailableCameraNumber } from '#domain/camera/camera.js';

export class CameraNotFoundError extends Error {}

/**
 * @param {{
 *   inferenceClient: (path: string, options?: { method?: string, body?: unknown }) => Promise<any>,
 *   cameraRegistry: {
 *     list: (options?: { includeDisabled?: boolean }) => Promise<any[]>,
 *     get: (id: unknown) => Promise<any>,
 *     map: (row: any) => any
 *   }
 * }} dependencies
 */
export function createCameraService({ inferenceClient, cameraRegistry }) {
  return {
    async list() {
      return cameraRegistry.list();
    },

    /** @param {{ name: string, normalizedUrl: string, streamPath: string }} input */
    async register({ name, normalizedUrl, streamPath }) {
      const cameras = await cameraRegistry.list({ includeDisabled: true });
      const number = nextAvailableCameraNumber(cameras);
      const id = `camara${number}`;
      const existingDisabledCamera = cameras.find((item) => item.id === id && !item.enabled);
      const registrationBody = { source_id: id, name, stream_path: streamPath, enabled: true };

      const saved = existingDisabledCamera
        ? await inferenceClient(`/cameras/${encodeURIComponent(id)}`, { method: 'PUT', body: registrationBody })
        : await inferenceClient('/cameras', { method: 'POST', body: registrationBody });
      const camera = {
        ...cameraRegistry.map(saved?.camera ?? saved),
        id,
        number,
        name,
        url: `${normalizedUrl}/`,
        streamPath,
        registeredAt: String(saved?.registered_at ?? saved?.created_at ?? new Date().toISOString())
      };

      try {
        const source = await inferenceClient('/sources/start', {
          method: 'POST', body: { source_id: id, stream_path: streamPath }
        });
        return { camera, inference: { available: true, source } };
      } catch (error) {
        console.error(`[SentriX] No se pudo iniciar la inferencia para ${id}:`, error);
        return { camera, inference: { available: false, error: error instanceof Error ? error.message : String(error) } };
      }
    },

    /** @param {unknown} id */
    async start(id) {
      const camera = await cameraRegistry.get(id);
      if (!camera) throw new CameraNotFoundError('Primero registra una camara.');
      return inferenceClient('/sources/start', {
        method: 'POST', body: { source_id: camera.id, stream_path: camera.streamPath ?? camera.id }
      });
    },

    /** @param {string} id */
    async remove(id) {
      const camera = await cameraRegistry.list({ includeDisabled: true }).then((items) => items.find((item) => item.id === id) ?? null);
      if (!camera) return { removed: true };

      let warning = null;
      try {
        await inferenceClient(`/sources/${encodeURIComponent(id)}`, { method: 'DELETE' });
      } catch (error) {
        console.error(`[SentriX] No se pudo detener la inferencia para ${id}:`, error);
        warning = 'El registro se eliminó, pero no fue posible confirmar la detención de la inferencia.';
      }

      try {
        await inferenceClient(`/cameras/${encodeURIComponent(id)}`, { method: 'DELETE' });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        if (!message.toLowerCase().includes('predeterminada')) throw error;
        await inferenceClient(`/cameras/${encodeURIComponent(id)}`, {
          method: 'PUT',
          body: { source_id: id, name: camera.name, stream_path: camera.streamPath, enabled: false }
        });
      }
      return { removed: true, inferenceStopped: warning === null, warning };
    }
  };
}
