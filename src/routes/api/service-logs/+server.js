import { json } from '@sveltejs/kit';
import { callInference } from '#lib/server/inference-api.js';

export async function GET({ url }) {
  const params = new URLSearchParams();
  const camera = url.searchParams.get('camera')?.trim();
  const level = url.searchParams.get('level')?.trim();
  const eventType = url.searchParams.get('event_type')?.trim();
  const limit = Math.min(Math.max(Number(url.searchParams.get('limit')) || 100, 1), 500);

  if (camera) params.set('camera', camera);
  if (level) params.set('level', level);
  if (eventType) params.set('event_type', eventType);
  params.set('limit', String(limit));

  try {
    const payload = await callInference(`/inference-logs?${params.toString()}`);
    const logs = Array.isArray(payload)
      ? payload
      : Array.isArray(payload?.items)
        ? payload.items
        : Array.isArray(payload?.logs)
          ? payload.logs
          : [];

    return json({ logs, total: payload?.total ?? logs.length });
  } catch (error) {
    return json({ logs: [], total: 0, error: error instanceof Error ? error.message : String(error) }, { status: 502 });
  }
}
