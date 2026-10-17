import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { startRealFrontend } from '../../support/frontend-server.js';

let app;
before(async () => { app = await startRealFrontend(); });
after(async () => { await app?.close(); });

test('el frontend completa el flujo real de monitoreo de una cámara', async () => {
  const camerasResponse = await fetch(`${app.baseUrl}/api/camera`, { signal: AbortSignal.timeout(20_000) });
  const camerasPayload = await camerasResponse.json();
  assert.equal(camerasResponse.status, 200);
  const camera = camerasPayload.cameras?.[0];
  assert.ok(camera?.id, 'El frontend no recibió una cámara identificable');

  const inferenceResponse = await fetch(
    `${app.baseUrl}/api/inference?id=${encodeURIComponent(camera.id)}`,
    { signal: AbortSignal.timeout(30_000) }
  );
  const inference = await inferenceResponse.json();
  assert.equal(inferenceResponse.status, 200);
  assert.equal(inference.available, true, inference.error ?? 'La inferencia no está disponible');
  assert.ok(inference.source, inference.error ?? 'No se obtuvo el estado de la fuente');
});
