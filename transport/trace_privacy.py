"""Export-only projection. Document data and exception events never leave this process."""
from __future__ import annotations
import re
import json
import os
from typing import Any, Mapping

_ALLOWED = re.compile(r'^(langfuse\.observation\.(type|level|metadata\.contentMode)|mcp\.tool\.(name|status|error_type|error_category)|provider|operation\.id|duration_ms|file\.extension|session\.id|user\.id|service\.(name|version))$')
_PRIVATE_FIELD = re.compile(r'(?:^|[_-])(?:key|token|secret|password)(?:[_-]|$)|authorization|cookie|password|passwd|secret|credential|api[_-]?key|access[_-]?token|refresh[_-]?token|private[_-]?key|^(token|headers?|env|environment|command|cmd|path|file_path|url|data|base64|bytes|buffer|artifact|artifacts|attachments?|content|text)$', re.I)


def sanitize_content(value: Any) -> Any:
    """Debug exposes bounded tool context; document bodies remain excluded."""
    if os.getenv('OBSERVABILITY_CONTENT_MODE') != 'debug':
        return '[redacted]'
    try:
        limit = max(256, min(16000, int(os.getenv('OBSERVABILITY_CONTENT_MAX_CHARS', '4000'))))
    except ValueError:
        limit = 4000
    remaining = limit
    seen: set[int] = set()

    def walk(data: Any, depth: int = 0) -> Any:
        nonlocal remaining
        if remaining <= 0 or depth > 8:
            return '[truncated]'
        if data is None or isinstance(data, (bool, int, float)):
            return data
        if isinstance(data, str):
            if len(data) > 1000000:
                return '[oversized content]'
            if data.lstrip().startswith(('{', '[')):
                try:
                    return walk(json.loads(data), depth + 1)
                except (ValueError, TypeError):
                    pass
            data = re.sub(r'([a-z][a-z0-9+.-]*://)[^\s/:@]+:[^\s/@]+@', r'\1[redacted]@', data, flags=re.I)
            safe = re.sub(r'-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?(?:-----END [^-]*PRIVATE KEY-----|$)', '[redacted]', data)
            safe = re.sub(r'data:[^\s,;]+(?:;[^,]*)?;base64,[A-Za-z0-9+/=_-]+', '[redacted]', safe, flags=re.I)
            safe = re.sub(r'\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+', '[redacted]', safe, flags=re.I)
            safe = re.sub(r'\b(?:sk-|pk-lf-|sk-lf-|ghp_|github_pat_|AIza|xox[baprs]-|ATATT|ATOA)[A-Za-z0-9_-]{8,}', '[redacted]', safe)
            safe = re.sub(r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', '[redacted]', safe)
            safe = re.sub(r'''((?:[A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)|authorization|password|passwd|secret|token|api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret)\s*[=:]\s*)(?:"[^"\n]*"|'[^'\n]*'|[^\s,;]+)''', r'\1[redacted]', safe, flags=re.I)
            safe = re.sub(r'\b(?:UEsDB|JVBERi0|iVBORw0KGgo)[A-Za-z0-9+/=_-]*', '[redacted]', safe)
            safe = re.sub(r'\b[A-Za-z0-9+/=_-]{64,}\b', '[redacted]', safe)
            safe = re.sub(r'''(?:/(?:app|home|auth|data|workspace|tmp|etc)/|[A-Za-z]:\\)[^\s"'<>]*''', '[redacted]', safe)
            result = safe[:remaining]
            remaining -= len(result)
            return result + ('[truncated]' if len(result) < len(safe) else '')
        if not isinstance(data, (dict, list, tuple)) or id(data) in seen:
            return '[redacted]'
        seen.add(id(data))
        if isinstance(data, (list, tuple)):
            return [walk(item, depth + 1) for item in data[:30]]
        output: dict[str, Any] = {}
        for key, child in list(data.items())[:60]:
            if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', key) or remaining <= 0:
                continue
            remaining -= len(key)
            output[key] = '[redacted]' if _PRIVATE_FIELD.search(key) else walk(child, depth + 1)
        return output

    return walk(value)


def sanitize_attributes(attributes: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in attributes.items():
        if key in ('langfuse.observation.input', 'langfuse.observation.output'):
            result[key] = json.dumps(sanitize_content(value), ensure_ascii=False)
            continue
        if not _ALLOWED.fullmatch(key):
            continue
        if isinstance(value, (bool, int, float)):
            result[key] = value
        elif isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_. -]{1,120}', value):
            result[key] = value
    return result


class PrivacyExporter:
    def __init__(self, exporter: Any) -> None:
        self.exporter = exporter

    def export(self, spans: Any) -> Any:
        from opentelemetry.sdk.trace import ReadableSpan
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.trace import Status
        safe = [ReadableSpan(
            name=span.name if re.fullmatch(r'mcp\.[A-Za-z0-9_]{1,100}', span.name) else 'mcp.operation',
            context=span.context, parent=span.parent,
            resource=Resource(sanitize_attributes(span.resource.attributes)),
            attributes=sanitize_attributes(span.attributes or {}), events=(), links=(),
            status=Status(span.status.status_code), kind=span.kind,
            start_time=span.start_time, end_time=span.end_time,
            instrumentation_scope=span.instrumentation_scope,
        ) for span in spans]
        return self.exporter.export(safe)

    def shutdown(self) -> None:
        self.exporter.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self.exporter.force_flush(timeout_millis)
