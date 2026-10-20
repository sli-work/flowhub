"""Transactional deterministic archive automation with leased durable jobs."""
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from uuid import uuid4

from sqlalchemy import or_, select, update

from flowhub_api.db.session import SessionFactory
from flowhub_api.models import ExpertRun, ExpertVersion, TaskItem, User
from flowhub_api.services.document_archive import (
    authorize_archive, frozen_canvas, snapshot_sources, summary_for_run, validated_summary,
)

LEASE_SECONDS = 60


def event_key(task, event, rule, run, payload, backfill_key):
    value = {'task': task.id, 'event': event, 'rule': rule, 'run': run.id if run else None,
             'generation': run.execution_generation if run else 0, 'payload': payload, 'backfill': backfill_key}
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


async def validate_automation_rule(session, rule, expert_version_id):
    from flowhub_api.models.tool import ToolDefinition, ToolVersion

    expert = await session.get(ExpertVersion, expert_version_id)
    bindings = (expert.tool_bindings or []) if expert is not None else []
    if not any(binding.get('tool_version_id') == rule.get('toolVersionId')
               and binding.get('scope', 'current_work_item') == 'current_work_item' for binding in bindings):
        raise ValueError('Automation tool requires the pinned Expert grant')
    version = await session.get(ToolVersion, rule.get('toolVersionId'))
    definition = await session.get(ToolDefinition, version.tool_id) if version else None
    if (version is None or version.status != 'published' or definition is None
            or definition.status != 'active' or definition.source != 'native'
            or (version.config_json or {}).get('adapter') != 'flowhub_document_archive'):
        raise ValueError('Automation only permits active published native archive tools')
    return version


async def verify_frozen_sources(session, task, frozen):
    current = await snapshot_sources(session, task)
    by_id = {source['id']: source for source in current}
    for source in frozen:
        if by_id.get(source['id']) != source:
            raise ValueError('Frozen adopted archive source changed; explicitly reschedule the automation')


async def schedule_automation(session, *, task, event, actor, expert_version_id, run=None, backfill_key=None):
    """Freeze adopted references in the caller's task/run transaction; do not commit."""
    from flowhub_api.models.tool import ToolAutomationJob

    if event not in {'node_entered', 'expert_output_validated'}:
        raise ValueError('Unsupported automation event')
    canvas = await frozen_canvas(session, task)
    node = next((node for node in canvas.nodes if node['id'] == task.node_id), {})
    rules = (((node.get('cfg') or {}).get('automation') or {}).get('rules') or [])
    selected = [dict(rule) for rule in rules if rule.get('enabled', True) and rule.get('event') == event]
    if not selected:
        return []
    await authorize_archive(session, task, actor)
    for rule in selected:
        await validate_automation_rule(session, rule, expert_version_id)
    payload = {'sources': []}
    try:
        payload['sources'] = await snapshot_sources(session, task)
        if event == 'expert_output_validated':
            content = await summary_for_run(session, task, run)
            if content is None:
                raise ValueError('Summary format: validated run has no summary document body')
            await validated_summary(session, task, run, content)
            payload['summary_content'] = content
    except Exception as exc:
        # Workflow progression remains available; operators see a durable failed job.
        payload['freeze_error'] = str(exc)[:500]
    jobs = []
    for rule in selected:
        key = event_key(task, event, rule, run, payload, backfill_key)
        job_id = 'ta' + key[:30]
        job = await session.get(ToolAutomationJob, job_id)
        if job is None:
            job = ToolAutomationJob(id=job_id, event_key=key, task_id=task.id,
                run_id=run.id if run else None, generation=run.execution_generation if run else 0,
                expert_version_id=expert_version_id, rule_json=rule, payload_json=payload,
                requested_by=actor.id, status='failed' if payload.get('freeze_error') else 'queued',
                error=payload.get('freeze_error', ''), result_json={}, attempts=0, claim_token='',
                created_at=datetime.now(UTC).isoformat(), finished_at='')
            session.add(job)
        elif job.status == 'failed' and not payload.get('freeze_error'):
            job.status, job.error, job.finished_at = 'queued', '', ''
        jobs.append(job)
    await session.flush()
    return jobs


async def _claim_job():
    from flowhub_api.models.tool import ToolAutomationJob

    async with SessionFactory() as session:
        async with session.begin():
            now = datetime.now(UTC)
            job = (await session.execute(select(ToolAutomationJob).where(or_(
                ToolAutomationJob.status == 'queued',
                (ToolAutomationJob.status == 'running') & (ToolAutomationJob.lease_until < now),
            )).order_by(ToolAutomationJob.created_at, ToolAutomationJob.id)
                .with_for_update(skip_locked=True).limit(1))).scalar_one_or_none()
            if job is None:
                return None
            token = uuid4().hex
            job.status, job.claim_token = 'running', token
            job.lease_until, job.attempts = now + timedelta(seconds=LEASE_SECONDS), job.attempts + 1
            return job.id, token


async def _execute_claim(session, job):
    from flowhub_api.services.tool_execution import execute_tool

    task = await session.get(TaskItem, job.task_id, with_for_update=True, populate_existing=True)
    user = await session.get(User, job.requested_by)
    run = await session.get(ExpertRun, job.run_id) if job.run_id else None
    if task is None or user is None:
        raise ValueError('Automation task or original actor is missing')
    if job.run_id and (run is None or run.execution_generation != job.generation
                       or run.expert_version_id != job.expert_version_id or run.requested_by != user.id):
        raise ValueError('Automation run generation or actor changed')
    await authorize_archive(session, task, user)
    await validate_automation_rule(session, job.rule_json, job.expert_version_id)
    payload = job.payload_json or {}
    if payload.get('freeze_error'):
        raise ValueError(payload['freeze_error'])
    await verify_frozen_sources(session, task, payload.get('sources', []))
    arguments = {'source_document_ids': [source['id'] for source in payload.get('sources', [])]}
    if 'summary_content' in payload:
        await validated_summary(session, task, run, payload['summary_content'])
        arguments['summary_content'] = payload['summary_content']
    return await execute_tool(session, user=user, tool_version_id=job.rule_json['toolVersionId'],
        arguments=arguments, expert_version_id=job.expert_version_id, task=task, run=run,
        idempotency_key=f'automation:{job.event_key}', source='automation', frozen_sources=payload.get('sources', []))


async def process_one_automation_job():
    """Claim in a short transaction, then fence all archive effects behind its lock."""
    from flowhub_api.models.tool import ToolAutomationJob

    claim = await _claim_job()
    if claim is None:
        return False
    job_id, token = claim
    try:
        async with SessionFactory() as session:
            async with session.begin():
                job = (await session.execute(select(ToolAutomationJob).where(
                    ToolAutomationJob.id == job_id, ToolAutomationJob.claim_token == token,
                    ToolAutomationJob.status == 'running').with_for_update())).scalar_one_or_none()
                if job is None:
                    return True
                result = await _execute_claim(session, job)
                job.result_json = result
                outcome = result.get('status')
                job.status = outcome if outcome in {'succeeded', 'awaiting_approval'} else 'failed'
                job.error = str(result.get('error') or outcome)[:500] if job.status == 'failed' else ''
                job.finished_at = '' if job.status == 'awaiting_approval' else datetime.now(UTC).isoformat()
                job.lease_until = None
    except Exception as exc:
        # Only the current claimant can finalize. The archive transaction rolled back.
        async with SessionFactory() as session:
            async with session.begin():
                await session.execute(update(ToolAutomationJob).where(
                    ToolAutomationJob.id == job_id, ToolAutomationJob.claim_token == token,
                    ToolAutomationJob.status == 'running').values(status='failed', error=str(exc)[:500],
                        finished_at=datetime.now(UTC).isoformat(), lease_until=None))
    return True
