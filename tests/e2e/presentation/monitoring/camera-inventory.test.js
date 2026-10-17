import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { startRealFrontend } from '../../support/frontend-server.js';

let app;
before(async () => { app = await startRealFrontend(); });
after(async () => { await app?.close(); });

test('el frontend consulta el inventario real a través de /api/camera', async () => {
  const response = await fetch(`${app.baseUrl}/api/camera`, { signal: AbortSignal.timeout(20_000) });
  const payload = await response.json();
  assert.equal(response.status, 200);
  assert.ok(Array.isArray(payload.cameras));
  assert.ok(payload.cameras.length > 0, 'El frontend no recibió cámaras de FastAPI');
});
