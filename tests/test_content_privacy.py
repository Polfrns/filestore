import json
from unittest.mock import patch

import pytest

from transport.trace_privacy import sanitize_content, sanitize_attributes, PrivacyExporter


@pytest.mark.parametrize('mode', ['redacted', 'debug', 'invalid'])
def test_export_modes_preserve_queries_but_never_documents_or_credentials(monkeypatch, mode):
    monkeypatch.setenv('OBSERVABILITY_CONTENT_MODE', mode)
    source = {'query': 'VISIBLE_QUERY password=PRIVATE_PASSWORD',
              'content': 'PRIVATE_DOCUMENT', 'text': 'PRIVATE_PDF_TEXT',
              'data': 'UEsDBPRIVATE_DOCX', 'Authorization': 'Bearer PRIVATE_AUTH',
              'command': 'echo PRIVATE_COMMAND', 'path': '/data/PRIVATE_PATH',
              'CLAUDE_BRIDGE_KEY': 'PRIVATE_BRIDGE_KEY', 'AWS_ACCESS_KEY_ID': 'PRIVATE_AWS_KEY',
              'connection': 'postgres://PRIVATE_USER:PRIVATE_PASS@db/test'}
    before = json.dumps(source)
    result = sanitize_attributes({'langfuse.observation.input': before,
                                  'langfuse.observation.output': 'VISIBLE_RESULT'})
    exported = json.dumps(result)
    assert 'PRIVATE_' not in exported
    assert json.dumps(source) == before
    if mode == 'debug':
        assert 'VISIBLE_QUERY' in exported and 'VISIBLE_RESULT' in exported
    else:
        assert 'VISIBLE_' not in exported


def test_debug_has_bounded_total_text_and_handles_cycles(monkeypatch):
    monkeypatch.setenv('OBSERVABILITY_CONTENT_MODE', 'debug')
    monkeypatch.setenv('OBSERVABILITY_CONTENT_MAX_CHARS', '256')
    value = {'query': 'a ' * 5000}
    value['self'] = value
    assert len(json.dumps(sanitize_content(value))) < 600
    assert sanitize_content('x' * 1000001) == '[oversized content]'


async def test_read_file_result_is_redacted_even_in_debug(monkeypatch):
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.sampling import ALWAYS_ON
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from tests.test_observability import Exporter, Registry
    from transport.langfuse_tracing import traced_tool
    monkeypatch.setenv('OBSERVABILITY_CONTENT_MODE', 'debug')
    exporter = Exporter()
    provider = TracerProvider(sampler=ALWAYS_ON)
    provider.add_span_processor(SimpleSpanProcessor(PrivacyExporter(exporter)))
    async def read_file(path: str) -> str:
        return 'PRIVATE_DOCUMENT'
    with patch('transport.langfuse_tracing.get_tool_tracer', return_value=provider.get_tracer('test')):
        assert await traced_tool(Registry())(read_file)('private.docx') == 'PRIVATE_DOCUMENT'
    assert 'PRIVATE_DOCUMENT' not in json.dumps(dict(exporter.spans[0].attributes))
    provider.shutdown()
