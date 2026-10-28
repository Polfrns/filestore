import { lstat, realpath } from 'node:fs/promises';
import { dirname, isAbsolute, relative, resolve, sep } from 'node:path';
import { WorkspaceError } from './errors.mjs';

/** @typedef {'inputs'|'work'|'outputs'} Zone */

/** @type {readonly Zone[]} */
export const ZONES = Object.freeze(['inputs', 'work', 'outputs']);
/** Zones the model may write through file tools. `inputs/` belongs to Roko uploads. */
export const WRITABLE_ZONES = Object.freeze(new Set(['work', 'outputs']));
const MAX_PATH_CHARS = 1024;

/**
 * A validated workspace-relative path. Only parseWorkspacePath constructs one.
 * @typedef {object} WorkspacePath
 * @property {Zone} zone
 * @property {string} relative   POSIX, starts with the zone, no `.`/`..`/empty segments
 * @property {boolean} isZoneRoot
 */

/** @param {string} message @returns {never} */
function invalid(message) { throw new WorkspaceError('invalid_path', message); }

/** Rejects everything that could be read as absolute or as a traversal on any OS. */
function assertRelativeSyntax(/** @type {unknown} */ value, /** @type {string} */ what) {
  if (typeof value !== 'string' || !value) invalid(`${what} must be a non-empty string.`);
  if (value.length > MAX_PATH_CHARS) invalid(`${what} is longer than ${MAX_PATH_CHARS} characters.`);
  if (value.includes('\0')) invalid(`${what} contains a NUL character.`);
  if (value.includes('\\')) invalid(`${what} must use "/" separators.`);
  if (value.startsWith('/') || value.startsWith('~') || /^[A-Za-z]:/.test(value)) invalid(`${what} must be relative to the workspace.`);
  if (value.split('/').some(segment => segment === '..')) invalid(`${what} must not contain "..".`);
  return value;
}

/**
 * @param {unknown} input
 * @param {{allowZoneRoot?: boolean}} [options]
 * @returns {WorkspacePath}
 */
export function parseWorkspacePath(input, { allowZoneRoot = false } = {}) {
  const value = assertRelativeSyntax(input, 'path');
  const segments = value.split('/');
  if (segments.some(segment => segment === '' || segment === '.')) invalid('path must not contain empty or "." segments.');
  const zone = /** @type {Zone} */ (segments[0]);
  if (!ZONES.includes(zone)) invalid(`path must start with ${ZONES.map(z => `${z}/`).join(', ')}.`);
  if (segments.length === 1 && !allowZoneRoot) invalid('path must name a file inside the zone, not the zone itself.');
  return { zone, relative: segments.join('/'), isZoneRoot: segments.length === 1 };
}

/** @param {WorkspacePath} path */
export function assertWritable(path) {
  if (!WRITABLE_ZONES.has(path.zone)) throw new WorkspaceError('read_only_zone', `${path.zone}/ is read-only; write to work/ or outputs/.`);
}

/**
 * Compiles the filestore glob contract: `*` and `?` stay within a segment, `**` spans segments,
 * character classes as usual. Matches complete workspace-relative paths.
 * @param {unknown} pattern
 * @returns {RegExp}
 */
export function compileGlob(pattern) {
  const glob = assertRelativeSyntax(pattern, 'glob');
  let source = '';
  for (let i = 0; i < glob.length; i++) {
    const char = glob[i];
    if (char === '*' && glob[i + 1] === '*') {
      const segmentStart = i === 0 || glob[i - 1] === '/';
      if (segmentStart && glob[i + 2] === '/') { source += '(?:[^/]*/)*'; i += 2; continue; }
      source += '.*'; i += 1; continue;
    }
    if (char === '*') { source += '[^/]*'; continue; }
    if (char === '?') { source += '[^/]'; continue; }
    if (char === '[') {
      const close = glob.indexOf(']', i + 2);
      if (close !== -1) {
        let body = glob.slice(i + 1, close).replace(/\\/g, '\\\\').replace(/\//g, '');
        if (body.startsWith('!')) body = '^' + body.slice(1);
        source += `[${body}]`; i = close; continue;
      }
    }
    source += char.replace(/[.+^${}()|[\]\\]/g, '\\$&');
  }
  return new RegExp(`^${source}$`);
}

/** @param {string} root @param {string} path */
const inside = (root, path) => { const rel = relative(root, path); return rel === '' || (!isAbsolute(rel) && rel !== '..' && !rel.startsWith(`..${sep}`)); };

/**
 * Absolute host path for a validated workspace path. Checks every existing component:
 * a lexical check alone lets a symlink created from the shell escape the workspace.
 * Missing trailing components are allowed so new files can be created.
 * @param {string} workspaceRoot
 * @param {WorkspacePath} path
 * @returns {Promise<string>}
 */
export async function resolveConfined(workspaceRoot, path) {
  const root = await realpath(workspaceRoot);
  const target = resolve(root, ...path.relative.split('/'));
  if (!inside(root, target)) invalid('path escapes the workspace.');
  let current = target;
  while (current !== root) {
    try {
      const stats = await lstat(current);
      if (stats.isSymbolicLink()) invalid(`${relative(root, current).split(sep).join('/')} is a symbolic link; links are never followed.`);
      if (!inside(root, await realpath(current))) invalid('path escapes the workspace.');
    } catch (error) {
      if (error instanceof WorkspaceError) throw error;
      if (/** @type {NodeJS.ErrnoException} */ (error).code !== 'ENOENT') throw error;
    }
    current = dirname(current);
  }
  return target;
}
