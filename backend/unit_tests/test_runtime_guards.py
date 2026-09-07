from types import SimpleNamespace
import pytest
from flowhub_api.services.expert_runtime import run_repo_tool_loop, build_graph

@pytest.mark.asyncio
async def test_unknown_tools_exhaust_budget_and_summarize():
    class Model:
        rounds = 0
        def bind_tools(self, tools): return self
        async def ainvoke(self, messages):
            self.rounds += 1
            return SimpleNamespace(content='', tool_calls=[{'name': 'unknown', 'id': str(self.rounds), 'args': {}}])
    model = Model()
    bundle = SimpleNamespace(tools=[SimpleNamespace(name='known')])
    answer, calls = await run_repo_tool_loop(model, [('human', 'test')], bundle, max_calls=2)
    assert model.rounds <= 4
    assert calls == 2
    assert answer

@pytest.mark.asyncio
async def test_fast_still_rejects_empty_output(monkeypatch):
    class Model:
        def __init__(self, **kw): pass
        async def ainvoke(self, messages): return SimpleNamespace(content='')
    monkeypatch.setattr('flowhub_api.services.expert_runtime.ChatOpenAI', Model)
    monkeypatch.setattr('flowhub_api.services.expert_runtime.decrypt_secret', lambda x: x)
    graph = build_graph(SimpleNamespace(base_url='', api_key=''), SimpleNamespace(model='test'), 'test', quality_mode='fast').compile()
    result = await graph.ainvoke({'prompt': 'test'})
    assert result['quality_status'] == 'needs_human_review'

@pytest.mark.asyncio
async def test_judge_unavailable_never_regenerates(monkeypatch):
    calls = []
    class Model:
        def __init__(self, **kw): pass
        async def ainvoke(self, messages):
            calls.append(messages)
            return SimpleNamespace(content='answer' if len(calls) == 1 else 'invalid judge')
    monkeypatch.setattr('flowhub_api.services.expert_runtime.ChatOpenAI', Model)
    monkeypatch.setattr('flowhub_api.services.expert_runtime.decrypt_secret', lambda x: x)
    graph = build_graph(SimpleNamespace(base_url='', api_key=''), SimpleNamespace(model='test'), 'test').compile()
    result = await graph.ainvoke({'prompt': 'test'})
    assert result['attempt'] == 1
    assert result['quality_status'] == 'needs_human_review'
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_schema_output_gets_one_dedicated_format_repair(monkeypatch):
    calls = []

    class Model:
        def __init__(self, **kw): pass

        async def ainvoke(self, messages):
            calls.append(messages)
            prompt = str(messages[-1])
            if '任务：JSON 格式修复' in prompt:
                return SimpleNamespace(content='{"summary":"已按原文归位"}')
            return SimpleNamespace(content='这里是正文，不是 JSON')

    monkeypatch.setattr('flowhub_api.services.expert_runtime.ChatOpenAI', Model)
    monkeypatch.setattr('flowhub_api.services.expert_runtime.decrypt_secret', lambda x: x)
    graph = build_graph(SimpleNamespace(base_url='', api_key=''), SimpleNamespace(model='test'), 'test',
                        quality_mode='fast', output_schema=[{'key': 'summary', 'label': '摘要', 'type': 'input', 'required': True}]).compile()
    result = await graph.ainvoke({'prompt': 'test'})

    assert result['output'] == '{"summary":"已按原文归位"}'
    assert result['format_status'] == 'repaired'
    assert result['quality_status'] == 'not_checked'
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_schema_output_stops_after_one_failed_format_repair(monkeypatch):
    calls = []

    class Model:
        def __init__(self, **kw): pass

        async def ainvoke(self, messages):
            calls.append(messages)
            prompt = str(messages[-1])
            return SimpleNamespace(content='修复结果仍然不是 JSON' if '任务：JSON 格式修复' in prompt else '初始原文不是 JSON')

    monkeypatch.setattr('flowhub_api.services.expert_runtime.ChatOpenAI', Model)
    monkeypatch.setattr('flowhub_api.services.expert_runtime.decrypt_secret', lambda x: x)
    graph = build_graph(SimpleNamespace(base_url='', api_key=''), SimpleNamespace(model='test'), 'test',
                        quality_mode='fast', output_schema=[{'key': 'summary', 'label': '摘要', 'type': 'input', 'required': True}]).compile()
    result = await graph.ainvoke({'prompt': 'test'})

    assert result['output'] == '初始原文不是 JSON'
    assert result['format_status'] == 'invalid'
    assert result['quality_status'] == 'needs_human_review'
    assert len(calls) == 2
