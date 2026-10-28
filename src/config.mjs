import { resolve } from 'node:path';

const MiB = 1024 * 1024;
const GiB = 1024 * MiB;

export const MIN_KEY_LENGTH = 32;
export const IDENTITY_MAX_AGE_MS = 10 * 60 * 1000;

/**
 * Every tuning knob in one place. Values come from the environment once at startup.
 * @typedef {object} Limits
 * @property {number} ttlMs              idle time before a workspace is deleted
 * @property {number} sweepIntervalMs
 * @property {number} workspaceMaxBytes
 * @property {number} workspaceMaxFiles
 * @property {number} globalMaxBytes
 * @property {number} uploadMaxBytes
 * @property {number} writeMaxBytes      write_file content
 * @property {number} execPerUser
 * @property {number} execGlobal
 * @property {number} deliveriesPerWorkspace
 * @property {number} deliverableMaxBytes
 */

/**
 * @typedef {object} ServiceConfig
 * @property {string} key          ROKO_WORKSPACE_KEY
 * @property {string} dataDir
 * @property {string} host
 * @property {number} port
 * @property {string} ocrLang
 * @property {Limits} limits
 */

/** @type {Limits} */
export const DEFAULT_LIMITS = Object.freeze({
  ttlMs: 30 * 60 * 1000,
  sweepIntervalMs: 60 * 1000,
  workspaceMaxBytes: 512 * MiB,
  workspaceMaxFiles: 5000,
  globalMaxBytes: 20 * GiB,
  uploadMaxBytes: 100 * MiB,
  writeMaxBytes: 2 * MiB,
  execPerUser: 2,
  execGlobal: 4,
  deliveriesPerWorkspace: 10,
  deliverableMaxBytes: 15 * MiB,
});

/** @param {Record<string, string | undefined>} env @param {string} name @param {number} fallback */
function positiveInteger(env, name, fallback) {
  const raw = env[name];
  if (raw === undefined || raw === '') return fallback;
  const value = Number(raw);
  if (!Number.isSafeInteger(value) || value <= 0) throw new Error(`${name} must be a positive integer, got "${raw}".`);
  return value;
}

/** @param {Record<string, string | undefined>} env @returns {ServiceConfig} */
export function loadConfig(env = process.env) {
  const key = env.ROKO_WORKSPACE_KEY ?? '';
  if (key.length < MIN_KEY_LENGTH) throw new Error(`ROKO_WORKSPACE_KEY must contain at least ${MIN_KEY_LENGTH} characters.`);
  const limits = {
    ttlMs: positiveInteger(env, 'WORKSPACE_TTL_MS', DEFAULT_LIMITS.ttlMs),
    sweepIntervalMs: positiveInteger(env, 'WORKSPACE_SWEEP_MS', DEFAULT_LIMITS.sweepIntervalMs),
    workspaceMaxBytes: positiveInteger(env, 'WORKSPACE_MAX_BYTES', DEFAULT_LIMITS.workspaceMaxBytes),
    workspaceMaxFiles: positiveInteger(env, 'WORKSPACE_MAX_FILES', DEFAULT_LIMITS.workspaceMaxFiles),
    globalMaxBytes: positiveInteger(env, 'WORKSPACE_GLOBAL_MAX_BYTES', DEFAULT_LIMITS.globalMaxBytes),
    uploadMaxBytes: positiveInteger(env, 'WORKSPACE_MAX_UPLOAD_BYTES', DEFAULT_LIMITS.uploadMaxBytes),
    writeMaxBytes: DEFAULT_LIMITS.writeMaxBytes,
    execPerUser: positiveInteger(env, 'WORKSPACE_MAX_EXEC_PER_USER', DEFAULT_LIMITS.execPerUser),
    execGlobal: positiveInteger(env, 'WORKSPACE_MAX_EXEC_GLOBAL', DEFAULT_LIMITS.execGlobal),
    deliveriesPerWorkspace: DEFAULT_LIMITS.deliveriesPerWorkspace,
    deliverableMaxBytes: DEFAULT_LIMITS.deliverableMaxBytes,
  };
  return Object.freeze({
    key,
    dataDir: resolve(env.WORKSPACE_DATA_DIR || '/data'),
    host: env.WORKSPACE_HOST || '0.0.0.0',
    port: positiveInteger(env, 'PORT', 8080),
    ocrLang: env.WORKSPACE_OCR_LANG || 'eng+spa',
    limits: Object.freeze(limits),
  });
}
