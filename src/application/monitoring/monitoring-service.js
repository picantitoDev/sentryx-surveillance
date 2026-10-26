export class MonitoringUnavailableError extends Error {}

/**
 * @param {{
 *   inferenceClient: (path: string, options?: { method?: string, body?: unknown }) => Promise<any>,
 *   cameraRegistry: { list: () => Promise<any[]>, get: (id: unknown) => Promise<any> }
 * }} dependencies
 */
export function createMonitoringService({ inferenceClient, cameraRegistry }) {
  return {
    /** @param {string | null} requestedId */
    async getSnapshot(requestedId) {
      let health;
      try {
        health = await inferenceClient('/health');
      } catch (error) {
        throw new MonitoringUnavailableError(error instanceof Error ? error.message : String(error));
      }

      let camera;
      try {
        camera = requestedId ? await cameraRegistry.get(requestedId) : (await cameraRegistry.list())[0] ?? null;
      } catch (error) {
        return {
          available: health?.status === 'ok', health, source: null, latestDetection: null,
          alerts: [], detections: [], error: error instanceof Error ? error.message : String(error)
        };
      }
      if (!camera) {
        return {
          available: health?.status === 'ok', health, source: null,
          latestDetection: null, alerts: [], detections: []
        };
      }

      try {
        const [source, detections, alertsPage] = await Promise.all([
          inferenceClient(`/sources/${encodeURIComponent(camera.id)}/status`),
          inferenceClient(`/detections?camera=${encodeURIComponent(camera.id)}&limit=500&offset=0`),
          inferenceClient(`/alerts?camera=${encodeURIComponent(camera.id)}&limit=30`)
        ]);
        const registeredTime = new Date(camera.registeredAt).getTime();
        /** @param {{ fecha_hora: string }} row */
        const isRecent = (row) => !Number.isFinite(registeredTime) || new Date(row.fecha_hora).getTime() >= registeredTime;
        return {
          available: health?.status === 'ok',
          health,
          source,
          detections: detections.filter(isRecent),
          latestDetection: detections.find(isRecent) ?? null,
          alerts: (alertsPage.items ?? []).filter(isRecent)
        };
      } catch (error) {
        return {
          available: health?.status === 'ok', health, source: null, latestDetection: null,
          alerts: [], detections: [], error: error instanceof Error ? error.message : String(error)
        };
      }
    }
  };
}
