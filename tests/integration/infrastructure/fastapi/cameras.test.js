import assert from 'node:assert/strict';
import test from 'node:test';
import { activeCamera, cameraRows, realApi } from '../../support/real-api.js';

test('FastAPI real entrega al menos una cámara activa', async () => {
  const { response, payload } = await realApi('/cameras');
  const cameras = cameraRows(payload);
  assert.equal(response.status, 200);
  assert.ok(cameras.length > 0, 'FastAPI no devolvió cámaras registradas');
  assert.ok(cameras.some((camera) => camera.enabled !== false), 'No hay cámaras activas');
});

test('la cámara activa contiene identificador y ruta de stream', async () => {
  const camera = await activeCamera();
  assert.ok(camera, 'No se encontró una cámara activa');
  assert.ok(camera.source_id ?? camera.id, 'La cámara no tiene identificador');
  assert.ok(camera.stream_path ?? camera.streamPath, 'La cámara no tiene stream_path');
});
