import assert from 'node:assert/strict';

export const apiUrl = String(process.env.INFERENCE_API_URL ?? '').replace(/\/$/, '');
const apiKey = String(process.env.INFERENCE_API_KEY ?? '');

export function requireRealApiConfiguration() {
  assert.ok(apiUrl, 'Falta INFERENCE_API_URL en .env');
  assert.ok(apiKey, 'Falta INFERENCE_API_KEY en .env');
}

export async function realApi(path) {
  requireRealApiConfiguration();
  const response = await fetch(`${apiUrl}${path}`, {
    headers: { 'X-API-Key': apiKey },
    signal: AbortSignal.timeout(15_000)
  });
  const payload = await response.json().catch(() => ({}));
  return { response, payload };
}

export function cameraRows(payload) {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.cameras)) return payload.cameras;
  if (Array.isArray(payload?.items)) return payload.items;
  return [];
}

export async function activeCamera() {
  const { response, payload } = await realApi('/cameras');
  assert.equal(response.status, 200, 'FastAPI no permitió consultar /cameras');
  return cameraRows(payload).find((camera) => camera.enabled !== false) ?? null;
}
