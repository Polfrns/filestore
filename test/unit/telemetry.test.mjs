import assert from 'node:assert/strict';
import { createHmac } from 'node:crypto';
import { test } from 'node:test';
import { verifyRemoteTrace } from '../../src/telemetry.mjs';

function headersFor(payload, secret) {
  const value = Buffer.from(JSON.stringify(payload)).toString('base64url');
  const signature = createHmac('sha256', secret).update(value).digest('hex');
  return { 'x-observability-context': value, 'x-observability-signature': signature };
}

test('remote trace parent must match the signed identity and remain fresh', () => {
  const secret = 's'.repeat(32);
  const identity = { sessionId: 'a'.repeat(64), userId: 'b'.repeat(64) };
  const payload = { ...identity, traceparent: `00-${'c'.repeat(32)}-${'d'.repeat(16)}-01`, tracestate: '', timestamp: 1_000 };
  const headers = headersFor(payload, secret);
  assert.deepEqual(verifyRemoteTrace(headers, identity, secret, 1_000), payload);
  assert.equal(verifyRemoteTrace(headers, { ...identity, userId: 'e'.repeat(64) }, secret, 1_000), null);
  assert.equal(verifyRemoteTrace(headers, identity, secret, 61_001), null);
});
