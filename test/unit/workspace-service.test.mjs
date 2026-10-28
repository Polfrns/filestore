import assert from 'node:assert/strict';
import { mkdtemp, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { DEFAULT_LIMITS } from '../../src/config.mjs';
import { ExecutionGate } from '../../src/execution-gate.mjs';
import { WorkspaceError } from '../../src/errors.mjs';
import { deriveKeys, workspaceOwnerFor } from '../../src/identity.mjs';
import { WorkspaceService } from '../../src/workspace-service.mjs';
import { WorkspaceStore } from '../../src/workspace-store.mjs';

async function fixture(t) {
  const dataDir = await mkdtemp(join(tmpdir(), 'roko-workspace-'));
  t.after(() => rm(dataDir, { recursive: true, force: true }));
  const config = { dataDir, port: 8080, limits: DEFAULT_LIMITS };
  const service = new WorkspaceService({
    store: new WorkspaceStore({ dataDir, limits: DEFAULT_LIMITS, clock: Date.now }),
    gate: new ExecutionGate({ perUser: 2, global: 4 }), config,
    runnerFactory: () => ({ execute: async () => ({ exitCode: 0, stdout: '', stderr: '' }), verify: async () => {}, runProgram: async () => ({ exitCode: 0, stdout: '{"valid":true}', stderr: '' }) }),
  });
  const keys = deriveKeys('x'.repeat(32));
  const owner = workspaceOwnerFor({ tenantId: '', userId: 'u', conversationId: 'c', telemetry: null }, keys);
  return { service, owner };
}

test('file tools refuse writes to Roko-owned inputs before workspace creation', async t => {
  const { service, owner } = await fixture(t);
  await assert.rejects(service.writeFile(owner, 'inputs/source.txt', 'changed'), error => error instanceof WorkspaceError && error.code === 'read_only_zone');
});

test('file tools reject traversal before workspace I/O', async t => {
  const { service, owner } = await fixture(t);
  await assert.rejects(service.readFile(owner, 'outputs/../inputs/source.txt'), error => error instanceof WorkspaceError && error.code === 'invalid_path');
});

test('writes and edits output files without changing unrelated text', async t => {
  const { service, owner } = await fixture(t);
  await service.writeFile(owner, 'outputs/report.txt', 'first\nsecond\n');
  await service.editFile(owner, 'outputs/report.txt', 'second', 'updated');
  assert.equal(await service.readFile(owner, 'outputs/report.txt'), 'Lines 1-3 of 3:\n1: first\n2: updated\n3: ');
});

test('file tools refuse a symlink that points outside the workspace', { skip: process.platform === 'win32' }, async t => {
  const { service, owner } = await fixture(t);
  await service.writeFile(owner, 'outputs/inside.txt', 'safe');
  const outside = join(tmpdir(), `roko-outside-${Date.now()}.txt`);
  await writeFile(outside, 'secret');
  t.after(() => rm(outside, { force: true }));
  const workspace = service.store.locate(owner);
  await symlink(outside, join(workspace.root, 'work', 'escape.txt'));
  await assert.rejects(service.readFile(owner, 'work/escape.txt'), error => error instanceof WorkspaceError && error.code === 'invalid_path');
});
