"""Explicit acceptance backfill API, authorization, and scheduler integration."""
import asyncio
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from flowhub_api.core.config import get_settings
from flowhub_api.models import (
    DocItem, ExpertDeployment, ExpertRun, ExpertVersion,
    LlmProvider, LlmProviderModel, Project, TaskItem, TemplateCanvas, User, WorkItem,
)
from flowhub_api.models.expert import ExpertJob
from flowhub_api.models.tool import ToolAutomationJob, ToolInvocation
from flowhub_api.services import document_archive as archive
from flowhub_api.services import expert_scheduler as scheduler
from flowhub_api.services import tool_automation as automation
from flowhub_api.services.crypto import encrypt_secret
from flowhub_api.services.runtime_quality import content_hash
from test_tool_archive_integration import ObjectStore, seed


@pytest.fixture
def backfill_case(client, monkeypatch):
    """Persist real workflow/deployment configuration; pause only background polls."""
    store = ObjectStore()
    real_worker = automation.process_one_automation_job
    monkeypatch.setattr(archive, 'get_minio', lambda: store)
    monkeypatch.setattr(automation, 'process_one_automation_job', AsyncMock(return_value=False))
    monkeypatch.setattr(scheduler, 'claim_next', AsyncMock(return_value=None))
    engine = create_async_engine(get_settings().sqlalchemy_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(automation, 'SessionFactory', factory)

    async def setup():
        ids, user_id = await seed(factory, store)
        suffix = uuid4().hex[:10]
        project_name = 'Backfill ' + suffix
        ids['deployment'], ids['project'] = 'dep-' + suffix, 'prj-' + suffix
        async with factory() as session:
            session.add(Project(id=ids['project'], name=project_name, code=ids['project'], status='active'))
            provider = LlmProvider(id='provider-' + suffix, name='Backfill provider ' + suffix,
                base_url='https://example.invalid/v1', api_key=encrypt_secret('test-only-placeholder'),
                credential_configured=True, status='healthy')
            session.add(provider)
            await session.flush()
            model = LlmProviderModel(id='model-' + suffix, provider_id=provider.id, model='test-model', enabled=True)
            session.add(model)
            await session.flush()
            version = await session.get(ExpertVersion, ids['version'])
            version.system_prompt, version.provider_model_id = 'Generate a draft for human review.', model.id
            session.add(ExpertDeployment(id=ids['deployment'], expert_id=ids['expert'], expert_version_id=ids['version'],
                name='Backfill deployment ' + suffix, environment='production', status='active', created_by=user_id))
            wi = await session.get(WorkItem, ids['wi'])
            wi.project = project_name
            for task in (await session.execute(select(TaskItem).where(TaskItem.wi_id == ids['wi']))).scalars():
                task.project = project_name
            for doc in (await session.execute(select(DocItem).where(DocItem.wi == ids['wi']))).scalars():
                doc.project = project_name
            canvas = await session.get(TemplateCanvas, f"{ids['tpl']}:v1")
            canvas.nodes = [{**node, 'cfg': {**node.get('cfg', {}),
                **({'handler': 'Expert 协助', 'expert': {'expertDeploymentId': ids['deployment']}}
                   if node['id'] == 'accept' else {})}} for node in canvas.nodes]
            await session.commit()
        return ids

    ids = asyncio.run(setup())
    yield {'ids': ids, 'factory': factory, 'store': store, 'worker': real_worker}
    asyncio.run(engine.dispose())


def test_duplicate_backfill_creates_one_run_and_jobs_and_never_submits(client, org_headers, backfill_case):
    case, ids = backfill_case, backfill_case['ids']
    url = f"/api/v1/tasks/{ids['task']}/automation/backfill"
    first = client.post(url, headers=org_headers, json={'generate_summary': True})
    assert first.status_code == 200, first.text
    second = client.post(url, headers=org_headers, json={'generate_summary': True})
    assert second.status_code == 200, second.text
    one, two = first.json()['data'], second.json()['data']
    assert one['run']['id'] == two['run']['id']
    assert [job['id'] for job in one['jobs']] == [job['id'] for job in two['jobs']]
    assert len(one['jobs']) == 1

    async def complete_and_check():
        async with case['factory']() as session:
            runs = (await session.execute(select(ExpertRun).where(ExpertRun.task_id == ids['task']))).scalars().all()
            backfills = [run for run in runs if (run.config_snapshot or {}).get('automationBackfillKey')]
            assert len(backfills) == 1
            run = backfills[0]
            assert (await session.get(User, run.requested_by)).account == 'liting'
            assert run.expert_version_id == ids['version']
            expert_jobs = (await session.execute(select(ExpertJob).where(ExpertJob.run_id == run.id))).scalars().all()
            assert len(expert_jobs) == 1 and expert_jobs[0].completion == {'automatic': False}
            task = await session.get(TaskItem, ids['task'])
            assert task.status == 'assigned' and task.form_values == {} and task.expert_pending
            # Drive the real scheduler's completion hook after deterministic schema validation.
            run.status, run.output = 'succeeded', 'validated backfill output'
            run.parsed = {'values': {'fmu4u8pua': '# Backfilled AI draft'}}
            run.quality_result = {'status': 'passed', 'contentHash': content_hash(run.output)}
            await scheduler.complete_workflow(session, run, expert_jobs[0].completion)
            expert_jobs[0].status = 'completed'
            await session.commit()
        assert await case['worker']()
        assert await case['worker']()
        async with case['factory']() as session:
            jobs = (await session.execute(select(ToolAutomationJob).where(ToolAutomationJob.task_id == ids['task']))).scalars().all()
            assert len(jobs) == 2 and all(job.status == 'succeeded' for job in jobs)
            docs = (await session.execute(select(DocItem).where(DocItem.wi == ids['wi'], DocItem.kind == archive.ARCHIVE_KIND))).scalars().all()
            assert len(docs) == 2
            summary = next(doc for doc in docs if doc.provenance.get('origin') == 'AI生成')
            assert summary.provenance['reviewStatus'] == 'pending_human_review'
            assert case['store'].objects[summary.object_name] == b'# Backfilled AI draft'
            task, wi = await session.get(TaskItem, ids['task']), await session.get(WorkItem, ids['wi'])
            assert task.status == 'assigned' and task.form_values == {} and not task.expert_pending
            assert wi.status == 'in_progress'
    asyncio.run(complete_and_check())


def test_backfill_rejects_unrelated_actor_and_missing_pinned_grant_without_writes(client, org_headers, dev_headers, backfill_case):
    case, ids = backfill_case, backfill_case['ids']
    url = f"/api/v1/tasks/{ids['task']}/automation/backfill"
    denied = client.post(url, headers=dev_headers, json={'generate_summary': False})
    assert denied.status_code == 403, denied.text

    async def revoke_grant():
        async with case['factory']() as session:
            version = await session.get(ExpertVersion, ids['version'])
            version.tool_bindings = []
            await session.commit()
    asyncio.run(revoke_grant())
    denied = client.post(url, headers=org_headers, json={'generate_summary': True})
    assert denied.status_code in {400, 403}, denied.text

    async def check_no_effects():
        async with case['factory']() as session:
            jobs = (await session.execute(select(ToolAutomationJob).where(ToolAutomationJob.task_id == ids['task']))).scalars().all()
            runs = (await session.execute(select(ExpertRun).where(ExpertRun.task_id == ids['task']))).scalars().all()
            assert jobs == [] and all(not (run.config_snapshot or {}).get('automationBackfillKey') for run in runs)
            task = await session.get(TaskItem, ids['task'])
            assert task.status == 'assigned' and task.form_values == {} and not task.expert_pending
    asyncio.run(check_no_effects())


def test_no_run_backfill_approval_requeues_same_original_job_and_archives_once(client, org_headers, backfill_case):
    case, ids = backfill_case, backfill_case['ids']
    async def require_approval():
        async with case['factory']() as session:
            version = await session.get(ExpertVersion, ids['version'])
            version.tool_bindings = [{**binding, 'approval': 'required'} for binding in version.tool_bindings]
            await session.commit()
    asyncio.run(require_approval())
    response = client.post(f"/api/v1/tasks/{ids['task']}/automation/backfill", headers=org_headers, json={'generate_summary': False})
    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert data['run'] is None and len(data['jobs']) == 1
    job_id = data['jobs'][0]['id']

    async def wait_for_approval():
        assert await case['worker']()
        async with case['factory']() as session:
            job = await session.get(ToolAutomationJob, job_id)
            assert job.status == 'awaiting_approval'
            invocation = await session.get(ToolInvocation, job.result_json['invocationId'])
            assert invocation.run_id is None and job.run_id is None
            return invocation.id, job.requested_by, job.payload_json
    invocation_id, original_actor, original_payload = asyncio.run(wait_for_approval())
    approved = client.post(f'/api/v1/tool-invocations/{invocation_id}/decision', headers=org_headers, json={'decision': 'approved'})
    assert approved.status_code == 200, approved.text

    async def finish_approval():
        async with case['factory']() as session:
            job = await session.get(ToolAutomationJob, job_id)
            assert job.status == 'queued' and job.requested_by == original_actor and job.payload_json == original_payload
        assert await case['worker']()
        async with case['factory']() as session:
            job = await session.get(ToolAutomationJob, job_id)
            assert job.status == 'succeeded' and job.attempts == 2
            invocation = await session.get(ToolInvocation, invocation_id)
            assert invocation.status == 'succeeded' and invocation.requested_by == original_actor
            docs = (await session.execute(select(DocItem).where(DocItem.wi == ids['wi'], DocItem.kind == archive.ARCHIVE_KIND))).scalars().all()
            assert len(docs) == 1 and docs[0].provenance['sourceDocumentId'] == ids['doc']
            assert (await session.get(TaskItem, ids['task'])).status == 'assigned'
    asyncio.run(finish_approval())
