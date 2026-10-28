import { randomUUID } from 'node:crypto';
import { lstat, mkdir, readdir, readFile, rename, rm, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { WorkspaceError } from './errors.mjs';
import { ZONES } from './paths.mjs';

const KEY_PATTERN = /^[a-f0-9]{32}$/;
const META_FILE = 'meta.json';

/**
 * @typedef {object} Workspace
 * @property {string} key       WorkspaceOwner.workspaceKey
 * @property {string} userKey
 * @property {string} dir       private parent: meta.json and workspace/
 * @property {string} root      sandbox-writable root containing inputs/, work/, outputs/
 */

/**
 * @typedef {object} WorkspaceMeta
 * @property {number} createdAt
 * @property {number} lastUsedAt
 * @property {number} deliveries
 */

/** @typedef {{bytes: number, files: number}} Usage */

/** @param {unknown} value @returns {value is WorkspaceMeta} */
const isMeta = value => !!value && typeof value === 'object'
  && ['createdAt', 'lastUsedAt', 'deliveries'].every(key => Number.isSafeInteger(/** @type {any} */ (value)[key]));

/** Size of a tree without following links. Symlinks count as entries but not as their targets. */
export async function measure(/** @type {string} */ root) {
  /** @type {Usage} */
  const usage = { bytes: 0, files: 0 };
  const visit = async (/** @type {string} */ directory) => {
    let entries;
    try { entries = await readdir(directory, { withFileTypes: true }); } catch (error) {
      if (/** @type {NodeJS.ErrnoException} */ (error).code === 'ENOENT') return;
      throw error;
    }
    for (const entry of entries) {
      const path = join(directory, entry.name);
      const stats = await lstat(path);
      if (stats.isDirectory()) { await visit(path); continue; }
      usage.files += 1;
      usage.bytes += stats.size;
    }
  };
  await visit(root);
  return usage;
}

export class WorkspaceStore {
  /**
   * @param {{dataDir: string, limits: import('./config.mjs').Limits, clock: () => number}} options
   */
  constructor({ dataDir, limits, clock }) {
    this.base = join(dataDir, 'w');
    this.limits = limits;
    this.clock = clock;
    /** @type {Map<string, number>} last measured bytes per workspace; their sum is the global usage */
    this.bytesByKey = new Map();
    /** @type {Map<string, number>} operations in flight; the sweeper never deletes these */
    this.inUse = new Map();
  }

  /** @param {string} key */
  dirFor(key) {
    if (!KEY_PATTERN.test(key)) throw new Error('Invalid workspace key.');
    return join(this.base, key);
  }

  /** @param {import('./identity.mjs').WorkspaceOwner} owner @returns {Workspace} */
  locate(owner) {
    const dir = this.dirFor(owner.workspaceKey);
    return { key: owner.workspaceKey, userKey: owner.userKey, dir, root: join(dir, 'workspace') };
  }

  /** @param {Workspace} workspace */
  async exists(workspace) {
    return (await this.readMeta(workspace.dir)) !== null;
  }

  /**
   * Runs `operation` with the workspace marked in use, creating it on demand.
   * @template T
   * @param {import('./identity.mjs').WorkspaceOwner} owner
   * @param {{create: boolean}} options
   * @param {(workspace: Workspace) => Promise<T>} operation
   * @returns {Promise<T>}
   */
  async use(owner, { create }, operation) {
    const workspace = this.locate(owner);
    this.inUse.set(workspace.key, (this.inUse.get(workspace.key) ?? 0) + 1);
    try {
      if (create) await this.ensure(workspace);
      else if (await this.exists(workspace)) await this.touch(workspace);
      return await operation(workspace);
    } finally {
      const count = (this.inUse.get(workspace.key) ?? 1) - 1;
      if (count > 0) this.inUse.set(workspace.key, count); else this.inUse.delete(workspace.key);
      if (await this.exists(workspace)) await this.touch(workspace);
    }
  }

  /** @param {Workspace} workspace */
  async ensure(workspace) {
    await mkdir(this.base, { recursive: true, mode: 0o700 });
    for (const zone of ZONES) await mkdir(join(workspace.root, zone), { recursive: true, mode: 0o700 });
    const meta = await this.readMeta(workspace.dir);
    const now = this.clock();
    await this.writeMeta(workspace.dir, meta ? { ...meta, lastUsedAt: now } : { createdAt: now, lastUsedAt: now, deliveries: 0 });
  }

  /** @param {Workspace} workspace */
  async touch(workspace) {
    const meta = await this.readMeta(workspace.dir);
    if (meta) await this.writeMeta(workspace.dir, { ...meta, lastUsedAt: this.clock() });
  }

  /** Counts one delivered output against the per-conversation limit. @param {Workspace} workspace */
  async recordDelivery(workspace) {
    const meta = await this.readMeta(workspace.dir);
    if (!meta) throw new WorkspaceError('not_found', 'This conversation has no workspace.');
    if (meta.deliveries >= this.limits.deliveriesPerWorkspace) {
      throw new WorkspaceError('limit_exceeded', `At most ${this.limits.deliveriesPerWorkspace} files can be delivered per conversation workspace.`);
    }
    await this.writeMeta(workspace.dir, { ...meta, deliveries: meta.deliveries + 1 });
  }

  /** Re-measures one workspace and returns its usage. @param {Workspace} workspace */
  async usage(workspace) {
    const usage = await measure(workspace.root);
    this.bytesByKey.set(workspace.key, usage.bytes);
    return usage;
  }

  globalBytes() {
    let total = 0;
    for (const bytes of this.bytesByKey.values()) total += bytes;
    return total;
  }

  /**
   * Checked before every write that the service performs itself. Never truncates.
   * @param {Workspace} workspace @param {number} addBytes @param {number} [addFiles]
   */
  async assertRoom(workspace, addBytes, addFiles = 1) {
    const usage = await this.usage(workspace);
    if (usage.bytes + addBytes > this.limits.workspaceMaxBytes) {
      throw new WorkspaceError('quota_exceeded', `The workspace would exceed its ${this.limits.workspaceMaxBytes}-byte limit (now ${usage.bytes} bytes). Delete files from work/ or outputs/ first.`);
    }
    if (usage.files + addFiles > this.limits.workspaceMaxFiles) {
      throw new WorkspaceError('quota_exceeded', `The workspace would exceed its ${this.limits.workspaceMaxFiles}-file limit.`);
    }
    if (this.globalBytes() + addBytes > this.limits.globalMaxBytes) {
      throw new WorkspaceError('quota_exceeded', 'The workspace service is out of storage. Try again later.');
    }
  }

  /** Over-quota state after a shell command wrote freely. @param {Workspace} workspace */
  async overQuota(workspace) {
    const usage = await this.usage(workspace);
    return usage.bytes > this.limits.workspaceMaxBytes || usage.files > this.limits.workspaceMaxFiles;
  }

  /** @param {string} key */
  async remove(key) {
    await rm(this.dirFor(key), { recursive: true, force: true });
    this.bytesByKey.delete(key);
  }

  /**
   * Deletes idle and malformed workspaces, then refreshes global usage. Safe at startup:
   * anything left half-created by a crash has no valid meta.json and is removed.
   * @returns {Promise<{removed: number, kept: number}>}
   */
  async sweep() {
    let names;
    try { names = await readdir(this.base); } catch (error) {
      if (/** @type {NodeJS.ErrnoException} */ (error).code === 'ENOENT') return { removed: 0, kept: 0 };
      throw error;
    }
    const now = this.clock();
    let removed = 0, kept = 0;
    for (const name of names) {
      if (this.inUse.has(name)) { kept += 1; continue; }
      const dir = join(this.base, name);
      const meta = KEY_PATTERN.test(name) ? await this.readMeta(dir) : null;
      if (!meta || now - meta.lastUsedAt > this.limits.ttlMs) {
        await rm(dir, { recursive: true, force: true });
        this.bytesByKey.delete(name);
        removed += 1;
      } else {
        this.bytesByKey.set(name, (await measure(join(dir, 'workspace'))).bytes);
        kept += 1;
      }
    }
    return { removed, kept };
  }

  /** @param {string} dir @returns {Promise<WorkspaceMeta | null>} */
  async readMeta(dir) {
    let text;
    try { text = await readFile(join(dir, META_FILE), 'utf8'); } catch (error) {
      if (/** @type {NodeJS.ErrnoException} */ (error).code === 'ENOENT') return null;
      throw error;
    }
    try {
      const value = JSON.parse(text);
      return isMeta(value) ? value : null;
    } catch { return null; } // Malformed meta is treated as an abandoned workspace.
  }

  /** Atomic replace so a crash never leaves a half-written meta.json. @param {string} dir @param {WorkspaceMeta} meta */
  async writeMeta(dir, meta) {
    const temporary = join(dir, `${META_FILE}.${randomUUID()}.tmp`);
    await writeFile(temporary, JSON.stringify(meta), { mode: 0o600 });
    await rename(temporary, join(dir, META_FILE));
  }
}
