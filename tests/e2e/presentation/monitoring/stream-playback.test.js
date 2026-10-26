import assert from 'node:assert/strict';
import test from 'node:test';

const streamUrl = process.env.E2E_STREAM_URL ?? 'https://184.194.94.182/camera03/';

test('la página real de reproducción de camera03 está disponible', async () => {
  const response = await fetch(streamUrl, { signal: AbortSignal.timeout(20_000) });
  assert.equal(response.status, 200);
  assert.match(response.headers.get('content-type') ?? '', /text\/html/i);
});
