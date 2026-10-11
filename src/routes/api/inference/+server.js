import { json } from '@sveltejs/kit';
import { getCamera } from '#lib/server/camera-store.js';
import { callInference } from '#lib/server/inference-api.js';

export async function GET({ url }) {
  const camera = await getCamera(url.searchParams.get('id'));
  if (!camera) {
    return json({ available: false, source: null, latestDetection: null, alerts: [], detections: [] });
  }
  try {
    const [source, detections, alertsPage] = await Promise.all([
      callInference(`/sources/${encodeURIComponent(camera.id)}/status`),
      callInference(`/detections?camera=${encodeURIComponent(camera.id)}&limit=10`),
      callInference(`/alerts?camera=${encodeURIComponent(camera.id)}&limit=30`)
    ]);
    const registeredTime = new Date(camera.registeredAt).getTime();
    /** @param {{ fecha_hora: string }} row */
    const isRecent = (row) => new Date(row.fecha_hora).getTime() >= registeredTime;
    return json({
      available: true,
      source,
      detections: detections.filter(isRecent),
      latestDetection: detections.find(isRecent) ?? null,
      alerts: (alertsPage.items ?? []).filter(isRecent)
    });
  } catch (error) {
    return json({
      available: false, source: null, latestDetection: null,
      alerts: [], detections: [], error: error instanceof Error ? error.message : String(error)
    }, { status: 502 });
  }
}
