import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { SandboxRunner } from '../src/sandbox.mjs';

if (process.platform !== 'linux') throw new Error('Workspace sandbox check requires Linux. Run it in the production Docker image.');
const dataDir = await mkdtemp(join(tmpdir(), 'roko-workspace-check-'));
try {
  const runner = new SandboxRunner({ workspace: join(dataDir, 'probe', 'workspace'), dataDir, port: Number(process.env.PORT || 8080) });
  const evidence = await runner.verify();
  const read = await runner.runProgram('python3', ['-c', "import pandas, matplotlib, docx, pymupdf; print('file tools available')"]);
  if (read.exitCode !== 0) throw new Error(read.stderr || 'Python document tool check failed.');
  const output = await runner.runProgram('python3', [new URL('./file-tools.py', import.meta.url).pathname, 'validate', '--workspace', runner.workspace, '--path', 'outputs/probe.txt']);
  if (output.exitCode === 0) throw new Error('File validation probe unexpectedly accepted a missing file.');
  console.log(JSON.stringify(evidence));
} finally { await rm(dataDir, { recursive: true, force: true }); }
