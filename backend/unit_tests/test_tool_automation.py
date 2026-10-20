from types import SimpleNamespace as N
from unittest.mock import AsyncMock

import pytest

from flowhub_api.services import tool_automation as automation


def test_event_keys_freeze_rule_task_generation_and_payload():
    task = N(id='task')
    run = N(id='run', execution_generation=2)
    rule = {'id': 'archive', 'toolVersionId': 'v1'}
    first = automation.event_key(task, 'node_entered', rule, run, {'sources': [{'id': 'a', 'sha256': 'x'}]}, None)
    assert first == automation.event_key(task, 'node_entered', rule, run, {'sources': [{'sha256': 'x', 'id': 'a'}]}, None)
    assert first != automation.event_key(task, 'node_entered', rule, run, {'sources': [{'id': 'a', 'sha256': 'y'}]}, None)
    run.execution_generation = 3
    assert first != automation.event_key(task, 'node_entered', rule, run, {'sources': [{'id': 'a', 'sha256': 'x'}]}, None)


@pytest.mark.asyncio
async def test_rule_rejects_ungranted_tool_version():
    session = N(get=AsyncMock(return_value=N(tool_bindings=[])))
    with pytest.raises(ValueError, match='grant'):
        await automation.validate_automation_rule(session, {'toolVersionId': 'tool'}, 'expert')


@pytest.mark.asyncio
async def test_schedule_ignores_disabled_rules_before_any_snapshot(monkeypatch):
    canvas = N(nodes=[{'id': 'accept', 'cfg': {'automation': {'rules': [{'enabled': False}]}}}])
    monkeypatch.setattr(automation, 'frozen_canvas', AsyncMock(return_value=canvas))
    snapshot = AsyncMock(side_effect=AssertionError('must not read objects'))
    monkeypatch.setattr(automation, 'snapshot_sources', snapshot)
    assert await automation.schedule_automation(N(), task=N(node_id='accept'), event='node_entered', actor=N(), expert_version_id='expert') == []


@pytest.mark.asyncio
async def test_frozen_source_change_is_not_silently_archived(monkeypatch):
    monkeypatch.setattr(automation, 'snapshot_sources', AsyncMock(return_value=[{'id': 'a', 'sha256': 'new'}]))
    with pytest.raises(ValueError, match='changed'):
        await automation.verify_frozen_sources(N(), N(), [{'id': 'a', 'sha256': 'old'}])


class MemorySession:
    def __init__(self, objects=None, result=None):
        self.objects = objects or {}
        self.result = result
        self.calls = []
    async def get(self, model, key, **kwargs):
        return self.objects.get((model.__name__, key))
    def add(self, obj):
        self.objects[(type(obj).__name__, obj.id)] = obj
    async def flush(self):
        pass
    async def execute(self, statement):
        self.calls.append(statement)
        return N(scalar_one_or_none=lambda: self.result)
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        return False
    def begin(self):
        return self


@pytest.fixture
def scheduling(monkeypatch):
    from flowhub_api.models.tool import ToolDefinition, ToolVersion
    rule = {'id': 'archive', 'enabled': True, 'event': 'node_entered', 'toolVersionId': 'tv'}
    canvas = N(nodes=[{'id': 'accept', 'cfg': {'automation': {'rules': [rule]}}}])
    task = N(id='task', node_id='accept')
    actor = N(id='actor')
    version = N(tool_bindings=[{'tool_version_id': 'tv', 'scope': 'current_work_item'}])
    tool_version = N(tool_id='tool', status='published', config_json={'adapter': 'flowhub_document_archive'})
    tool = N(status='active', source='native')
    session = MemorySession({('ExpertVersion', 'ev'): version, ('ToolVersion', 'tv'): tool_version,
                             ('ToolDefinition', 'tool'): tool})
    monkeypatch.setattr(automation, 'frozen_canvas', AsyncMock(return_value=canvas))
    monkeypatch.setattr(automation, 'authorize_archive', AsyncMock())
    monkeypatch.setattr(automation, 'snapshot_sources', AsyncMock(return_value=[{'id': 'doc', 'sha256': 'hash'}]))
    return session, task, actor, rule, tool


@pytest.mark.asyncio
async def test_scheduling_persists_frozen_payload_and_is_idempotent(scheduling):
    session, task, actor, rule, tool = scheduling
    first = await automation.schedule_automation(session, task=task, event='node_entered', actor=actor, expert_version_id='ev')
    second = await automation.schedule_automation(session, task=task, event='node_entered', actor=actor, expert_version_id='ev')
    assert first[0] is second[0]
    assert first[0].status == 'queued'
    assert first[0].payload_json == {'sources': [{'id': 'doc', 'sha256': 'hash'}]}
    first[0].status = 'failed'
    await automation.schedule_automation(session, task=task, event='node_entered', actor=actor, expert_version_id='ev')
    assert first[0].status == 'queued'


@pytest.mark.asyncio
async def test_scheduling_records_storage_failure_as_durable_failed_job(scheduling, monkeypatch):
    session, task, actor, rule, tool = scheduling
    monkeypatch.setattr(automation, 'snapshot_sources', AsyncMock(side_effect=ValueError('storage read failed')))
    jobs = await automation.schedule_automation(session, task=task, event='node_entered', actor=actor, expert_version_id='ev')
    assert jobs[0].status == 'failed'
    assert jobs[0].error == 'storage read failed'
    assert jobs[0].payload_json['freeze_error'] == 'storage read failed'


@pytest.mark.asyncio
async def test_disabled_or_non_native_rule_is_rejected(scheduling):
    session, task, actor, rule, tool = scheduling
    tool.status = 'disabled'
    with pytest.raises(ValueError, match='native archive'):
        await automation.schedule_automation(session, task=task, event='node_entered', actor=actor, expert_version_id='ev')
    tool.status, tool.source = 'active', 'mcp'
    with pytest.raises(ValueError, match='native archive'):
        await automation.validate_automation_rule(session, rule, 'ev')
    with pytest.raises(ValueError, match='Unsupported'):
        await automation.schedule_automation(session, task=task, event='submit', actor=actor, expert_version_id='ev')


@pytest.mark.asyncio
async def test_generated_summary_is_scheduled_before_adoption(scheduling, monkeypatch):
    session, task, actor, rule, tool = scheduling
    rule['event'] = 'expert_output_validated'
    run = N(id='run', execution_generation=2)
    monkeypatch.setattr(automation, 'summary_for_run', AsyncMock(return_value='summary'))
    monkeypatch.setattr(automation, 'validated_summary', AsyncMock())
    jobs = await automation.schedule_automation(session, task=task, event='expert_output_validated', actor=actor,
                                               expert_version_id='ev', run=run)
    assert jobs[0].payload_json['summary_content'] == 'summary'
    assert jobs[0].run_id == 'run' and jobs[0].generation == 2
    monkeypatch.setattr(automation, 'summary_for_run', AsyncMock(return_value=None))
    missing = await automation.schedule_automation(session, task=task, event='expert_output_validated', actor=actor,
                                                   expert_version_id='ev', run=run)
    assert missing[0].status == 'failed'
    assert 'no summary document body' in missing[0].payload_json['freeze_error']


@pytest.mark.asyncio
async def test_execute_claim_rechecks_authority_and_uses_same_executor(scheduling, monkeypatch):
    from flowhub_api.services import tool_execution
    session, task, actor, rule, tool = scheduling
    task.wi_id = 'wi'
    session.objects[('TaskItem', 'task')] = task
    session.objects[('User', 'actor')] = actor
    run = N(id='run', execution_generation=2, expert_version_id='ev', requested_by='actor')
    session.objects[('ExpertRun', 'run')] = run
    job = N(task_id='task', requested_by='actor', run_id='run', generation=2, expert_version_id='ev',
            rule_json=rule, event_key='key', payload_json={'sources': [{'id': 'doc', 'sha256': 'hash'}], 'summary_content': 'summary'})
    executor = AsyncMock(return_value={'status': 'succeeded'})
    monkeypatch.setattr(tool_execution, 'execute_tool', executor)
    monkeypatch.setattr(automation, 'validated_summary', AsyncMock())
    assert await automation._execute_claim(session, job) == {'status': 'succeeded'}
    assert executor.call_args.kwargs['arguments'] == {'source_document_ids': ['doc'], 'summary_content': 'summary'}
    assert executor.call_args.kwargs['idempotency_key'] == 'automation:key'
    assert executor.call_args.kwargs['source'] == 'automation'
    run.execution_generation = 3
    with pytest.raises(ValueError, match='generation or actor'):
        await automation._execute_claim(session, job)
    job.run_id = None
    job.payload_json = {'freeze_error': 'failed freeze'}
    with pytest.raises(ValueError, match='failed freeze'):
        await automation._execute_claim(session, job)
    session.objects.pop(('TaskItem', 'task'))
    with pytest.raises(ValueError, match='missing'):
        await automation._execute_claim(session, job)


@pytest.mark.asyncio
async def test_claim_and_worker_finalize_under_current_fence(monkeypatch):
    job = N(id='job', attempts=0, status='queued', claim_token='', lease_until=None)
    session = MemorySession(result=job)
    monkeypatch.setattr(automation, 'SessionFactory', lambda: session)
    claim = await automation._claim_job()
    assert claim[0] == 'job' and len(claim[1]) == 32
    assert job.status == 'running' and job.attempts == 1 and job.lease_until is not None
    monkeypatch.setattr(automation, '_claim_job', AsyncMock(return_value=claim))
    monkeypatch.setattr(automation, '_execute_claim', AsyncMock(return_value={'status': 'succeeded', 'result': {}}))
    assert await automation.process_one_automation_job()
    assert job.status == 'succeeded' and job.lease_until is None
    monkeypatch.setattr(automation, '_execute_claim', AsyncMock(return_value={'status': 'denied', 'error': 'access revoked'}))
    assert await automation.process_one_automation_job()
    assert job.status == 'failed' and job.error == 'access revoked'
    monkeypatch.setattr(automation, '_execute_claim', AsyncMock(return_value={'status': 'awaiting_approval', 'invocationId': 'approval'}))
    assert await automation.process_one_automation_job()
    assert job.status == 'awaiting_approval' and job.error == '' and job.finished_at == ''
    session.result = None
    assert await automation.process_one_automation_job()  # another worker replaced claim token
    monkeypatch.setattr(automation, '_claim_job', AsyncMock(return_value=None))
    assert not await automation.process_one_automation_job()


@pytest.mark.asyncio
async def test_worker_failure_rolls_back_archive_and_fences_failure_update(monkeypatch):
    job = N(id='job', attempts=1)
    session = MemorySession(result=job)
    monkeypatch.setattr(automation, 'SessionFactory', lambda: session)
    monkeypatch.setattr(automation, '_claim_job', AsyncMock(return_value=('job', 'token')))
    monkeypatch.setattr(automation, '_execute_claim', AsyncMock(side_effect=ValueError('source deleted')))
    assert await automation.process_one_automation_job()
    # Failure update includes the claim token instead of overwriting a newer lease.
    assert len(session.calls) == 2
    assert 'claim_token' in str(session.calls[-1])
