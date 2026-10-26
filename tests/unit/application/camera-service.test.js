import assert from 'node:assert/strict';
import test from 'node:test';
import { CameraNotFoundError, createCameraService } from '../../../src/application/cameras/camera-service.js';

function registry(cameras = []) {
  return {
    list: async () => cameras,
    get: async (id) => cameras.find((camera) => camera.id === id) ?? null,
    map: (row) => ({ id: row.source_id, streamPath: row.stream_path, enabled: true })
  };
}

test('registra la siguiente cámara disponible e inicia su fuente', async () => {
  const calls = [];
  const inferenceClient = async (path, options = {}) => {
    calls.push({ path, options });
    if (path === '/cameras') return { camera: options.body, created_at: '2026-10-17T10:00:00.000Z' };
    return { source_id: options.body.source_id, status: 'running' };
  };
  const service = createCameraService({ inferenceClient, cameraRegistry: registry([]) });

  const result = await service.register({
    name: 'Entrada', normalizedUrl: 'https://media.example/camera01', streamPath: 'camera01'
  });

  assert.equal(result.camera.id, 'camara1');
  assert.equal(result.camera.url, 'https://media.example/camera01/');
  assert.equal(result.inference.available, true);
  assert.deepEqual(calls.map((call) => call.path), ['/cameras', '/sources/start']);
});

test('start informa cuando la cámara no existe', async () => {
  const service = createCameraService({
    inferenceClient: async () => ({}), cameraRegistry: registry([])
  });
  await assert.rejects(() => service.start('camara99'), CameraNotFoundError);
});

test('remove no llama al backend cuando la cámara no existe', async () => {
  let calls = 0;
  const service = createCameraService({
    inferenceClient: async () => { calls += 1; }, cameraRegistry: registry([])
  });
  assert.deepEqual(await service.remove('camara99'), { removed: true });
  assert.equal(calls, 0);
});
