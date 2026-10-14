import { CAMERA_PLAYBACK_URL } from '$app/env/private';
import { callInference } from './inference-api.js';

/**
 * @typedef {{ id: string, number: number, name: string, url: string, streamPath: string, registeredAt: string, enabled: boolean }} Camera
 */

/** @param {any} payload */
function cameraRows(payload) {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.cameras)) return payload.cameras;
  if (Array.isArray(payload?.items)) return payload.items;
  return [];
}

/** @param {unknown} value */
function numericSuffix(value) {
  const number = Number(String(value ?? '').match(/(?:camara|camera)(\d+)$/i)?.[1]);
  return Number.isInteger(number) ? number : 0;
}

/** @param {any} row @returns {Camera} */
export function mapCamera(row) {
  const id = String(row?.source_id ?? row?.id ?? '').trim();
  const streamPath = String(row?.stream_path ?? row?.streamPath ?? id).trim();
  const configuredUrl = String(CAMERA_PLAYBACK_URL ?? '').trim();
  let playbackUrl = String(row?.url ?? row?.playback_url ?? '').trim();

  if (!playbackUrl && configuredUrl) {
    try {
      const parsed = new URL(configuredUrl);
      const segments = parsed.pathname.split('/').filter(Boolean);
      if (segments.length) segments[segments.length - 1] = streamPath;
      else segments.push(streamPath);
      parsed.pathname = `/${segments.join('/')}/`;
      playbackUrl = parsed.toString();
    } catch {
      playbackUrl = configuredUrl;
    }
  }

  return {
    id,
    number: Number(row?.number) || numericSuffix(id),
    name: String(row?.name ?? row?.nombre ?? id),
    url: playbackUrl,
    streamPath,
    registeredAt: String(row?.registered_at ?? row?.registeredAt ?? row?.created_at ?? row?.fecha_hora ?? ''),
    enabled: row?.enabled !== false
  };
}

/** @param {{ includeDisabled?: boolean }} [options] */
export async function getRegisteredCameras({ includeDisabled = false } = {}) {
  const cameras = /** @type {Camera[]} */ (cameraRows(await callInference('/cameras')).map(mapCamera));
  return cameras.filter((camera) => camera.id && (includeDisabled || camera.enabled));
}

/** @param {unknown} id */
export async function getRegisteredCamera(id) {
  const wanted = String(id ?? '').trim();
  if (!wanted) return null;
  return (await getRegisteredCameras()).find((camera) => camera.id === wanted) ?? null;
}
