import assert from 'node:assert/strict';
import test from 'node:test';
import { activeCamera, realApi } from '../../support/real-api.js';

test('FastAPI permite consultar el estado de la fuente activa', async () => {
  const camera = await activeCamera();
  assert.ok(camera, 'No se encontró una cámara activa');
  const id = camera.source_id ?? camera.id;
  const { response, payload } = await realApi(`/sources/${encodeURIComponent(id)}/status`);
  assert.equal(response.status, 200);
  assert.ok(payload.status, 'El estado de la fuente no está presente');
});

test('FastAPI permite consultar detecciones sin modificar datos', async () => {
  const camera = await activeCamera();
  assert.ok(camera, 'No se encontró una cámara activa');
  const id = camera.source_id ?? camera.id;
  const { response, payload } = await realApi(`/detections?camera=${encodeURIComponent(id)}&limit=10`);
  assert.equal(response.status, 200);
  assert.ok(Array.isArray(payload), 'La respuesta de detecciones no es una lista');
});
