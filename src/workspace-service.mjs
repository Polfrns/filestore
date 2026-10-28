import { lstat, mkdir, readdir, readFile, rename, rm, writeFile } from 'node:fs/promises';
import { basename, dirname, extname, join, relative, isAbsolute } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';
import { assertWritable, compileGlob, parseWorkspacePath, resolveConfined } from './paths.mjs';
import { WorkspaceError } from './errors.mjs';
import { SandboxRunner } from './sandbox.mjs';

const MAX_LIST_ENTRIES = 500;
const MAX_READ_BYTES = 100 * 1024;
const MAX_READ_LINES = 2000;
const SCRIPT_FILE = fileURLToPath(new URL('../scripts/file-tools.py', import.meta.url));

/** @typedef {{path: string, bytes: number, type: 'file'|'directory'}} WorkspaceEntry */

/** Owns per-conversation file and execution operations. Dependencies are replaceable in tests. */
export class WorkspaceService {
  /** @param {{store: import('./workspace-store.mjs').WorkspaceStore, gate: import('./execution-gate.mjs').ExecutionGate, config: import('./config.mjs').ServiceConfig, runnerFactory?: (root: string) => SandboxRunner, fileToolScript?: string}} options */
  constructor({ store, gate, config, runnerFactory = root => new SandboxRunner({ workspace: root, dataDir: config.dataDir, port: config.port }), fileToolScript = SCRIPT_FILE }) {
    this.store = store;
    this.gate = gate;
    this.config = config;
    this.runnerFactory = runnerFactory;
    this.fileToolScript = fileToolScript;
    this.runners = new Map();
  }

  /** Serialize filesystem tools with shell execution so a command cannot swap paths during a check/use pair. */
  withWorkspace(owner, create, operation) {
    return this.store.use(owner, { create }, ws => this.gate.run({ key: ws.key, userKey: owner.userKey }, undefined, () => operation(ws)));
  }

  /** @param {import('./identity.mjs').WorkspaceOwner} owner @param {string|undefined} glob */
  async listFiles(owner, glob) {
    const matcher = glob === undefined ? null : compileGlob(glob);
    return this.withWorkspace(owner, false, async ws => {
      const rows = [];
      const visit = async (directory, relative) => {
        for (const entry of await readdir(directory, { withFileTypes: true }).catch(error => error.code === 'ENOENT' ? [] : Promise.reject(error))) {
          const path = relative ? `${relative}/${entry.name}` : entry.name;
          const absolute = join(directory, entry.name);
          const stats = await lstat(absolute);
          if (stats.isSymbolicLink()) continue;
          if (stats.isDirectory()) {
            if (!matcher || matcher.test(path)) rows.push({ path, bytes: 0, type: 'directory' });
            await visit(absolute, path);
          } else if (stats.isFile() && (!matcher || matcher.test(path))) rows.push({ path, bytes: stats.size, type: 'file' });
          if (rows.length > MAX_LIST_ENTRIES) throw new WorkspaceError('limit_exceeded', `More than ${MAX_LIST_ENTRIES} entries match; provide a narrower glob.`);
        }
      };
      for (const zone of ['inputs', 'work', 'outputs']) await visit(join(ws.root, zone), zone);
      return rows.length ? rows.map(row => `${row.path}\t${row.bytes}\t${row.type}`).join('\n') : 'No files found.';
    });
  }

  /** @param {import('./identity.mjs').WorkspaceOwner} owner @param {string} rawPath @param {number|undefined} start @param {number|undefined} end */
  async readFile(owner, rawPath, start, end) {
    const path = parseWorkspacePath(rawPath);
    return this.withWorkspace(owner, false, async ws => {
      const absolute = await resolveConfined(ws.root, path);
      const stats = await lstat(absolute).catch(error => error.code === 'ENOENT' ? null : Promise.reject(error));
      if (!stats?.isFile()) throw new WorkspaceError('not_found', `File not found: ${path.relative}`);
      if (stats.size > MAX_READ_BYTES * 20) throw new WorkspaceError('too_large', 'Use line ranges to read this large file.');
      const bytes = await readFile(absolute);
      let text;
      try { text = new TextDecoder('utf-8', { fatal: true }).decode(bytes); }
      catch { return `Binary or non-UTF-8 file (${bytes.length} bytes, ${extname(path.relative) || 'unknown type'}). Use prepare_file to inspect supported documents.`; }
      const lines = text.split(/\r?\n/);
      if (start === undefined && end === undefined && (lines.length > MAX_READ_LINES || bytes.length > MAX_READ_BYTES)) {
        throw new WorkspaceError('too_large', 'Specify start_line and end_line to read a bounded range.');
      }
      const first = start ?? 1, last = end ?? Math.min(lines.length, first + MAX_READ_LINES - 1);
      if (first > lines.length || last < first || last - first + 1 > MAX_READ_LINES) throw new WorkspaceError('invalid_request', 'Line range must be ordered and contain at most 2000 lines.');
      const selected = lines.slice(first - 1, last);
      const output = selected.map((line, index) => `${first + index}: ${line}`).join('\n');
      if (Buffer.byteLength(output) > MAX_READ_BYTES) throw new WorkspaceError('too_large', 'This line range exceeds 100 KiB; request a narrower range.');
      return `Lines ${first}-${first + selected.length - 1} of ${lines.length}:\n${output}`;
    });
  }

  /** @param {import('./identity.mjs').WorkspaceOwner} owner @param {string} rawPath @param {string} content */
  async writeFile(owner, rawPath, content) {
    const path = parseWorkspacePath(rawPath);
    assertWritable(path);
    const bytes = Buffer.from(content, 'utf8');
    if (bytes.length > this.config.limits.writeMaxBytes) throw new WorkspaceError('too_large', 'write_file content exceeds 2 MiB.');
    return this.withWorkspace(owner, true, async ws => {
      const target = await resolveConfined(ws.root, path);
      const previous = await lstat(target).catch(error => error.code === 'ENOENT' ? null : Promise.reject(error));
      if (previous && !previous.isFile()) throw new WorkspaceError('invalid_path', 'write_file target must be a regular file.');
      const delta = bytes.length - (previous?.size ?? 0);
      await this.store.assertRoom(ws, Math.max(0, delta), previous ? 0 : 1);
      await mkdir(dirname(target), { recursive: true });
      const temp = join(dirname(target), `.${basename(target)}.${randomUUID()}.tmp`);
      try {
        await writeFile(temp, bytes, { flag: 'wx', mode: 0o600 });
        await resolveConfined(ws.root, path);
        await rename(temp, target);
      } finally { await rm(temp, { force: true }); }
      return `Wrote ${bytes.length} bytes to ${path.relative}.`;
    });
  }

  /** @param {import('./identity.mjs').WorkspaceOwner} owner @param {string} rawPath @param {string} oldText @param {string} newText */
  async editFile(owner, rawPath, oldText, newText) {
    const path = parseWorkspacePath(rawPath);
    assertWritable(path);
    if (!oldText) throw new WorkspaceError('invalid_request', 'old must not be empty.');
    return this.withWorkspace(owner, false, async ws => {
      const absolute = await resolveConfined(ws.root, path);
      let current;
      try { current = new TextDecoder('utf-8', { fatal: true }).decode(await readFile(absolute)); } catch (error) {
        if (error.code === 'ENOENT') throw new WorkspaceError('not_found', `File not found: ${path.relative}`);
        if (error instanceof TypeError) throw new WorkspaceError('not_text', 'File is not valid UTF-8 text.');
        throw error;
      }
      const first = current.indexOf(oldText);
      if (first === -1) throw new WorkspaceError('no_match', 'The exact old text was not found.');
      if (current.indexOf(oldText, first + oldText.length) !== -1) throw new WorkspaceError('ambiguous_match', 'The old text occurs more than once; include more surrounding context.');
      const next = current.slice(0, first) + newText + current.slice(first + oldText.length);
      const size = Buffer.byteLength(next);
      if (size > this.config.limits.writeMaxBytes) throw new WorkspaceError('too_large', 'Edited file exceeds 2 MiB.');
      await this.store.assertRoom(ws, Math.max(0, size - Buffer.byteLength(current)), 0);
      const temp = join(dirname(absolute), `.${basename(absolute)}.${randomUUID()}.tmp`);
      try { await writeFile(temp, next, { flag: 'wx', mode: 0o600 }); await resolveConfined(ws.root, path); await rename(temp, absolute); }
      finally { await rm(temp, { force: true }); }
      return `Updated ${path.relative}.`;
    });
  }

  /** @param {import('./identity.mjs').WorkspaceOwner} owner @param {{command: string, timeout_ms?: number}} args @param {AbortSignal|undefined} signal */
  async execute(owner, args, signal) {
    return this.store.use(owner, { create: true }, async ws => this.gate.run({ key: ws.key, userKey: owner.userKey }, signal, async runSignal => {
      let runner = this.runners.get(ws.key);
      if (!runner) { runner = this.runnerFactory(ws.root); this.runners.set(ws.key, runner); }
      const started = Date.now();
      let result;
      try { result = await runner.execute(args.command, { signal: runSignal, timeout: args.timeout_ms ?? 60_000 }); }
      catch (error) {
        if (error.code === 'timeout') throw new WorkspaceError('timeout', error.message);
        if (error.code === 'output_limit') throw new WorkspaceError('limit_exceeded', error.message);
        throw new WorkspaceError('sandbox_unavailable', `Sandbox execution failed: ${error.message}`);
      }
      const usage = await this.store.usage(ws);
      const overQuota = usage.bytes > this.config.limits.workspaceMaxBytes || usage.files > this.config.limits.workspaceMaxFiles;
      return { ...result, durationMs: Date.now() - started, truncated: false, usage, overQuota, isError: result.exitCode !== 0 };
    }));
  }

  /** @param {import('./identity.mjs').WorkspaceOwner} owner @param {'prepare'|'convert'|'recalc'|'validate'} operation @param {string} rawPath @param {AbortSignal|undefined} signal @param {string|undefined} output */
  async fileTool(owner, operation, rawPath, signal, output) {
    const path = parseWorkspacePath(rawPath);
    const outputPath = output === undefined ? null : parseWorkspacePath(output);
    if (outputPath) assertWritable(outputPath);
    return this.store.use(owner, { create: true }, async ws => this.gate.run({ key: ws.key, userKey: owner.userKey }, signal, async runSignal => {
      await resolveConfined(ws.root, path);
      const args = [this.fileToolScript, operation, '--workspace', ws.root, '--path', path.relative];
      if (outputPath) args.push('--output', outputPath.relative);
      const runner = this.runners.get(ws.key) ?? this.runnerFactory(ws.root);
      this.runners.set(ws.key, runner);
      const result = await runner.runProgram('python3', args, { signal: runSignal, timeout: 90_000 });
      let value;
      try { value = JSON.parse(result.stdout); } catch { throw new WorkspaceError('file_tool_failed', 'File tool returned invalid JSON.'); }
      if (result.exitCode !== 0) throw new WorkspaceError(value.code === 'missing_tool' ? 'unsupported_conversion' : 'file_tool_failed', value.error ?? 'File operation failed.');
      if (Array.isArray(value.images)) {
        const content = [{ type: 'text', text: value.text ?? '' }];
        for (const imagePath of value.images.slice(0, 4)) {
          const rel = isAbsolute(imagePath) ? relative(ws.root, imagePath) : imagePath;
          const imageRelative = rel.split('\\').join('/');
          if (imageRelative === '..' || imageRelative.startsWith('../') || isAbsolute(imageRelative)) throw new WorkspaceError('invalid_path', 'Prepared preview escaped the workspace.');
          const image = await readFile(await resolveConfined(ws.root, { relative: imageRelative }));
          const mimeType = imageRelative.toLowerCase().endsWith('.jpg') || imageRelative.toLowerCase().endsWith('.jpeg') ? 'image/jpeg' : 'image/png';
          content.push({ type: 'image', data: image.toString('base64'), mimeType });
        }
        return { content };
      }
      return value;
    }));
  }
}
