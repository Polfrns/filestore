import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { test } from 'node:test';
import { SandboxRunner } from '../../src/sandbox.mjs';

const linuxOnly = process.platform !== 'linux';

test('bubblewrap confines writes, keeps inputs read-only, and disables network', { skip: linuxOnly }, async t => {
  const root = await mkdtemp('/data/roko-sandbox-');
  t.after(() => rm(root, { recursive: true, force: true }));
  const workspace = join(root, 'workspace');
  await mkdir(join(workspace, 'inputs'), { recursive: true });
  await mkdir(join(workspace, 'outputs'), { recursive: true });
  await writeFile(join(workspace, 'inputs', 'source.txt'), 'protected');
  const runner = new SandboxRunner({ workspace, dataDir: root, port: 8080 });
  const proof = await runner.verify();
  assert.equal(proof.workspaceWrite, true);
  assert.equal(proof.outsideReadDenied, true);
  assert.equal(proof.outsideWriteDenied, true);
  assert.equal(proof.inputsReadOnly, true);
  assert.equal(proof.networkDenied, true);
  assert.equal(proof.nestedUserNamespacesBlocked, true);
  const result = await runner.execute('printf result > outputs/result.txt; test "$(cat inputs/source.txt)" = protected');
  assert.equal(result.exitCode, 0);
  assert.equal(await readFile(join(workspace, 'outputs', 'result.txt'), 'utf8'), 'result');
  const tool = await runner.runProgram('python3', ['/app/scripts/file-tools.py', 'validate', '--workspace', workspace, '--path', 'outputs/result.txt']);
  assert.equal(tool.exitCode, 0);
  assert.equal(JSON.parse(tool.stdout).valid, true);
});

test('bubblewrap sandbox enforces command timeout', { skip: linuxOnly }, async t => {
  const root = await mkdtemp('/data/roko-timeout-');
  t.after(() => rm(root, { recursive: true, force: true }));
  const runner = new SandboxRunner({ workspace: join(root, 'workspace'), dataDir: root, port: 8080 });
  await assert.rejects(runner.execute('sleep 5', { timeout: 1000 }), /timed out/i);
});
