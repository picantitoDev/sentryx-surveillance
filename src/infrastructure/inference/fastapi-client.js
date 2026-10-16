// SOLO servidor. Nunca importar este modulo en +page.svelte.
import { INFERENCE_API_URL, INFERENCE_API_KEY } from '$app/env/private';

/**
 * @param {string} path
 * @param {{ method?: string, body?: unknown }} [options]
 * @returns {Promise<any>}
 */
export async function callInference(path, { method = 'GET', body } = {}) {
  const base = INFERENCE_API_URL?.replace(/\/$/, '');
  const key = INFERENCE_API_KEY;
  if (!base || !key) {
    throw new Error('Configura INFERENCE_API_URL e INFERENCE_API_KEY en .env');
  }
  const response = await fetch(`${base}${path}`, {
    method,
    headers: {
      'X-API-Key': key,
      ...(body === undefined ? {} : { 'Content-Type': 'application/json' })
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    signal: AbortSignal.timeout(6000)
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = typeof data.detail === 'string' ? data.detail : `HTTP ${response.status}`;
    throw new Error(`FastAPI: ${message}`);
  }
  return data;
}
