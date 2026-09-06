"""Isolated scheduler tests: no application database connection or schema reset."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from flowhub_api.services import expert_scheduler as scheduler


class Result:
    def __init__(self, value): self.value = value
    def scalar_one_or_none(self): return self.value


class Sessions:
    def __init__(self, session): self.session = session
    @asynccontextmanager
    async def begin(self): yield self.session
    @asynccontextmanager
    async def __call__(self): yield self.session


@pytest.mark.asyncio
async def test_claim_uses_skip_locked_and_rotates_fence(monkeypatch):
    job = SimpleNamespace(id='job', run_id='run', generation=2, attempts=1, claim_token='old')
    session = SimpleNamespace(execute=AsyncMock(return_value=Result(job)))
    monkeypatch.setattr(scheduler, 'SessionFactory', Sessions(session))
    claim = await scheduler.claim_next()
    sql = str(session.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert 'FOR UPDATE SKIP LOCKED' in sql
    assert 'expert_jobs.lease_until < now()' in sql
    assert claim.token != 'old'
    assert claim.generation == 2 and claim.attempts == 2
    assert job.status == 'running' and job.lease_until is not None


@pytest.mark.asyncio
async def test_empty_queue_does_not_create_claim(monkeypatch):
    monkeypatch.setattr(scheduler, 'SessionFactory', Sessions(SimpleNamespace(execute=AsyncMock(return_value=Result(None)))))
    assert await scheduler.claim_next() is None


@pytest.mark.asyncio
async def test_stale_lease_rejected(monkeypatch):
    session = SimpleNamespace(execute=AsyncMock(return_value=Result(None)))
    monkeypatch.setattr(scheduler, 'SessionFactory', Sessions(session))
    with pytest.raises(scheduler.LeaseLost):
        await scheduler.check_lease(scheduler.Claim('job', 'run', 2, 'old', 1))
    sql = str(session.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert 'expert_jobs.claim_token =' in sql
    assert 'expert_jobs.lease_until > now()' in sql


@pytest.mark.asyncio
async def test_current_lease_accepted(monkeypatch):
    monkeypatch.setattr(scheduler, 'SessionFactory', Sessions(SimpleNamespace(execute=AsyncMock(return_value=Result('job')))))
    await scheduler.check_lease(scheduler.Claim('job', 'run', 1, 'token', 1))


@pytest.mark.asyncio
async def test_blocked_automatic_result_returns_task_to_human(monkeypatch):
    task = SimpleNamespace(status='pending_confirmation', expert_pending=True)
    session = SimpleNamespace(execute=AsyncMock(return_value=Result(task)), get=AsyncMock(return_value=task))
    run = SimpleNamespace(task_id='task', status='failed')
    await scheduler.complete_workflow(session, run, {'automatic': True, 'auto_depth': 0})
    assert task.status == 'assigned'
    assert not task.expert_pending


@pytest.mark.asyncio
async def test_interrupted_task_remains_pending():
    session = SimpleNamespace(execute=AsyncMock())
    await scheduler.complete_workflow(session, SimpleNamespace(task_id='task', status='interrupted'), {'automatic': True})
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_completed_task_is_never_advanced_again():
    task = SimpleNamespace(status='completed', expert_pending=True)
    session = SimpleNamespace(execute=AsyncMock(return_value=Result(task)), get=AsyncMock())
    await scheduler.complete_workflow(session, SimpleNamespace(task_id='task', status='succeeded'), {'automatic': True})
    session.get.assert_not_called()
    assert task.status == 'completed'


@pytest.mark.asyncio
async def test_cleanup_deletes_only_committed_outbox_rows(monkeypatch):
    row = SimpleNamespace(object_name='old/document.md')
    session = SimpleNamespace(execute=AsyncMock(return_value=Result(row)), delete=AsyncMock())
    monkeypatch.setattr(scheduler, 'SessionFactory', Sessions(session))
    calls = []
    monkeypatch.setattr('flowhub_api.clients.minio.get_minio', lambda: SimpleNamespace(remove_object=lambda *args: calls.append(args)))
    monkeypatch.setattr('flowhub_api.core.config.get_settings', lambda: SimpleNamespace(minio_bucket='docs'))
    await scheduler.cleanup_one_object()
    assert calls == [('docs', 'old/document.md')]
    session.delete.assert_awaited_once_with(row)


@pytest.mark.asyncio
async def test_failed_object_deletion_keeps_outbox_for_retry(monkeypatch):
    session = SimpleNamespace(execute=AsyncMock(return_value=Result(SimpleNamespace(object_name='old'))), delete=AsyncMock())
    monkeypatch.setattr(scheduler, 'SessionFactory', Sessions(session))
    def failure(*args): raise OSError('unavailable')
    monkeypatch.setattr('flowhub_api.clients.minio.get_minio', lambda: SimpleNamespace(remove_object=failure))
    monkeypatch.setattr('flowhub_api.core.config.get_settings', lambda: SimpleNamespace(minio_bucket='docs'))
    with pytest.raises(OSError): await scheduler.cleanup_one_object()
    session.delete.assert_not_called()
