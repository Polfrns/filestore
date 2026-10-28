import { createHmac, timingSafeEqual } from 'node:crypto';
import { IDENTITY_MAX_AGE_MS } from './config.mjs';
import { WorkspaceError } from './errors.mjs';

export const CONTEXT_AUDIENCE = 'roko-workspace';
export const CONTEXT_VERSION = 1;
const CONTEXT_KEY_LABEL = 'roko-workspace/context/v1';
const WORKSPACE_KEY_LABEL = 'roko-workspace/workspace-id/v1';
const MAX_CONTEXT_CHARS = 4096;
const MAX_ID_CHARS = 256;
const TELEMETRY_ID = /^[a-f0-9]{64}$/;

/**
 * Who is calling, as asserted by Roko's signature. Raw ids never leave this process.
 * @typedef {object} RokoIdentity
 * @property {string} tenantId
 * @property {string} userId
 * @property {string} conversationId
 * @property {{sessionId: string, userId: string} | null} telemetry  pseudonyms for Langfuse
 */

/**
 * Keys derived once from ROKO_WORKSPACE_KEY; the raw secret is only compared, never reused for HMAC.
 * @typedef {object} DerivedKeys
 * @property {Buffer} bearer
 * @property {Buffer} context
 * @property {Buffer} workspaceId
 */

/** @param {string} secret @returns {DerivedKeys} */
export function deriveKeys(secret) {
  const label = (/** @type {string} */ name) => createHmac('sha256', secret).update(name).digest();
  return { bearer: Buffer.from(secret), context: label(CONTEXT_KEY_LABEL), workspaceId: label(WORKSPACE_KEY_LABEL) };
}

/**
 * @typedef {object} WorkspaceOwner
 * @property {string} workspaceKey  opaque, unguessable directory name for (tenant, user, conversation)
 * @property {string} userKey       opaque per-user key for concurrency limits
 */

/** @param {RokoIdentity} identity @param {DerivedKeys} keys @returns {WorkspaceOwner} */
export function workspaceOwnerFor(identity, keys) {
  const userKey = hmacHex(keys.workspaceId, ['user', identity.tenantId, identity.userId]).slice(0, 32);
  return { userKey, workspaceKey: hmacHex(keys.workspaceId, ['conversation', userKey, identity.conversationId]).slice(0, 32) };
}

/** @param {Buffer} key @param {unknown[]} parts */
function hmacHex(key, parts) {
  return createHmac('sha256', key).update(JSON.stringify(parts)).digest('hex');
}

/** @param {string} expected @param {string} actual */
function sameSecret(expected, actual) {
  const a = Buffer.from(expected), b = Buffer.from(actual);
  return a.length === b.length && timingSafeEqual(a, b);
}

/** @param {unknown} value @param {number} min */
const boundedString = (value, min) => typeof value === 'string' && value.length >= min && value.length <= MAX_ID_CHARS && !value.includes('\0');

/** @param {string} message @returns {never} */
function reject(message) { throw new WorkspaceError('unauthorized', message); }

/** Signs a context exactly as Roko does; used by tests and the local check script. */
export function signContext(/** @type {DerivedKeys} */ keys, /** @type {object} */ payload) {
  const context = Buffer.from(JSON.stringify(payload)).toString('base64url');
  return { context, signature: createHmac('sha256', keys.context).update(context).digest('hex') };
}

/**
 * Pure verification of the service bearer plus the signed per-request identity.
 * Throws WorkspaceError('unauthorized') for every failure; callers touch no disk before this returns.
 * @param {Record<string, string | string[] | undefined>} headers  lower-cased, as node:http provides
 * @param {DerivedKeys} keys
 * @param {number} now
 * @returns {RokoIdentity}
 */
export function verifyIdentity(headers, keys, now) {
  const authorization = headers.authorization;
  if (typeof authorization !== 'string' || !authorization.startsWith('Bearer ')
    || !sameSecret(keys.bearer.toString(), authorization.slice('Bearer '.length))) reject('Invalid service credentials.');
  const context = headers['x-roko-context'], signature = headers['x-roko-signature'];
  if (typeof context !== 'string' || !context || context.length > MAX_CONTEXT_CHARS) reject('Missing signed identity.');
  if (typeof signature !== 'string' || !/^[a-f0-9]{64}$/.test(signature)) reject('Missing signed identity.');
  const expected = createHmac('sha256', keys.context).update(context).digest();
  if (!timingSafeEqual(expected, Buffer.from(signature, 'hex'))) reject('Invalid identity signature.');
  let payload;
  try { payload = JSON.parse(Buffer.from(context, 'base64url').toString('utf8')); } catch { reject('Malformed identity.'); }
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) reject('Malformed identity.');
  if (payload.v !== CONTEXT_VERSION || payload.aud !== CONTEXT_AUDIENCE) reject('Identity is not for this service.');
  if (!Number.isSafeInteger(payload.timestamp) || Math.abs(now - payload.timestamp) > IDENTITY_MAX_AGE_MS) reject('Identity has expired.');
  if (!boundedString(payload.userId, 1) || !boundedString(payload.conversationId, 1) || !boundedString(payload.tenantId ?? '', 0)) reject('Malformed identity.');
  const telemetry = payload.telemetry;
  const validTelemetry = telemetry && typeof telemetry === 'object' && TELEMETRY_ID.test(telemetry.sessionId) && TELEMETRY_ID.test(telemetry.userId);
  return {
    tenantId: payload.tenantId ?? '',
    userId: payload.userId,
    conversationId: payload.conversationId,
    telemetry: validTelemetry ? { sessionId: telemetry.sessionId, userId: telemetry.userId } : null,
  };
}
