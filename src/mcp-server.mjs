import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { z } from 'zod';
import { WorkspaceError } from './errors.mjs';
import { withToolTrace } from './telemetry.mjs';

export const WORKSPACE_INSTRUCTIONS = `You work in this conversation's Linux workspace. Files are under inputs/ (source files, read-only), work/ (scratch), and outputs/ (deliverables). Use prepare_file before editing supported binary documents, validate the final output, and save deliverables under outputs/. Network access and package installation are disabled. Use deliver_workspace_file in the chat to attach a finished output.`;

/** @param {{service: import('./workspace-service.mjs').WorkspaceService, owner: import('./identity.mjs').WorkspaceOwner, identity: import('./identity.mjs').RokoIdentity, signal?: AbortSignal}} options */
export function createWorkspaceMcpServer({ service, owner, identity, traceContext, signal }) {
  const server = new McpServer({ name: 'roko-workspace', version: '1.0.0' }, { instructions: WORKSPACE_INSTRUCTIONS });
  const register = (name, description, inputSchema, operation) => {
    server.registerTool(name, { description, inputSchema }, async (args, extra) => {
      try {
        const result = await withToolTrace(`workspace.${name}`, identity?.telemetry, traceContext, () => operation(args, extra.signal), { tool: name });
        const isError = typeof result === 'object' && result !== null && result.isError === true;
        const displayed = isError ? Object.fromEntries(Object.entries(result).filter(([key]) => key !== 'isError')) : result;
        if (displayed && typeof displayed === 'object' && Array.isArray(displayed.content)) return { isError, content: displayed.content };
        return { isError, content: [{ type: 'text', text: typeof displayed === 'string' ? displayed : JSON.stringify(displayed) }] };
      } catch (error) {
        const failure = error instanceof WorkspaceError
          ? error
          : new WorkspaceError('file_tool_failed', 'The workspace operation failed.');
        return { isError: true, content: [{ type: 'text', text: `${failure.code}: ${failure.message}` }] };
      }
    });
  };
  register('list_files', 'List files in this conversation workspace. Optionally narrow the result with a POSIX glob.', { glob: z.string().optional() }, args => service.listFiles(owner, args.glob));
  register('read_file', 'Read UTF-8 text from a workspace file. Use line ranges for large files.', {
    path: z.string(), start_line: z.number().int().positive().optional(), end_line: z.number().int().positive().optional(),
  }, args => service.readFile(owner, args.path, args.start_line, args.end_line));
  register('write_file', 'Create or replace a UTF-8 file in work/ or outputs/. inputs/ is read-only.', { path: z.string(), content: z.string() }, args => service.writeFile(owner, args.path, args.content));
  register('edit_file', 'Replace one exact, unique text occurrence in a file under work/ or outputs/.', { path: z.string(), old: z.string(), new: z.string() }, args => service.editFile(owner, args.path, args.old, args.new));
  register('execute', 'Run a Linux shell command in this workspace. No network or package installs. Write final files to outputs/.', {
    command: z.string().min(1).max(16000), description: z.string().max(240).optional(), timeout_ms: z.number().int().min(1000).max(120000).optional(),
  }, (args, callSignal) => service.execute(owner, args, callSignal ?? signal));
  register('prepare_file', 'Extract text and previews from a PDF or office document before editing.', { path: z.string() }, (args, callSignal) => service.fileTool(owner, 'prepare', args.path, callSignal ?? signal));
  register('convert', 'Convert a supported document into another format and write it to work/ or outputs/.', { path: z.string(), output: z.string() }, (args, callSignal) => service.fileTool(owner, 'convert', args.path, callSignal ?? signal, args.output));
  register('recalc', 'Recalculate a spreadsheet and report formula errors.', { path: z.string(), output: z.string() }, (args, callSignal) => service.fileTool(owner, 'recalc', args.path, callSignal ?? signal, args.output));
  register('validate', 'Validate a document using the available format-specific checks.', { path: z.string() }, (args, callSignal) => service.fileTool(owner, 'validate', args.path, callSignal ?? signal));
  return server;
}
