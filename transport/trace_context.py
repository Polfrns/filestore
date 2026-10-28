"""Service trace context is authenticated separately from MCP authorization."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator, Mapping

@dataclass(frozen=True)
class TraceIdentity:
    session_id: str
    user_id: str
    traceparent: str
    tracestate: str

current_trace: ContextVar[TraceIdentity | None] = ContextVar('filestore_trace', default=None)


def verify_trace(headers: Mapping[bytes, bytes], key: str | None = None, now: float | None = None) -> TraceIdentity | None:
    key = key if key is not None else os.getenv('OBSERVABILITY_TRACE_SECRET', '')
    raw = headers.get(b'x-observability-context', b'')
    signature = headers.get(b'x-observability-signature', b'')
    if len(key) < 32 or len(raw) > 2048 or not re.fullmatch(rb'[a-f0-9]{64}', signature):
        return None
    expected = hmac.new(key.encode(), raw, hashlib.sha256).hexdigest().encode()
    if not hmac.compare_digest(expected, signature):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(raw + b'=' * (-len(raw) % 4)))
        timestamp = data['timestamp']
        if type(timestamp) is not int or abs((time.time() if now is None else now) * 1000 - timestamp) > 60000:
            return None
        if not re.fullmatch(r'00-(?!0{32})[a-f0-9]{32}-(?!0{16})[a-f0-9]{16}-0[01]', data['traceparent']):
            return None
        if not all(re.fullmatch(r'[a-f0-9]{64}', data[k]) for k in ('sessionId', 'userId')):
            return None
        state = data.get('tracestate', '')
        if not isinstance(state, str) or len(state) > 512:
            return None
        return TraceIdentity(data['sessionId'], data['userId'], data['traceparent'], state)
    except (ValueError, TypeError, KeyError):
        return None


@contextmanager
def incoming_trace(headers: Mapping[bytes, bytes]) -> Iterator[None]:
    identity = verify_trace(headers)
    token = current_trace.set(identity)
    otel_token = None
    try:
        if identity:
            try:
                from opentelemetry.context import attach
                from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
                ctx = TraceContextTextMapPropagator().extract({'traceparent': identity.traceparent, 'tracestate': identity.tracestate})
                otel_token = attach(ctx)
            except Exception:
                pass  # Optional tracing cannot affect authenticated requests.
        yield
    finally:
        if otel_token is not None:
            from opentelemetry.context import detach
            detach(otel_token)
        current_trace.reset(token)
