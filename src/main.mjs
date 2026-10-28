import { mkdir, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { loadConfig } from './config.mjs';
import { deriveKeys } from './identity.mjs';
import { ExecutionGate } from './execution-gate.mjs';
import { WorkspaceStore } from './workspace-store.mjs';
import { WorkspaceService } from './workspace-service.mjs';
import { SandboxRunner } from './sandbox.mjs';
import { createWorkspaceHttpServer } from './http-server.mjs';
import { initializeTelemetry, shutdownTelemetry } from './telemetry.mjs';

const config = loadConfig();
await initializeTelemetry();
const keys = deriveKeys(config.key);
const store = new WorkspaceStore({ dataDir: config.dataDir, limits: config.limits, clock: Date.now });
const gate = new ExecutionGate({ perUser: config.limits.execPerUser, global: config.limits.execGlobal });
const service = new WorkspaceService({ store, gate, config });
let ready = false;

await mkdir(config.dataDir, { recursive: true, mode: 0o700 });
await store.sweep();
const server = createWorkspaceHttpServer({ config, keys, store, gate, service, isReady: () => ready });
server.listen(config.port, config.host, async () => {
  console.info(`roko-workspace listening on ${config.host}:${config.port}`);
  const probeRoot = join(config.dataDir, '.startup-probe', 'workspace');
  const runner = new SandboxRunner({ workspace: probeRoot, dataDir: config.dataDir, port: config.port });
  try {
    await runner.verify();
    ready = true;
    console.info('Workspace sandbox canary and network probes passed.');
  } catch (error) {
    ready = false;
    console.error(`Workspace sandbox is unavailable: ${error.message}`);
  } finally {
    await rm(join(config.dataDir, '.startup-probe'), { recursive: true, force: true });
  }
});

const sweeper = setInterval(() => {
  void store.sweep().catch(error => console.error(`Workspace sweep failed: ${error.message}`));
}, config.limits.sweepIntervalMs);
sweeper.unref();

const shutdown = () => {
  clearInterval(sweeper);
  ready = false;
  server.close(() => { void shutdownTelemetry().finally(() => process.exit(0)); });
  setTimeout(() => process.exit(1), 10_000).unref();
};
process.once('SIGTERM', shutdown);
process.once('SIGINT', shutdown);
