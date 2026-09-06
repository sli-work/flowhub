from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flowhub_api.services.context_budget import ContextBudget, ContextBudgetExceeded
from flowhub_api.services.runtime_model import BudgetedModel, make_model
from flowhub_api.services.runtime_quality import answer_chunks, content_hash, review_candidate


def test_chunks_cover_tail_and_hash_tracks_changes():
    text = 'a' * 20000 + 'tail'
    assert ''.join(answer_chunks(text)) == text
    assert content_hash(text) != content_hash(text + '!')


@pytest.mark.asyncio
async def test_judge_outage_retries_only_judge_then_requires_human():
    judge = AsyncMock(side_effect=RuntimeError('offline'))
    result = await review_candidate(question='q', evidence='e', answer='a', policy={'review':True,'max_attempts':3}, attempt=1, deterministic_issues=[], invoke_review=judge, parse_review=lambda x:x)
    assert judge.await_count == 2
    assert result['quality_status'] == 'needs_human_review'
    assert not result['retry_judge']


@pytest.mark.asyncio
async def test_fast_still_blocks_deterministic_issues():
    judge = AsyncMock()
    result = await review_candidate(question='q', evidence='e', answer='a', policy={'review':False,'max_attempts':1}, attempt=1, deterministic_issues=['invalid schema'], invoke_review=judge, parse_review=lambda x:x)
    assert result['quality_status'] == 'needs_human_review'
    judge.assert_not_called()


@pytest.mark.asyncio
async def test_fast_is_explicitly_unchecked():
    result = await review_candidate(question='q', evidence='e', answer='a', policy={'review':False,'max_attempts':1}, attempt=1, deterministic_issues=[], invoke_review=AsyncMock(), parse_review=lambda x:x)
    assert result['quality_status'] == 'not_checked'


@pytest.mark.asyncio
async def test_tail_error_blocks_long_answer():
    async def judge(question, evidence, chunk):
        return (False,['tail error'],'ok') if 'wrong' in chunk else (True,[],'ok')
    result = await review_candidate(question='q', evidence='e', answer='a'*17000+'wrong', policy={'review':True,'max_attempts':3}, attempt=1, deterministic_issues=[], invoke_review=judge, parse_review=lambda x:x)
    assert result['quality_status'] == 'needs_revision'
    assert result['validation_issues'] == ['tail error']


@pytest.mark.asyncio
async def test_model_rejects_before_network():
    inner = SimpleNamespace(ainvoke=AsyncMock())
    model = BudgetedModel(inner,ContextBudget(1000,100))
    with pytest.raises(ContextBudgetExceeded):
        await model.ainvoke([('human','x'*2000)])
    inner.ainvoke.assert_not_called()


@pytest.mark.asyncio
async def test_stream_closed_when_caller_cancels():
    closed = []
    async def stream(messages):
        try:
            yield 'first'
            yield 'second'
        finally:
            closed.append(True)
    model = BudgetedModel(SimpleNamespace(astream=stream), ContextBudget())
    result = model.astream([('human','hi')])
    assert await anext(result) == 'first'
    await result.aclose()
    assert closed == [True]


def test_model_factory_reserves_output_and_retries():
    captures = []
    def factory(**kwargs):
        captures.append(kwargs)
        return object()
    make_model(factory, SimpleNamespace(model='unknown',max_context_tokens=12000,max_output_tokens=2000), SimpleNamespace(base_url='http://local'), 'placeholder', retries=0)
    assert captures[0]['max_tokens'] == 2000
    assert captures[0]['max_retries'] == 0


@pytest.mark.asyncio
async def test_judge_rejection_without_issues_never_passes():
    async def judge(*args):
        return False, [], 'ok'
    result = await review_candidate(question='q', evidence='e', answer='a', policy={'review':True,'max_attempts':3}, attempt=1, deterministic_issues=[], invoke_review=judge, parse_review=lambda x:x)
    assert result['quality_status'] == 'needs_revision'
    assert result['validation_issues']
