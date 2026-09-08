import asyncio

import pytest

from flowhub_api.services.runtime_quality import review_candidate


@pytest.mark.asyncio
async def test_quality_judge_timeout_finishes_with_human_review_instead_of_hanging():
    calls = 0

    async def stalled_judge(*_args):
        nonlocal calls
        calls += 1
        await asyncio.Event().wait()

    result = await review_candidate(
        question="检查结论", evidence="已知证据", answer="可用结论",
        policy={"review": True, "max_attempts": 2}, attempt=1,
        deterministic_issues=[], invoke_review=stalled_judge,
        parse_review=lambda _value: (True, [], "passed"), review_timeout_seconds=0.01,
    )

    assert calls == 2
    assert result["quality_status"] == "needs_human_review"
    assert result["validation_issues"] == ["质量校验服务不可用，请人工复核。"]
