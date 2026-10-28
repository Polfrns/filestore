import { createHmac, timingSafeEqual } from 'node:crypto';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const privacy = require('./privacy.cjs');
let tracing, api, sdk;
let initialized = false;

/** Initialize Langfuse once. Export failure never disables workspace operations. */
export async function initializeTelemetry(env = process.env) {
  if (initialized) return;
  initialized = true;
  if (env.LANGFUSE_TRACING_ENABLED === 'false' || !env.LANGFUSE_BASE_URL || !env.LANGFUSE_PUBLIC_KEY || !env.LANGFUSE_SECRET_KEY) return;
  try {
    const [{ NodeSDK }, { LangfuseSpanProcessor }, tracingApi, otelApi] = await Promise.all([
      import('@opentelemetry/sdk-node'), import('@langfuse/otel'), import('@langfuse/tracing'), import('@opentelemetry/api'),
    ]);
    sdk = new NodeSDK({ spanProcessors: [privacy.protectProcessor(new LangfuseSpanProcessor({ mask: ({ data }) => privacy.maskData(data), mediaUploadEnabled: false }))] });
    sdk.start();
    tracing = tracingApi;
    api = otelApi;
  } catch { console.error('[workspace tracing] initialization unavailable'); }
}

/** Accept a remote parent only when Roko signed it recently and it matches signed identity pseudonyms. */
export function verifyRemoteTrace(headers, identity, secret = process.env.OBSERVABILITY_TRACE_SECRET, now = Date.now()) {
  if (!secret || secret.length < 32 || !identity) return null;
  const raw = headers['x-observability-context'];
  const signature = headers['x-observability-signature'];
  if (typeof raw !== 'string' || raw.length > 2048 || typeof signature !== 'string' || !/^[a-f0-9]{64}$/.test(signature)) return null;
  const expected = createHmac('sha256', secret).update(raw).digest();
  if (!timingSafeEqual(expected, Buffer.from(signature, 'hex'))) return null;
  try {
    const payload = JSON.parse(Buffer.from(raw, 'base64url').toString('utf8'));
    if (!Number.isSafeInteger(payload.timestamp) || Math.abs(now - payload.timestamp) > 60_000
      || !/^00-(?!0{32})[a-f0-9]{32}-(?!0{16})[a-f0-9]{16}-0[01]$/.test(payload.traceparent)
      || !/^[a-f0-9]{64}$/.test(payload.sessionId) || !/^[a-f0-9]{64}$/.test(payload.userId)
      || payload.sessionId !== identity.sessionId || payload.userId !== identity.userId
      || typeof payload.tracestate !== 'string' || payload.tracestate.length > 512) return null;
    return payload;
  } catch { return null; }
}

/** One fail-open span per tool/REST operation; raw paths, commands, and file contents never enter spans. */
export async function withToolTrace(name, identity, parent, operation, metadata = {}) {
  if (!tracing || !api || !identity) return operation();
  let pending;
  const once = () => pending ??= Promise.resolve().then(operation);
  const run = () => tracing.propagateAttributes({ sessionId: identity.sessionId, userId: identity.userId }, () =>
    tracing.startActiveObservation(name, async span => {
      const safeMetadata = { tool: name, state: 'started', ...metadata };
      try {
        span.update({ metadata: safeMetadata });
        const result = await once();
        span.update({ metadata: { ...safeMetadata, state: 'completed', ...(typeof result?.bytes === 'number' ? { bytes: result.bytes } : {}) } });
        return result;
      } catch (error) {
        span.update({ level: 'ERROR', metadata: { ...safeMetadata, state: 'failed', errorCategory: error?.code ?? 'operation_failed' } });
        throw error;
      }
    }));
  try {
    let context = api.ROOT_CONTEXT;
    if (parent) {
      const [, traceId, spanId, flags] = parent.traceparent.split('-');
      context = api.trace.setSpanContext(context, { traceId, spanId, traceFlags: parseInt(flags, 16), isRemote: true,
        ...(parent.tracestate ? { traceState: api.createTraceState(parent.tracestate) } : {}) });
    }
    return await api.context.with(context, run);
  } catch { return once(); }
}

export async function shutdownTelemetry() {
  try { await sdk?.shutdown(); } catch { /* Export is deliberately fail-open. */ }
}
