"""Optional fail-open MCP observations. No arguments, results, or exception text exported."""
from __future__ import annotations

import asyncio
import atexit
import base64
import functools
import inspect
import json
import logging
import os
import time
import uuid
from pathlib import PurePosixPath
from typing import Any

from .trace_context import current_trace
from .trace_privacy import PrivacyExporter, sanitize_content

logger = logging.getLogger(__name__)
_provider = None
_tracer = None
_configured = False


def get_tool_tracer() -> Any:
    global _configured, _provider, _tracer
    if _configured:
        return _tracer
    _configured = True
    url = os.getenv('LANGFUSE_BASE_URL', '').strip().rstrip('/')
    public = os.getenv('LANGFUSE_PUBLIC_KEY', '').strip()
    secret = os.getenv('LANGFUSE_SECRET_KEY', '').strip()
    if os.getenv('LANGFUSE_TRACING_ENABLED') == 'false' or not (url and public and secret):
        return None
    try:
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        credentials = base64.b64encode(f'{public}:{secret}'.encode()).decode('ascii')
        exporter = OTLPSpanExporter(endpoint=f'{url}/api/public/otel/v1/traces',
            headers={'Authorization': f'Basic {credentials}', 'x-langfuse-ingestion-version': '4'}, timeout=5)
        _provider = TracerProvider(resource=Resource.create({'service.name': 'leedeo-filestore-mcp'}))
        _provider.add_span_processor(BatchSpanProcessor(PrivacyExporter(exporter), max_queue_size=1024, max_export_batch_size=128))
        _tracer = _provider.get_tracer('leedeo-filestore.mcp', '0.7.2')
        atexit.register(_provider.shutdown)
    except Exception:
        logger.warning('MCP tracing unavailable')
    return _tracer


def _attribute(span: Any, key: str, value: Any) -> None:
    try:
        span.set_attribute(key, value)
    except Exception:
        pass


def traced_tool(registry: Any) -> Any:
    def decorator(function: Any) -> Any:
        @functools.wraps(function)
        async def instrumented(*args: Any, **kwargs: Any) -> Any:
            span = None
            otel_token = None
            started = time.monotonic()
            try:
                tracer = get_tool_tracer()
                if tracer is not None:
                    from opentelemetry.context import attach
                    from opentelemetry.trace import set_span_in_context
                    span = tracer.start_span(f'mcp.{function.__name__}', record_exception=False, set_status_on_exception=False)
                    otel_token = attach(set_span_in_context(span))
                    _attribute(span, 'langfuse.observation.type', 'tool')
                    _attribute(span, 'langfuse.observation.metadata.contentMode', 'debug' if os.getenv('OBSERVABILITY_CONTENT_MODE') == 'debug' else 'redacted')
                    _attribute(span, 'provider', 'filestore')
                    _attribute(span, 'mcp.tool.name', function.__name__)
                    _attribute(span, 'operation.id', uuid.uuid4().hex)
                    _attribute(span, 'mcp.tool.status', 'started')
                    identity = current_trace.get()
                    if identity:
                        _attribute(span, 'session.id', identity.session_id)
                        _attribute(span, 'user.id', identity.user_id)
                    bound = inspect.signature(function).bind_partial(*args, **kwargs)
                    _attribute(span, 'langfuse.observation.input', json.dumps(sanitize_content(bound.arguments), ensure_ascii=False))
                    path = bound.arguments.get('path')
                    extension = PurePosixPath(path).suffix.lower() if isinstance(path, str) else ''
                    if extension and len(extension) <= 12 and extension[1:].isalnum():
                        _attribute(span, 'file.extension', extension)
            except Exception:
                logger.warning('MCP observation start unavailable')
            try:
                result = await function(*args, **kwargs)
            except BaseException as error:
                if span is not None:
                    cancelled = isinstance(error, asyncio.CancelledError)
                    _attribute(span, 'mcp.tool.status', 'cancelled' if cancelled else 'failed')
                    _attribute(span, 'mcp.tool.error_type', type(error).__name__)
                    category = 'cancelled' if cancelled else 'timeout' if isinstance(error, TimeoutError) else 'permission_denied' if isinstance(error, PermissionError) else 'missing_file' if isinstance(error, FileNotFoundError) else 'tool_failed'
                    _attribute(span, 'mcp.tool.error_category', category)
                    try:
                        from opentelemetry.trace import Status, StatusCode
                        span.set_status(Status(StatusCode.ERROR))
                    except Exception:
                        pass
                raise
            else:
                if span is not None:
                    _attribute(span, 'mcp.tool.status', 'completed')
                    # read_file can return a whole document as a plain string.
                    output = {'content': '[document redacted]'} if function.__name__ == 'read_file' else result
                    _attribute(span, 'langfuse.observation.output', json.dumps(sanitize_content(output), ensure_ascii=False))
                return result
            finally:
                if span is not None:
                    _attribute(span, 'duration_ms', (time.monotonic() - started) * 1000)
                    try:
                        span.end()
                    except Exception:
                        pass
                if otel_token is not None:
                    try:
                        from opentelemetry.context import detach
                        detach(otel_token)
                    except Exception:
                        pass
        return registry.tool()(instrumented)
    return decorator
