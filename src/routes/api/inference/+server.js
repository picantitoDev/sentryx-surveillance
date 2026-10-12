import { json } from '@sveltejs/kit';
import { callInference } from '#lib/server/inference-api.js';
import { getRegisteredCamera, getRegisteredCameras } from '#lib/server/camera-registry.js';

export async function GET({ url }) {
  let health;
  try {
    health = await callInference('/health');
  } catch (error) {
    return json({
      available: false, health: null, source: null, latestDetection: null,
      alerts: [], detections: [], error: error instanceof Error ? error.message : String(error)
    }, { status: 502 });
  }

  const requestedId = url.searchParams.get('id');
  let camera;
  try {
    camera = requestedId
      ? await getRegisteredCamera(requestedId)
      : (await getRegisteredCameras())[0] ?? null;
  } catch (error) {
    return json({
      available: health?.status === 'ok', health, source: null, latestDetection: null,
      alerts: [], detections: [], error: error instanceof Error ? error.message : String(error)
    });
  }
  if (!camera) {
    return json({
      available: health?.status === 'ok', health, source: null,
      latestDetection: null, alerts: [], detections: []
    });
  }

  try {
    const [source, detections, alertsPage] = await Promise.all([
      callInference(`/sources/${encodeURIComponent(camera.id)}/status`),
      callInference(`/detections?camera=${encodeURIComponent(camera.id)}&limit=10`),
      callInference(`/alerts?camera=${encodeURIComponent(camera.id)}&limit=30`)
    ]);
    const registeredTime = new Date(camera.registeredAt).getTime();
    /** @param {{ fecha_hora: string }} row */
    const isRecent = (row) => !Number.isFinite(registeredTime) || new Date(row.fecha_hora).getTime() >= registeredTime;
    return json({
      available: health?.status === 'ok',
      health,
      source,
      detections: detections.filter(isRecent),
      latestDetection: detections.find(isRecent) ?? null,
      alerts: (alertsPage.items ?? []).filter(isRecent)
    });
  } catch (error) {
    return json({
      available: health?.status === 'ok', health, source: null, latestDetection: null,
      alerts: [], detections: [], error: error instanceof Error ? error.message : String(error)
    });
  }
}
