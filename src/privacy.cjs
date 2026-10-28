'use strict';
// Export-boundary policy: preserve measurements and bounded technical enums only.
const REDACTED = '[redacted]';
function contentMode() {
  return process.env.OBSERVABILITY_CONTENT_MODE === 'debug' ? 'debug' : 'redacted';
}
function contentLimit() {
  const value = Number(process.env.OBSERVABILITY_CONTENT_MAX_CHARS || 4000);
  return Number.isFinite(value) ? Math.max(256, Math.min(16000, Math.floor(value))) : 4000;
}
const PRIVATE_FIELD = /(?:^|[_-])(?:key|token|secret|password)(?:[_-]|$)|authorization|cookie|password|passwd|secret|credential|api[_-]?key|access[_-]?token|refresh[_-]?token|private[_-]?key|^(token|headers?|env|environment|command|cmd|path|file_path|url|data|base64|bytes|buffer|artifact|artifacts|attachments?)$/i;
function scrubText(value) {
  return value
    .replace(/([a-z][a-z0-9+.-]*:\/\/)[^\s/:@]+:[^\s/@]+@/gi, '$1[redacted]@')
    .replace(/-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?(?:-----END [^-]*PRIVATE KEY-----|$)/g, REDACTED)
    .replace(/data:[^\s,;]+(?:;[^,]*)?;base64,[A-Za-z0-9+/=_-]+/gi, REDACTED)
    .replace(/\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+/gi, REDACTED)
    .replace(/\b(?:sk-|pk-lf-|sk-lf-|ghp_|github_pat_|AIza|xox[baprs]-|ATATT|ATOA)[A-Za-z0-9_-]{8,}/g, REDACTED)
    .replace(/\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/g, REDACTED)
    .replace(/((?:[A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)|authorization|password|passwd|secret|token|api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret)\s*[=:]\s*)(?:"[^"\n]*"|'[^'\n]*'|[^\s,;]+)/gi, '$1[redacted]')
    .replace(/\b(?:UEsDB|JVBERi0|iVBORw0KGgo)[A-Za-z0-9+/=_-]*/g, REDACTED)
    .replace(/\b[A-Za-z0-9+/=_-]{64,}\b/g, REDACTED)
    .replace(/(?:\/(?:app|home|auth|data|workspace|tmp|etc)\/|[A-Za-z]:\\)[^\s"'<>]*/g, REDACTED);
}
/** Bounded export copy. Debug permits text, never attachment bytes or secrets. */
function sanitizeContent(data) {
  if (contentMode() !== 'debug') return REDACTED;
  let remaining = contentLimit();
  const seen = new WeakSet();
  function walk(value, depth = 0) {
    if (remaining <= 0 || depth > 8) return '[truncated]';
    if (value == null || typeof value === 'boolean' || typeof value === 'number') return value;
    if (typeof value === 'string') {
      // Parse structured payloads before truncation so late secret fields cannot escape.
      if (value.length <= 1000000 && /^[\[{]/.test(value.trim())) {
        try { return walk(JSON.parse(value), depth + 1); } catch { /* plain text */ }
      }
      if (value.length > 1000000) return '[oversized content]';
      const safe = scrubText(value);
      const result = safe.slice(0, remaining);
      remaining -= result.length;
      return result + (result.length < safe.length ? '[truncated]' : '');
    }
    if (typeof value !== 'object' || Buffer.isBuffer(value) || ArrayBuffer.isView(value) || seen.has(value)) return REDACTED;
    seen.add(value);
    if (Array.isArray(value)) return value.slice(0, 30).map(item => walk(item, depth + 1));
    // A file block may hide its bytes under an SDK-specific property.
    if (/file|image|audio|document|base64/i.test(String(value.type ?? value.source_type ?? ''))) {
      return {type: 'attachment', content: REDACTED};
    }
    const output = {};
    for (const [key, child] of Object.entries(value).slice(0, 60)) {
      if (!/^[A-Za-z0-9_.-]{1,80}$/.test(key) || remaining <= 0) continue;
      remaining -= key.length;
      output[key] = PRIVATE_FIELD.test(key) ? REDACTED : walk(child, depth + 1);
    }
    return output;
  }
  return walk(data);
}
function maskData(data) {
  return contentMode() === 'debug' ? sanitizeContent(data) : sanitizeData(data);
}
const ENUM = /^(contentMode|provider|tool|operation|state|status|errorCategory|finishReason|extension|mimeType|type|model|service|category|filename|mcpServer|usageAccounting)$/;
const COUNTER = /^(durationMs|attempt|exitCode|startedAt|endedAt|bytes|attachmentCount|generatedFileCount|missingStart|truncated|observedAt)$/;
function sanitizeData(data, depth=0, seen=new WeakSet()) {
  if (depth > 6) return REDACTED;
  if (data == null || typeof data === 'boolean' || typeof data === 'number') return data;
  if (typeof data === 'string') {
    if (data.length <= 65536 && /^[\[{]/.test(data.trim())) {
      try {return JSON.stringify(sanitizeData(JSON.parse(data),depth+1,seen));} catch { /* opaque text */ }
    }
    return REDACTED;
  }
  if (typeof data !== 'object' || Buffer.isBuffer(data) || ArrayBuffer.isView(data)) return REDACTED;
  if (seen.has(data)) return REDACTED;
  seen.add(data);
  if (Array.isArray(data)) return data.slice(0,30).map(v=>sanitizeData(v,depth+1,seen));
  const out={};
  for(const [key,value] of Object.entries(data).slice(0,60)) {
    if (COUNTER.test(key) && ['number','boolean'].includes(typeof value)) out[key]=value;
    else if (ENUM.test(key) && typeof value==='string' && value.length<=120 && /^[A-Za-z0-9_. -]+$/.test(value)
      && !/token|password|secret|credential|authorization/i.test(value)
      && (key !== 'filename' || process.env.OBSERVABILITY_FILE_NAMES==='true')) out[key]=value;
    else if (/^(id|executionId|operationId)$/.test(key) && typeof value==='string' && /^[A-Za-z0-9_-]{16,80}$/.test(value)) out[key]=value;
    else if (key==='missingStart' && typeof value==='boolean') out[key]=value;
  }
  return out;
}
// These SDK fields are JSON strings, not arbitrary message content. Only known
// metering keys with finite, nonnegative numeric values may cross the boundary.
const METERING_KEYS = new Set(['input','output','total','input_tokens','output_tokens','total_tokens',
  'input_cached_tokens','cached_tokens','cached_input','cache_read_input_tokens','cache_creation_input_tokens',
  'input_cache_read','input_cache_creation','output_reasoning','reasoning_tokens']);
function sanitizeMetering(value) {
  if (typeof value === 'string') {
    if (value.length > 16384) return undefined;
    try { value = JSON.parse(value); } catch { return undefined; }
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined;
  const clean = {};
  for (const [key, number] of Object.entries(value)) {
    if (METERING_KEYS.has(key) && typeof number === 'number' && Number.isFinite(number) && number >= 0) clean[key] = number;
  }
  return Object.keys(clean).length ? JSON.stringify(clean) : undefined;
}
const BRIDGE_MODEL_ALIASES = /^(gpt-codex(?:-fast|-deep)?|claude-max(?:-fast|-deep)?)$/;
function sanitizeAttributes(attributes={}) {
  const out={};
  for(const [key,value] of Object.entries(attributes)) {
    if (/^langfuse\.observation\.(usage_details|cost_details)$/.test(key)) {
      const clean = sanitizeMetering(value);
      if (clean !== undefined) out[key] = clean;
      continue;
    }
    if (/^langfuse\.(observation|trace)\.(input|output)$/.test(key)) {
      const clean = sanitizeContent(value);
      out[key] = JSON.stringify(clean);
      continue;
    }
    if (/input|output|prompt|completion|command|content|body|header|url|path|query|statement|exception|stack|secret|token(?!s)|authorization|cookie/i.test(key)
      && !/usage|tokens|token_count/i.test(key)) {out[key]=REDACTED; continue;}
    if (key.includes('metadata')) {
      if (key.endsWith('.metadata')) {
        let data=value; try {if(typeof value==='string')data=JSON.parse(value);} catch {data=null;}
        out[key]=JSON.stringify(sanitizeData(data));
      } else {
        const field=key.split('.').pop();
        out[key]=ENUM.test(field) || COUNTER.test(field) ? sanitizeData({[field]:value})[field] : REDACTED;
      }
    } else if (typeof value==='number' || typeof value==='boolean') out[key]=value;
    else if (/^(session.id|user.id|langfuse.(session|user).id)$/.test(key) && /^[a-f0-9]{64}$/.test(value)) out[key]=value;
    else if (/^(langfuse.observation.(type|level|model.name)|langfuse.trace.name|service.name|service.version|langfuse.environment|langfuse.release|mcp.tool.name)$/.test(key)
      && typeof value==='string' && /^[A-Za-z0-9_. -]{1,120}$/.test(value)) out[key]=value;
    else if (/^(id|executionId|operationId)$/.test(key) && typeof value==='string' && /^[A-Za-z0-9_-]{16,80}$/.test(value)) out[key]=value;
    else if (key==='missingStart' && typeof value==='boolean') out[key]=value;
  }
  return out;
}
function sanitizeSpan(span) {
  // Clone the export view, never mutate model messages, live spans, or another exporter.
  const clean = Object.create(Object.getPrototypeOf(span));
  Object.assign(clean, span, {attributes:sanitizeAttributes(span.attributes),events:[],links:[],status:{code:span.status?.code ?? 0}});
  clean.name = typeof span.name==='string' && /^[A-Za-z0-9_.-]{1,140}$/.test(span.name) && !/secret|password|credential|authorization/i.test(span.name) ? span.name : 'operation';
  const resource=span.resource;
  if(resource) clean.resource={...resource, attributes:sanitizeAttributes(resource.attributes)};
  return clean;
}
function protectProcessor(processor, {provider}={}) {
  return {
    onStart:(span,ctx)=>{try {processor.onStart(span,ctx);} catch {}},
    onEnd:span=>{try {
      const safe=sanitizeSpan(span);
      safe.attributes['langfuse.observation.metadata.contentMode']=contentMode();
      if(provider) {
        safe.attributes['langfuse.observation.metadata.provider']=provider;
        if (!safe.attributes['langfuse.observation.metadata.state']) {
          safe.attributes['langfuse.observation.metadata.state']=span.status?.code===2 ? 'failed':'completed';
        }
        if(safe.attributes['langfuse.observation.type']==='generation')safe.name='model.other';
        // Roko sees the OpenAI-compatible response to the very same Bridge run.
        // Keep its generation visible, but account for that run only in Bridge.
        if (provider === 'roko' && safe.attributes['langfuse.observation.type'] === 'generation' && BRIDGE_MODEL_ALIASES.test(safe.attributes['langfuse.observation.model.name'] || '')) {
          for (const key of Object.keys(safe.attributes)) {
            if (/^langfuse\.observation\.(usage_details|cost_details)$|^gen_ai\.usage\.|^llm\.token_count\./.test(key)) delete safe.attributes[key];
          }
          // Zero overrides model-price/tokenizer inference on the replica.
          safe.attributes['langfuse.observation.usage_details'] = JSON.stringify({input:0,output:0,total:0});
          safe.attributes['langfuse.observation.cost_details'] = JSON.stringify({input:0,output:0,total:0});
          safe.attributes['langfuse.observation.metadata.usageAccounting']='bridge_replica';
        }
      }
      processor.onEnd(safe);
    } catch {}},
    forceFlush:async()=>{try {await processor.forceFlush();} catch {}},
    shutdown:async()=>{try {await processor.shutdown();} catch {}},
  };
}
module.exports={sanitizeData,sanitizeContent,maskData,contentMode,sanitizeAttributes,sanitizeSpan,protectProcessor};
