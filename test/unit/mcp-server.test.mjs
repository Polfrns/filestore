import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createWorkspaceMcpServer } from '../../src/mcp-server.mjs';

test('workspace MCP exposes the approved tools and no output download tool', () => {
  const server = createWorkspaceMcpServer({ service: {} });
  const names = Object.keys(server._registeredTools);
  assert.deepEqual(names, [
    'list_files', 'read_file', 'write_file', 'edit_file', 'execute',
    'prepare_file', 'convert', 'recalc', 'validate',
  ]);
});
