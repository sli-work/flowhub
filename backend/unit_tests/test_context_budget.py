from types import SimpleNamespace

import pytest

from flowhub_api.services.context_budget import (
    ContextBudget, ContextBudgetExceeded, budget_for_model,
    enforce_message_budget, prepare_session_history,
)


def test_budget_model_overrides_provider():
    budget = budget_for_model(SimpleNamespace(max_context_tokens=16000, max_output_tokens=2000), SimpleNamespace(max_context_tokens=90000))
    assert budget.input_limit == 12400


def test_complete_prompt_and_tools_counted():
    with pytest.raises(ContextBudgetExceeded):
        enforce_message_budget([('system', 'x' * 2000), ('human', 'hi')], ContextBudget(1000, 100), tools=[{'description': 'x' * 1000}])


@pytest.mark.asyncio
async def test_short_long_history_compressed_semantically():
    calls = []
    async def summarize(messages, max_tokens):
        calls.append(messages)
        return '目标：交付。约束：采用最新用户纠正。待办：验证。'
    result = await prepare_session_history([(1,'user','old' * 1000), (2,'assistant','answer' * 1000)], budget=ContextBudget(4000, 100), summarize=summarize)
    assert calls
    assert result.marker > 0
    assert result.summary.startswith('目标')
    assert all(role != 'system' for role, _ in result.messages)


@pytest.mark.asyncio
async def test_failure_trims_and_marks_history():
    async def fail(*args):
        raise RuntimeError('unavailable')
    result = await prepare_session_history([(1,'user','x' * 5000)], budget=ContextBudget(1000, 100), summarize=fail)
    assert result.truncated
    assert '裁剪' in result.summary
    enforce_message_budget(result.messages, ContextBudget(1000,100))


@pytest.mark.asyncio
async def test_essential_current_input_is_never_silently_truncated():
    with pytest.raises(ContextBudgetExceeded):
        await prepare_session_history([], budget=ContextBudget(1000,100), essential_messages=[('human', 'x' * 5000)])


@pytest.mark.asyncio
async def test_small_history_preserves_roles_and_marker():
    result = await prepare_session_history([(1,'user','hello'),(2,'assistant','hi')], budget=ContextBudget())
    assert result.messages == [('human','hello'),('ai','hi')]
    assert result.marker == 0


@pytest.mark.asyncio
async def test_existing_summary_rewritten_not_appended_forever():
    async def summarize(messages, max_tokens):
        return '目标与约束的有界摘要'
    result = await prepare_session_history([(10,'user','recent')], summary='old' * 2000, marker=9, budget=ContextBudget(4000,100), summarize=summarize)
    assert result.summary == '目标与约束的有界摘要'
    assert result.marker == 9
    assert ('human','recent') in result.messages


@pytest.mark.asyncio
async def test_summary_exceeding_its_limit_is_not_trusted():
    async def summarize(messages, max_tokens):
        return 'x' * 5000
    result = await prepare_session_history([(1,'user','x'*3000)], budget=ContextBudget(2000,100), summarize=summarize)
    assert result.truncated
    assert len(result.summary) < 100


def test_tool_call_arguments_count_toward_budget():
    message = SimpleNamespace(type='ai', content='', additional_kwargs={}, tool_calls=[{'args': {'text': 'x'*3000}}])
    with pytest.raises(ContextBudgetExceeded):
        enforce_message_budget([message], ContextBudget(1000,100))


def test_default_budget_is_conservative():
    budget = budget_for_model()
    assert budget.context_tokens == 32000
    assert budget.output_tokens == 4096


@pytest.mark.asyncio
async def test_fit_history_reserves_real_system_and_tools():
    from flowhub_api.services.context_budget import fit_history_messages
    class Model:
        budget = ContextBudget(2000,100)
        tools = [{'description':'x'*500}]
        async def ainvoke(self, messages):
            return SimpleNamespace(content='用户要求保持原约束。')
    essential = [('system', 'x'*400), ('human','latest')]
    history = [('human','old'*500),('ai','old answer'*300)]
    result = await fit_history_messages(history, essential, Model())
    enforce_message_budget(essential + result, Model.budget, Model.tools)
    assert any('摘要' in content for _,content in result)
