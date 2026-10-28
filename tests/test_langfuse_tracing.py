"""Regression tests for optional MCP tracing (no external Langfuse needed)."""
import asyncio
from unittest.mock import patch

from transport.langfuse_tracing import traced_tool


class Registry:
    def tool(self):
        return lambda function: function


def test_tracing_disabled_preserves_result_and_signature():
    async def example(value: str) -> str:
        return value

    wrapped = traced_tool(Registry())(example)
    with patch('transport.langfuse_tracing.get_tool_tracer', return_value=None):
        assert asyncio.run(wrapped('ok')) == 'ok'
    assert wrapped.__name__ == 'example'


def test_tracing_disabled_preserves_errors():
    async def example() -> str:
        raise ValueError('secret information')

    wrapped = traced_tool(Registry())(example)
    with patch('transport.langfuse_tracing.get_tool_tracer', return_value=None):
        try:
            asyncio.run(wrapped())
        except ValueError as error:
            assert str(error) == 'secret information'
        else:
            raise AssertionError('Expected ValueError')
