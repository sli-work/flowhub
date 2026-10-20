from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command, interrupt

from flowhub_api.services import expert_runtime


class ToolModel:
    def __init__(self, **kwargs):
        pass

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        if any(isinstance(message, ToolMessage) for message in messages):
            return AIMessage(content='{"summary":"structured delivery"}')
        return AIMessage(content='', tool_calls=[{'id': 'stable-call', 'name': 'archive_test', 'args': {}}])


def make_graph(monkeypatch, callback, checkpointer=None):
    monkeypatch.setattr(expert_runtime, 'ChatOpenAI', ToolModel)
    monkeypatch.setattr(expert_runtime, 'decrypt_secret', lambda _: 'test-key')
    bundle = SimpleNamespace(tools=[StructuredTool.from_function(coroutine=callback, name='archive_test',
        description='Test scoped archive execution')], traces=[], evidence_context=[])
    factory = AsyncMock(return_value=bundle)
    graph = expert_runtime.build_graph(SimpleNamespace(api_key='x', base_url='https://example.test', max_context_tokens=32000),
        SimpleNamespace(model='test', max_context_tokens=None, max_output_tokens=None), 'Generate delivery JSON',
        output_schema=[{'key': 'summary', 'type': 'textarea', 'label': 'Summary', 'required': True}],
        business_tools_factory=factory, quality_mode='fast').compile(checkpointer=checkpointer)
    return graph, factory


@pytest.mark.asyncio
async def test_business_tools_execute_without_repo_or_attachment_bundles(monkeypatch):
    calls = []
    async def archive():
        """Archive only authorized material."""
        calls.append('archived')
        return 'archive stored'
    graph, factory = make_graph(monkeypatch, archive)
    result = await graph.ainvoke({'prompt': 'Produce summary', 'write_intent': False})
    assert calls == ['archived']
    assert result['output'] == '{"summary":"structured delivery"}'
    factory.assert_awaited_once()


@pytest.mark.asyncio
async def test_business_tool_approval_interrupt_is_resumable_without_preapproval_write(monkeypatch):
    writes = []
    approved = False
    async def archive():
        """Await approval, then archive."""
        if not approved:
            interrupt({'toolInvocationId': 'ti-test'})
        writes.append('archived')
        return 'archive stored'
    graph, _ = make_graph(monkeypatch, archive, InMemorySaver())
    config = {'configurable': {'thread_id': 'tool-approval-test'}}
    first = await graph.ainvoke({'prompt': 'Produce summary', 'write_intent': False}, config)
    assert first['__interrupt__'][0].value['toolInvocationId'] == 'ti-test'
    assert writes == []
    approved = True
    resumed = await graph.ainvoke(Command(resume=True), config)
    assert writes == ['archived']
    assert resumed['output'] == '{"summary":"structured delivery"}'
