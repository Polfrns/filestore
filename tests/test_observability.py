from __future__ import annotations
import asyncio
import base64
import hashlib
import hmac
import inspect
import json
from unittest.mock import patch

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult

from transport.langfuse_tracing import traced_tool
from transport.trace_context import current_trace, incoming_trace, verify_trace
from transport.trace_privacy import PrivacyExporter

class Registry:
    def tool(self):
        return lambda function: function

class Exporter:
    def __init__(self):
        self.spans = []
    def export(self, spans):
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS
    def shutdown(self):
        pass
    def force_flush(self, timeout_millis=30000):
        return True


def signed_headers(key='t'*32, now=1000):
    payload={'timestamp':now*1000,'traceparent':'00-'+'1'*32+'-'+'2'*16+'-01','tracestate':'',
             'sessionId':'3'*64,'userId':'4'*64}
    raw=base64.urlsafe_b64encode(json.dumps(payload,separators=(',',':')).encode()).rstrip(b'=')
    return {b'x-observability-context':raw,b'x-observability-signature':hmac.new(key.encode(),raw,hashlib.sha256).hexdigest().encode()}


def test_service_hmac_and_expiry(monkeypatch):
    headers=signed_headers()
    assert verify_trace(headers,'t'*32,1000).session_id == '3'*64
    assert verify_trace(headers,'t'*32,1061) is None
    assert verify_trace(headers,'x'*32,1000) is None
    assert verify_trace({b'traceparent':b'00-' + b'1'*32 + b'-' + b'2'*16 + b'-01'},'t'*32,1000) is None
    assert current_trace.get() is None

@pytest.mark.parametrize('failure',[None,'error','cancel'])
async def test_real_spans_preserve_results_close_and_suppress_document_contents(failure):
    target=Exporter();provider=TracerProvider(sampler=ALWAYS_ON);provider.add_span_processor(SimpleSpanProcessor(PrivacyExporter(target)))
    tracer=provider.get_tracer('test')
    calls=[]
    async def read_file(path: str) -> str:
        calls.append(path)
        if failure=='error': raise ValueError('SECRET_ERROR')
        if failure=='cancel': raise asyncio.CancelledError('SECRET_CANCEL')
        return 'SECRET_DOCUMENT_CONTENT'
    wrapped=traced_tool(Registry())(read_file)
    assert inspect.signature(wrapped)==inspect.signature(read_file)
    with patch('transport.langfuse_tracing.get_tool_tracer',return_value=tracer):
        if failure:
            with pytest.raises(ValueError if failure=='error' else asyncio.CancelledError): await wrapped('private.docx')
        else: assert await wrapped('private.docx')=='SECRET_DOCUMENT_CONTENT'
    assert calls==['private.docx']
    assert len(target.spans)==1
    span=target.spans[0]
    assert span.name=='mcp.read_file'
    assert span.end_time is not None
    assert span.attributes['mcp.tool.status']=={None:'completed','error':'failed','cancel':'cancelled'}[failure]
    assert span.attributes['file.extension']=='.docx'
    assert not span.events
    assert 'SECRET_' not in json.dumps(dict(span.attributes))
    provider.shutdown()

async def test_tracer_start_failure_does_not_skip_or_repeat_tool():
    class Broken:
        def start_span(self,*args,**kwargs): raise RuntimeError('offline')
    calls=[]
    async def example(): calls.append(1); return 'ok'
    with patch('transport.langfuse_tracing.get_tool_tracer',return_value=Broken()):
        assert await traced_tool(Registry())(example)()=='ok'
    assert calls==[1]

async def test_incoming_context_has_w3c_parent_and_session_and_is_reset(monkeypatch):
    import time
    monkeypatch.setenv('OBSERVABILITY_TRACE_SECRET','t'*32)
    target=Exporter();provider=TracerProvider(sampler=ALWAYS_ON);provider.add_span_processor(SimpleSpanProcessor(PrivacyExporter(target)))
    async def list_files(): return 'ok'
    with incoming_trace(signed_headers(now=int(time.time()))):
        assert current_trace.get().session_id=='3'*64
        with patch('transport.langfuse_tracing.get_tool_tracer',return_value=provider.get_tracer('test')):
            assert await traced_tool(Registry())(list_files)()=='ok'
    assert current_trace.get() is None
    span=target.spans[0]
    assert span.context.trace_id==int('1'*32,16)
    assert span.parent.span_id==int('2'*16,16)
    assert span.attributes['session.id']=='3'*64
    provider.shutdown()

async def test_mcp_registry_has_same_twelve_public_tools():
    from transport.server import mcp
    tools=await mcp.list_tools()
    names={tool.name for tool in tools}
    assert len(names)==12
    assert {'list_filestores','list_files','read_file','create_file','delete_file'} <= names

async def test_authentication_precedes_trace_context(monkeypatch):
    from transport.middleware import APIKeyMiddleware
    called=[]
    async def app(scope,receive,send): called.append(current_trace.get())
    async def receive(): return {'type':'http.request','body':b''}
    sent=[]
    async def send(event): sent.append(event)
    monkeypatch.setenv('OBSERVABILITY_TRACE_SECRET','t'*32)
    with patch('transport.middleware.resolve_user',return_value=None):
        await APIKeyMiddleware(app)({'type':'http','headers':[(b'authorization',b'Bearer invalid'),*signed_headers().items()]},receive,send)
    assert not called and sent[0]['status']==401
    assert current_trace.get() is None
