import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { once } from 'node:events';
import { test } from 'node:test';
import { DEFAULT_LIMITS } from '../../src/config.mjs';
import { ExecutionGate } from '../../src/execution-gate.mjs';
import { deriveKeys, signContext, workspaceOwnerFor } from '../../src/identity.mjs';
import { createWorkspaceHttpServer } from '../../src/http-server.mjs';
import { WorkspaceService } from '../../src/workspace-service.mjs';
import { WorkspaceStore } from '../../src/workspace-store.mjs';

async function runningServer(t, ready = true) {
  const dataDir = await mkdtemp(join(tmpdir(), 'roko-http-'));
  t.after(() => rm(dataDir, { recursive: true, force: true }));
  const config = { dataDir, host: '127.0.0.1', port: 0, key: 'k'.repeat(32), limits: DEFAULT_LIMITS };
  const keys = deriveKeys(config.key);
  const store = new WorkspaceStore({ dataDir, limits: DEFAULT_LIMITS, clock: Date.now });
  const gate = new ExecutionGate({ perUser: 2, global: 4 });
  const service = new WorkspaceService({ store, gate, config, runnerFactory: () => ({ execute: async () => ({ exitCode: 0, stdout: '', stderr: '' }), verify: async () => {}, runProgram: async () => ({ exitCode: 0, stdout: '{"valid":true}', stderr: '' }) }) });
  const server = createWorkspaceHttpServer({ config, keys, store, gate, service, isReady: () => ready });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => server.close());
  const address = server.address();
  const origin = `http://127.0.0.1:${address.port}`;
  const context = signContext(keys, { v: 1, aud: 'roko-workspace', tenantId: '', userId: 'user', conversationId: 'convo', timestamp: Date.now() });
  const headers = { authorization: `Bearer ${config.key}`, 'x-roko-context': context.context, 'x-roko-signature': context.signature };
  const owner = workspaceOwnerFor({ tenantId: '', userId: 'user', conversationId: 'convo', telemetry: null }, keys);
  return { origin, headers, store, owner };
}

test('health is public and reports startup canary state', async t => {
  const { origin } = await runningServer(t, false);
  const response = await fetch(`${origin}/health`);
  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), { status: 'unavailable' });
});

test('missing MCP identity is rejected before reading the body', async t => {
  const { origin } = await runningServer(t);
  const response = await fetch(`${origin}/mcp`, { method: 'POST', body: '{' });
  assert.equal(response.status, 401);
  assert.equal((await response.json()).code, 'unauthorized');
});

test('stateless MCP accepts separately authenticated initialize and tools/list requests', async t => {
  const { origin, headers } = await runningServer(t);
  const post = async body => fetch(`${origin}/mcp`, {
    method: 'POST',
    headers: { ...headers, accept: 'application/json, text/event-stream', 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
  const initialized = await post({ jsonrpc: '2.0', id: 1, method: 'initialize', params: { protocolVersion: '2025-03-26', capabilities: {}, clientInfo: { name: 'test', version: '1' } } });
  assert.equal(initialized.status, 200);
  assert.equal(initialized.headers.get('mcp-session-id'), null);
  const listed = await post({ jsonrpc: '2.0', id: 2, method: 'tools/list', params: {} });
  assert.equal(listed.status, 200);
  const event = (await listed.text()).split('\n').find(line => line.startsWith('data: '));
  const payload = JSON.parse(event.slice('data: '.length));
  assert.deepEqual(payload.result.tools.map(tool => tool.name), [
    'list_files', 'read_file', 'write_file', 'edit_file', 'execute', 'prepare_file', 'convert', 'recalc', 'validate',
  ]);
});

test('explicitly uploaded inputs are kept in inputs/ and never overwritten', async t => {
  const { origin, headers } = await runningServer(t);
  let response = await fetch(`${origin}/v1/workspace/inputs/source.txt`, { method: 'PUT', headers: { ...headers, 'content-length': '3' }, body: 'one' });
  assert.equal(response.status, 201);
  assert.deepEqual(await response.json(), { path: 'inputs/source.txt', bytes: 3 });
  response = await fetch(`${origin}/v1/workspace/inputs/source.txt`, { method: 'PUT', headers: { ...headers, 'content-length': '3' }, body: 'two' });
  assert.equal(response.status, 201);
  assert.deepEqual(await response.json(), { path: 'inputs/source-2.txt', bytes: 3 });
});

test('output download validates the zone, returns binary bytes and counts delivery', async t => {
  const { origin, headers, store, owner } = await runningServer(t);
  const workspace = store.locate(owner);
  await store.use(owner, { create: true }, async ws => writeFile(join(ws.root, 'outputs', 'report.pdf'), '%PDF-1.4\nbody'));
  const response = await fetch(`${origin}/v1/workspace/outputs/report.pdf`, { headers });
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('x-workspace-filename'), 'report.pdf');
  assert.equal((await response.arrayBuffer()).byteLength, Buffer.byteLength('%PDF-1.4\nbody'));
  const meta = await store.readMeta(workspace.dir);
  assert.equal(meta.deliveries, 1);
});
