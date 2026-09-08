"""Shared bounded quality gate for chat and workflow artifacts."""
from __future__ import annotations
import asyncio
import hashlib


QUALITY_REVIEW_TIMEOUT_SECONDS = 15
MAX_JUDGE_RETRIES = 2


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def answer_chunks(answer: str, size: int = 8000) -> list[str]:
    # Every character is reviewed, including the tail of long deliverables.
    return [answer[i:i + size] for i in range(0, len(answer), size)] or ['']


async def review_candidate(*, question, evidence, answer, policy, attempt,
                           deterministic_issues, invoke_review, parse_review,
                           review_timeout_seconds: float = QUALITY_REVIEW_TIMEOUT_SECONDS):
    issues = list(deterministic_issues)
    if not answer.strip():
        issues.insert(0, '回答为空，请输出可直接使用的完整结果。')
    if issues:
        return {'validation_issues': issues[:3], 'quality_status':
                'needs_revision' if attempt < int(policy['max_attempts']) else 'needs_human_review',
                'retry_judge': False}
    if not policy['review']:
        return {'validation_issues': [], 'quality_status': 'not_checked', 'retry_judge': False}
    for chunk in answer_chunks(answer):
        status = 'unavailable'
        # 质量模型是辅助校验，不能把已经生成的回答长时间卡在等待中。
        # 超时后保留回答并标记人工复核；最多两次短请求，不进入无界等待。
        for _ in range(MAX_JUDGE_RETRIES):
            try:
                review = await asyncio.wait_for(
                    invoke_review(question, evidence, chunk), timeout=review_timeout_seconds,
                )
                passed, found, status = parse_review(review)
            except Exception:
                passed, found, status = False, ['质量校验服务不可用，请人工复核。'], 'unavailable'
            if status != 'unavailable':
                break
        if status == 'unavailable':
            return {'validation_issues': found, 'quality_status': 'needs_human_review', 'retry_judge': False}
        if not passed:
            issues.extend(found or ["质量核验未通过，请人工复核。"])
    return {'validation_issues': issues[:3], 'quality_status': (
        'passed' if not issues else 'needs_revision' if attempt < int(policy['max_attempts']) else 'needs_human_review'),
        'retry_judge': False}
