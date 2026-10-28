import { WorkspaceError } from './errors.mjs';

export const GATE_WAIT_MS = 30_000;

/** Counting semaphore whose waiters give up after a deadline instead of queueing forever. */
class Semaphore {
  /** @param {number} capacity */
  constructor(capacity) {
    this.capacity = capacity;
    this.active = 0;
    /** @type {Array<() => void>} */
    this.waiters = [];
  }

  /** @param {number} waitMs @param {AbortSignal | undefined} signal @param {string} busyMessage */
  async acquire(waitMs, signal, busyMessage) {
    if (this.active < this.capacity) { this.active += 1; return; }
    await new Promise((resolve, reject) => {
      const grant = () => { cleanup(); this.active += 1; resolve(undefined); };
      const timer = setTimeout(() => { cleanup(); reject(new WorkspaceError('busy', busyMessage)); }, waitMs);
      const abort = () => { cleanup(); reject(signal?.reason ?? new Error('Request cancelled.')); };
      const cleanup = () => {
        clearTimeout(timer);
        signal?.removeEventListener('abort', abort);
        const index = this.waiters.indexOf(grant);
        if (index !== -1) this.waiters.splice(index, 1);
      };
      signal?.addEventListener('abort', abort, { once: true });
      this.waiters.push(grant);
    });
  }

  release() {
    this.active -= 1;
    this.waiters[0]?.();
  }
}

/**
 * One execution at a time per workspace, bounded per user and globally.
 * Also owns the AbortController of each running execution so deletion can stop it.
 */
export class ExecutionGate {
  /** @param {{perUser: number, global: number, waitMs?: number}} options */
  constructor({ perUser, global, waitMs = GATE_WAIT_MS }) {
    this.perUser = perUser;
    this.waitMs = waitMs;
    this.global = new Semaphore(global);
    /** @type {Map<string, Semaphore>} */
    this.users = new Map();
    /** @type {Map<string, Semaphore>} */
    this.workspaces = new Map();
    /** @type {Map<string, Set<AbortController>>} */
    this.running = new Map();
    /** @type {Map<string, Set<Promise<void>>>} */
    this.runningDone = new Map();
  }

  /**
   * @template T
   * @param {{key: string, userKey: string}} workspace
   * @param {AbortSignal | undefined} signal
   * @param {(signal: AbortSignal) => Promise<T>} run
   * @returns {Promise<T>}
   */
  async run(workspace, signal, run) {
    const own = this.slot(this.workspaces, workspace.key, 1);
    const user = this.slot(this.users, workspace.userKey, this.perUser);
    const acquired = [];
    const controller = new AbortController();
    const forward = () => controller.abort(signal?.reason);
    signal?.addEventListener('abort', forward, { once: true });
    if (signal?.aborted) forward();
    let finish;
    const done = new Promise(resolve => { finish = resolve; });
    const controllers = this.running.get(workspace.key) ?? new Set();
    controllers.add(controller);
    this.running.set(workspace.key, controllers);
    const pending = this.runningDone.get(workspace.key) ?? new Set();
    pending.add(done);
    this.runningDone.set(workspace.key, pending);
    try {
      await own.acquire(this.waitMs, controller.signal, 'Another command is still running in this workspace. Wait for it to finish.');
      acquired.push(own);
      await user.acquire(this.waitMs, controller.signal, 'Too many commands are running for this user. Try again shortly.');
      acquired.push(user);
      await this.global.acquire(this.waitMs, controller.signal, 'The workspace service is busy. Try again shortly.');
      acquired.push(this.global);
      return await run(controller.signal);
    } finally {
      signal?.removeEventListener('abort', forward);
      controllers.delete(controller);
      if (controllers.size === 0) this.running.delete(workspace.key);
      pending.delete(done);
      if (pending.size === 0) this.runningDone.delete(workspace.key);
      finish();
      for (const semaphore of acquired.reverse()) semaphore.release();
      this.prune(this.workspaces, workspace.key);
      this.prune(this.users, workspace.userKey);
    }
  }

  /** Stops the running execution of a workspace, if any. @param {string} key */
  async abort(key) {
    for (const controller of this.running.get(key) ?? []) controller.abort(new WorkspaceError('not_found', 'The workspace was deleted.'));
    await Promise.all(this.runningDone.get(key) ?? []);
  }

  /** @param {Map<string, Semaphore>} map @param {string} key @param {number} capacity */
  slot(map, key, capacity) {
    let semaphore = map.get(key);
    if (!semaphore) { semaphore = new Semaphore(capacity); map.set(key, semaphore); }
    return semaphore;
  }

  /** @param {Map<string, Semaphore>} map @param {string} key */
  prune(map, key) {
    const semaphore = map.get(key);
    if (semaphore && semaphore.active === 0 && semaphore.waiters.length === 0) map.delete(key);
  }
}
