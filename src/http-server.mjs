import { createServer } from 'node:http';
import { randomUUID } from 'node:crypto';
import { lstat, open, readFile, link, rm } from 'node:fs/promises';
import { extname, join } from 'node:path';
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';
import { createWorkspaceMcpServer } from './mcp-server.mjs';
import { parseWorkspacePath, resolveConfined } from './paths.mjs';
import { verifyIdentity, workspaceOwnerFor } from './identity.mjs';
import { WorkspaceError } from './errors.mjs';
import { withToolTrace, verifyRemoteTrace } from './telemetry.mjs';

const MAX_MCP_BODY_BYTES = 1024 * 1024;
const MIME_TYPES = Object.freeze({ '.pdf': 'application/pdf', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation', '.txt': 'text/plain; charset=utf-8', '.csv': 'text/csv; charset=utf-8', '.json': 'application/json', '.md': 'text/markdown; charset=utf-8' });

/** @param {import('node:http').IncomingMessage} request @param {number} limit */
async function readJson(request, limit) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > limit) throw new WorkspaceError('too_large', `Request body exceeds ${limit} bytes.`);
    chunks.push(chunk);
  }
  try { return JSON.parse(Buffer.concat(chunks).toString('utf8')); }
  catch { throw new WorkspaceError('invalid_request', 'Request body must be valid JSON.'); }
}

function json(response, status, value) {
  response.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' });
  response.end(JSON.stringify(value));
}

function errorResponse(response, error) {
  const failure = error instanceof WorkspaceError ? error : new WorkspaceError('invalid_request', 'The request could not be completed.');
  json(response, failure.status, { code: failure.code, message: failure.message });
}

function safeUploadName(raw) {
  let name;
  try { name = decodeURIComponent(raw); } catch { throw new WorkspaceError('invalid_path', 'Upload filename has invalid percent encoding.'); }
  if (!name || name === '.' || name === '..' || /[/\\\0\x00-\x1f\x7f]/.test(name) || name.length > 255 || name.trim() !== name) throw new WorkspaceError('invalid_path', 'Upload filename must be one safe path segment.');
  return name;
}

function magicMatches(extension, bytes) {
  if (extension === '.pdf') return bytes.subarray(0, 5).toString('ascii') === '%PDF-';
  if (extension === '.png') return bytes.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
  if (extension === '.jpg' || extension === '.jpeg') return bytes.length >= 3 && bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff;
  return true;
}

/** Create the internal-only service HTTP server. Authentication runs before body parsing or workspace lookup. */
export function createWorkspaceHttpServer({ config, keys, store, gate, service, isReady = () => true, logger = console }) {
  const authenticate = request => {
    const identity = verifyIdentity(request.headers, keys, Date.now());
    return { identity, owner: workspaceOwnerFor(identity, keys), traceContext: verifyRemoteTrace(request.headers, identity.telemetry) };
  };

  const server = createServer(async (request, response) => {
    const url = new URL(request.url ?? '/', 'http://workspace.local');
    if (request.method === 'GET' && url.pathname === '/health') {
      return json(response, isReady() ? 200 : 503, { status: isReady() ? 'ok' : 'unavailable' });
    }
    if (url.pathname === '/mcp' && request.method === 'GET') return json(response, 405, { code: 'method_not_allowed', message: 'GET SSE is disabled.' });
    if (request.method === 'POST' && url.pathname === '/mcp') {
      try {
        const { identity, owner, traceContext } = authenticate(request);
        if (!isReady()) throw new WorkspaceError('sandbox_unavailable', 'Workspace sandbox has not passed its startup checks.');
        const body = await readJson(request, MAX_MCP_BODY_BYTES);
        const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined });
        const mcp = createWorkspaceMcpServer({ service, owner, identity, traceContext });
        await mcp.connect(transport);
        await transport.handleRequest(request, response, body);
        response.once('close', () => { void transport.close(); void mcp.close(); });
      } catch (error) { errorResponse(response, error); }
      return;
    }
    try {
      const { identity, owner, traceContext } = authenticate(request);
      if (!isReady()) throw new WorkspaceError('sandbox_unavailable', 'Workspace sandbox has not passed its startup checks.');
      if (request.method === 'PUT' && url.pathname.startsWith('/v1/workspace/inputs/')) {
        const filename = safeUploadName(url.pathname.slice('/v1/workspace/inputs/'.length));
        const declared = Number(request.headers['content-length']);
        if (!Number.isSafeInteger(declared) || declared < 0) throw new WorkspaceError('invalid_request', 'Content-Length is required.');
        if (declared > config.limits.uploadMaxBytes) throw new WorkspaceError('too_large', `Uploads are limited to ${config.limits.uploadMaxBytes} bytes.`);
        const result = await withToolTrace('workspace.rest.upload', identity.telemetry, traceContext,
          () => uploadInput({ request, owner, filename, declared, config, store }), { operation: 'upload' });
        return json(response, 201, result);
      }
      if (request.method === 'GET' && url.pathname.startsWith('/v1/workspace/outputs/')) {
        const encoded = url.pathname.slice('/v1/workspace/outputs/'.length);
        let decoded;
        try { decoded = encoded.split('/').map(segment => decodeURIComponent(segment)).join('/'); }
        catch { throw new WorkspaceError('invalid_path', 'Output path has invalid percent encoding.'); }
        const output = parseWorkspacePath(`outputs/${decoded}`);
        const payload = await withToolTrace('workspace.rest.download', identity.telemetry, traceContext,
          () => downloadOutput({ owner, output, service, store, gate, config }), { operation: 'download' });
        response.writeHead(200, { 'content-type': payload.mimeType, 'content-length': payload.bytes.length, 'x-workspace-filename': encodeURIComponent(payload.filename), 'cache-control': 'no-store' });
        return response.end(payload.bytes);
      }
      if (request.method === 'DELETE' && url.pathname === '/v1/workspace') {
        await withToolTrace('workspace.rest.delete', identity.telemetry, traceContext, async () => {
          await gate.abort(owner.workspaceKey);
          await store.remove(owner.workspaceKey);
        }, { operation: 'delete' });
        return response.writeHead(204).end();
      }
      throw new WorkspaceError('not_found', 'Route not found.');
    } catch (error) {
      if (error instanceof WorkspaceError && error.code === 'unauthorized') logger.warn?.('Rejected unauthenticated workspace request.');
      errorResponse(response, error);
    }
  });
  return server;
}

async function uploadInput({ request, owner, filename, declared, config, store }) {
  return store.use(owner, { create: true }, async ws => {
    await store.assertRoom(ws, declared, 1);
    const temp = join(ws.dir, `.upload-${randomUUID()}.tmp`);
    const handle = await open(temp, 'wx', 0o600);
    let bytes = 0;
    try {
      for await (const chunk of request) {
        bytes += chunk.length;
        if (bytes > config.limits.uploadMaxBytes || bytes > declared) throw new WorkspaceError('too_large', 'Upload body exceeds its declared or configured size.');
        await handle.writeFile(chunk);
      }
      if (bytes !== declared) throw new WorkspaceError('invalid_request', 'Upload body size does not match Content-Length.');
      await handle.sync();
    } finally { await handle.close(); }
    try {
      const dir = join(ws.root, 'inputs');
      let finalName = filename, counter = 2;
      while (true) {
        const target = join(dir, finalName);
        try { await link(temp, target); break; }
        catch (error) {
          if (error.code !== 'EEXIST') throw error;
          const dot = filename.lastIndexOf('.');
          finalName = dot > 0 ? `${filename.slice(0, dot)}-${counter}${filename.slice(dot)}` : `${filename}-${counter}`;
          counter += 1;
        }
      }
      await rm(temp, { force: true });
      const usage = await store.usage(ws);
      if (usage.bytes > config.limits.workspaceMaxBytes || usage.files > config.limits.workspaceMaxFiles) {
        await rm(join(dir, finalName), { force: true });
        throw new WorkspaceError('quota_exceeded', 'The workspace quota was reached while uploading.');
      }
      return { path: `inputs/${finalName}`, bytes };
    } finally { await rm(temp, { force: true }); }
  });
}

async function downloadOutput({ owner, output, service, store, gate, config }) {
  return store.use(owner, { create: false }, async ws => {
    const absolute = await resolveConfined(ws.root, output);
    const stats = await lstat(absolute).catch(error => error.code === 'ENOENT' ? null : Promise.reject(error));
    if (!stats?.isFile()) throw new WorkspaceError('not_found', 'Output file not found.');
    if (stats.size === 0 || stats.size > config.limits.deliverableMaxBytes) throw new WorkspaceError('invalid_output', 'Output must be non-empty and no larger than 15 MiB.');
    const bytes = await readFile(absolute);
    if (!magicMatches(extname(absolute).toLowerCase(), bytes)) throw new WorkspaceError('invalid_output', 'Output file signature does not match its extension.');
    if (!['.pdf', '.png', '.jpg', '.jpeg'].includes(extname(absolute).toLowerCase())) {
      const result = await service.fileTool(owner, 'validate', output.relative, undefined);
      if (!result.valid) throw new WorkspaceError('invalid_output', 'Output validation failed.');
    }
    await gate.run({ key: ws.key, userKey: owner.userKey }, undefined, async () => store.recordDelivery(ws));
    return { bytes, filename: output.relative.split('/').at(-1), mimeType: MIME_TYPES[extname(absolute).toLowerCase()] ?? 'application/octet-stream' };
  });
}
